from __future__ import annotations
"""
notifications/sms.py — Twilio SMS send + Flask webhook receiver.
Sends you an alert for each new listing. Listens for your Y/N reply.
"""

import threading
from flask import Flask, request, Response
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from config import (
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
    TWILIO_FROM_NUMBER, YOUR_PHONE_NUMBER,
    WEBHOOK_HOST, WEBHOOK_PORT, SMS_CONFIRM_TIMEOUT_MINUTES
)
from core.db import update_listing_status, get_listing, log_sms

app = Flask(__name__)
_twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# In-memory map of pending confirmations: { listing_id: callback_fn }
_pending: dict = {}


def _format_alert(listing: dict, scam_score: int, scam_flags: list) -> str:
    """Format the SMS alert message."""
    beds  = listing.get("beds")
    baths = listing.get("baths")
    price = listing.get("price")
    src   = listing.get("source", "").capitalize()
    url   = listing.get("url", "")
    title = listing.get("title", "")

    bed_str  = f"{beds:.0f}BD" if beds is not None else "?"
    bath_str = f"/{baths:.0f}BA" if baths is not None else ""
    price_str = f"${price:,}/mo" if price else "Price unknown"

    # Complex tag
    complex_id = listing.get("complex_id")
    complex_tag = f"\n[MATCH] {complex_id}" if complex_id else ""

    # Scam warning
    scam_tag = ""
    if scam_score >= 20:
        top_flag = scam_flags[0] if scam_flags else "Multiple signals"
        scam_tag = f"\n⚠️ Scam score {scam_score} · {top_flag}"

    lines = [
        f"🏠 {bed_str}{bath_str} · {price_str}",
        f"Via: {src}",
        f"{title[:60]}" if title else "",
        complex_tag,
        scam_tag,
        url[:80] if url else "",
        "",
        "Reply Y to apply, N to skip, INFO for details, STOP to pause",
    ]
    return "\n".join(l for l in lines if l is not None).strip()


def send_alert(listing: dict, scam_score: int = 0, scam_flags: list = None,
               on_confirm=None, on_skip=None):
    """
    Send an SMS alert for a listing.
    on_confirm() is called when you reply Y.
    on_skip() is called when you reply N or timeout.
    """
    body = _format_alert(listing, scam_score, scam_flags or [])
    listing_id = listing["id"]

    print(f"[sms] Sending alert for {listing_id}")
    _twilio.messages.create(
        body=body,
        from_=TWILIO_FROM_NUMBER,
        to=YOUR_PHONE_NUMBER,
    )
    log_sms("outbound", body, listing_id)
    update_listing_status(listing_id, "alerted")

    # Register callbacks
    _pending[listing_id] = {
        "on_confirm": on_confirm,
        "on_skip":    on_skip,
        "listing":    listing,
    }

    # Auto-timeout
    if SMS_CONFIRM_TIMEOUT_MINUTES:
        t = threading.Timer(
            SMS_CONFIRM_TIMEOUT_MINUTES * 60,
            _handle_timeout, args=[listing_id]
        )
        t.daemon = True
        t.start()


def _handle_timeout(listing_id: str):
    if listing_id in _pending:
        print(f"[sms] Timeout — skipping {listing_id}")
        entry = _pending.pop(listing_id)
        update_listing_status(listing_id, "skipped")
        if entry.get("on_skip"):
            entry["on_skip"]()


def _find_pending_listing_id(from_number: str) -> str | None:
    """Find the most recently alerted listing (simple FIFO)."""
    if _pending:
        return next(iter(_pending))
    return None


# ── Flask webhook ─────────────────────────────────────────────

@app.route("/sms", methods=["POST"])
def sms_webhook():
    """Twilio calls this URL when you reply to the alert."""
    body   = request.form.get("Body", "").strip().upper()
    sender = request.form.get("From", "")
    resp   = MessagingResponse()

    # Security: only accept replies from your own number
    if sender != YOUR_PHONE_NUMBER:
        return Response(str(resp), mimetype="text/xml")

    listing_id = _find_pending_listing_id(sender)
    log_sms("inbound", body, listing_id)

    if body == "STOP":
        resp.message("⏸ Alerts paused. Reply START to resume.")
        # TODO: set a global pause flag
        return Response(str(resp), mimetype="text/xml")

    if not listing_id or listing_id not in _pending:
        resp.message("No pending listings. I'll alert you when something new comes in.")
        return Response(str(resp), mimetype="text/xml")

    entry = _pending[listing_id]
    listing = entry["listing"]

    if body == "Y":
        _pending.pop(listing_id, None)
        resp.message(f"✅ Applying to {listing.get('title', listing_id)[:50]}...")
        update_listing_status(listing_id, "applied")
        if entry.get("on_confirm"):
            # Run application in background thread
            t = threading.Thread(target=entry["on_confirm"], daemon=True)
            t.start()

    elif body == "N":
        _pending.pop(listing_id, None)
        resp.message("👍 Skipped.")
        update_listing_status(listing_id, "skipped")
        if entry.get("on_skip"):
            entry["on_skip"]()

    elif body == "INFO":
        l = get_listing(listing_id) or listing
        info = (
            f"Address: {l.get('address', 'N/A')}\n"
            f"Sqft: {l.get('sqft', 'N/A')}\n"
            f"Scam score: {l.get('scam_score', 0)}\n"
            f"Source: {l.get('source', 'N/A')}\n"
            f"{l.get('url', '')}"
        )
        resp.message(info)

    else:
        resp.message("Reply Y (apply), N (skip), INFO (details), or STOP (pause alerts).")

    return Response(str(resp), mimetype="text/xml")


def start_webhook_server():
    """Start the Flask webhook server in a background thread."""
    print(f"[sms] Webhook server starting on port {WEBHOOK_PORT}...")
    print(f"[sms] Set your Twilio webhook URL to: http://YOUR_IP:{WEBHOOK_PORT}/sms")
    t = threading.Thread(
        target=lambda: app.run(host=WEBHOOK_HOST, port=WEBHOOK_PORT, debug=False),
        daemon=True
    )
    t.start()
