"""
notifications/email.py — Email alerts with clickable Apply/Skip links.
Replaces Twilio SMS. Uses Gmail SMTP (smtplib, no extra deps).

Setup:
  1. Enable 2FA on your Google account
  2. Generate an App Password at https://myaccount.google.com/apppasswords
  3. Set EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO in config.py
"""

import smtplib
import threading
import secrets
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, redirect
from config import (
    EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO,
    WEBHOOK_HOST, WEBHOOK_PORT, SMS_CONFIRM_TIMEOUT_MINUTES,
)
from core.db import update_listing_status

app = Flask(__name__)

# { token: { "listing_id": ..., "on_confirm": fn, "on_skip": fn, "listing": dict } }
_pending: dict = {}


# ── Formatting ─────────────────────────────────────────────────

def _html_alert(listing: dict, scam_score: int, scam_flags: list,
                apply_url: str, skip_url: str) -> str:
    beds  = listing.get("beds")
    baths = listing.get("baths")
    price = listing.get("price")
    sqft  = listing.get("sqft")
    src   = listing.get("source", "").capitalize()
    url   = listing.get("url", "")
    addr  = listing.get("address", "N/A")
    desc  = (listing.get("description") or "")[:400]
    neighborhood = listing.get("neighborhood", "")

    bed_str   = f"{int(beds)}BD" if beds is not None else "?"
    bath_str  = f"/{int(baths)}BA" if baths is not None else ""
    price_str = f"${price:,}/mo" if price else "Price unknown"
    sqft_str  = f"{sqft:,} sqft" if sqft else ""

    MAX_SCAM_SCORE = 225
    scam_pct = min(round(scam_score / MAX_SCAM_SCORE * 100), 100)
    scam_html = ""
    if scam_score >= 20:
        flags_str = " · ".join(scam_flags[:3]) if scam_flags else "Multiple signals"
        scam_html = f"""
        <div style="background:#fff3cd;border:1px solid #ffc107;padding:10px;border-radius:4px;margin:12px 0;">
            ⚠️ <strong>Scam score {scam_pct}/100</strong> — {flags_str}
        </div>"""

    complex_html = ""
    if listing.get("complex_id"):
        complex_html = f'<p>📍 <strong>Complex match:</strong> {listing["complex_id"]}</p>'

    cover_html = ""
    if listing.get("cover_letter"):
        cover_html = f"""
        <details style="margin-top:16px;">
            <summary style="cursor:pointer;color:#555;">Draft cover letter</summary>
            <pre style="white-space:pre-wrap;font-size:13px;background:#f9f9f9;padding:12px;border-radius:4px;">{listing["cover_letter"]}</pre>
        </details>"""

    return f"""
    <html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:20px;color:#222;">
        <h2 style="margin-bottom:4px;">🏠 {bed_str}{bath_str} &nbsp;·&nbsp; {price_str}</h2>
        <p style="color:#666;margin-top:0;">{src}{(" · " + neighborhood) if neighborhood else ""}</p>

        {scam_html}

        <table style="width:100%;border-collapse:collapse;margin:12px 0;">
            <tr><td style="padding:4px 0;color:#555;">Address</td><td><strong>{addr}</strong></td></tr>
            {"<tr><td style='padding:4px 0;color:#555;'>Size</td><td><strong>" + sqft_str + "</strong></td></tr>" if sqft_str else ""}
            <tr><td style="padding:4px 0;color:#555;">Source</td><td>{src}</td></tr>
        </table>

        {complex_html}

        {"<p style='color:#555;font-size:14px;'>" + desc + ("…" if len(listing.get("description","")) > 400 else "") + "</p>" if desc else ""}

        <div style="margin:20px 0;">
            <a href="{apply_url}" style="background:#1a73e8;color:white;padding:10px 24px;border-radius:4px;text-decoration:none;margin-right:12px;font-weight:bold;">
                ✅ Apply
            </a>
            <a href="{skip_url}" style="background:#e8eaed;color:#444;padding:10px 24px;border-radius:4px;text-decoration:none;font-weight:bold;">
                ❌ Skip
            </a>
            {"&nbsp;&nbsp;<a href='" + url + "' style='color:#1a73e8;'>View listing →</a>" if url else ""}
        </div>

        {cover_html}

        <hr style="border:none;border-top:1px solid #eee;margin-top:24px;">
        <p style="font-size:11px;color:#aaa;">SF Rental Bot · listing id: {listing.get("id","")}</p>
    </body></html>
    """


def _text_alert(listing: dict, scam_score: int, scam_flags: list) -> str:
    beds  = listing.get("beds")
    baths = listing.get("baths")
    price = listing.get("price")
    bed_str   = f"{int(beds)}BD" if beds is not None else "?"
    bath_str  = f"/{int(baths)}BA" if baths is not None else ""
    price_str = f"${price:,}/mo" if price else "Price unknown"
    lines = [
        f"{bed_str}{bath_str} · {price_str}",
        f"Address: {listing.get('address', 'N/A')}",
        f"Source: {listing.get('source', '')}",
        f"URL: {listing.get('url', '')}",
    ]
    if scam_score >= 20:
        scam_pct = min(round(scam_score / 225 * 100), 100)
        lines.append(f"WARNING: Scam score {scam_pct}/100")
    return "\n".join(lines)


# ── Send ───────────────────────────────────────────────────────

def send_alert(listing: dict, scam_score: int = 0, scam_flags: list = None,
               on_confirm=None, on_skip=None):
    """Register a listing's callbacks. Call send_digest() to actually send the email."""
    token = secrets.token_urlsafe(16)
    listing_id = listing["id"]

    base = f"http://localhost:{WEBHOOK_PORT}"
    _pending[token] = {
        "listing_id": listing_id,
        "on_confirm": on_confirm,
        "on_skip":    on_skip,
        "listing":    listing,
        "scam_score": scam_score,
        "scam_flags": scam_flags or [],
        "apply_url":  f"{base}/confirm/{token}",
        "skip_url":   f"{base}/skip/{token}",
    }

    # Auto-timeout
    if SMS_CONFIRM_TIMEOUT_MINUTES:
        t = threading.Timer(
            SMS_CONFIRM_TIMEOUT_MINUTES * 60,
            _handle_timeout, args=[token]
        )
        t.daemon = True
        t.start()

    return token


def send_digest():
    """
    Send one digest email containing all pending listings.
    Call this once at the end of each pipeline run.
    """
    if not _pending:
        print("[email] No new listings to send.")
        return

    n = len(_pending)
    print(f"[email] Sending digest with {n} listing{'s' if n != 1 else ''}...")

    # Build HTML rows for each listing
    rows_html = []
    rows_text = []
    for token, entry in _pending.items():
        listing    = entry["listing"]
        scam_score = entry["scam_score"]
        scam_flags = entry["scam_flags"]
        apply_url  = entry["apply_url"]
        skip_url   = entry["skip_url"]
        rows_html.append(_html_alert(listing, scam_score, scam_flags, apply_url, skip_url))
        rows_text.append(_text_alert(listing, scam_score, scam_flags))

    # Wrap in digest container
    html_body = f"""
    <html><body style="font-family:sans-serif;max-width:640px;margin:auto;padding:20px;color:#222;">
        <h1 style="font-size:20px;margin-bottom:4px;">🏠 SF Rental Digest — {n} new listing{'s' if n != 1 else ''}</h1>
        <p style="color:#888;margin-top:0;font-size:13px;">Within 0.5mi of 4th &amp; King · $2,500–$4,500 · 1–2BD</p>
        <hr style="border:none;border-top:2px solid #eee;margin:16px 0;">
        {"<hr style='border:none;border-top:1px solid #eee;margin:24px 0;'>".join(rows_html)}
    </body></html>
    """
    text_body = f"SF Rental Digest — {n} new listings\n\n" + "\n\n---\n\n".join(rows_text)

    subject = f"🏠 {n} new SF rental{'s' if n != 1 else ''} near Caltrain"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = ", ".join(r.strip() for r in EMAIL_TO.split(","))
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        recipients = [r.strip() for r in EMAIL_TO.split(",")]
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_FROM, EMAIL_PASSWORD)
            smtp.sendmail(EMAIL_FROM, recipients, msg.as_string())
        print(f"[email] Digest sent to {recipients}.")
        for entry in _pending.values():
            update_listing_status(entry["listing_id"], "alerted")
    except Exception as e:
        print(f"[email] Failed to send digest: {e}")


def _handle_timeout(token: str):
    if token in _pending:
        entry = _pending.pop(token)
        update_listing_status(entry["listing_id"], "skipped")
        if entry.get("on_skip"):
            entry["on_skip"]()


# ── Flask click-handlers ───────────────────────────────────────

@app.route("/confirm/<token>")
def confirm(token):
    entry = _pending.pop(token, None)
    if not entry:
        return "<h2>Already handled or expired.</h2>", 410
    update_listing_status(entry["listing_id"], "applied")
    if entry.get("on_confirm"):
        threading.Thread(target=entry["on_confirm"], daemon=True).start()
    title = entry["listing"].get("title", entry["listing_id"])
    return f"<h2>✅ Applying to {title[:80]}…</h2><p>Check the terminal for progress.</p>"


@app.route("/skip/<token>")
def skip(token):
    entry = _pending.pop(token, None)
    if not entry:
        return "<h2>Already handled or expired.</h2>", 410
    update_listing_status(entry["listing_id"], "skipped")
    if entry.get("on_skip"):
        entry["on_skip"]()
    return "<h2>👍 Skipped.</h2>"


def start_webhook_server():
    print(f"[email] Click-handler server starting on port {WEBHOOK_PORT}...")
    t = threading.Thread(
        target=lambda: app.run(host=WEBHOOK_HOST, port=WEBHOOK_PORT, debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()
