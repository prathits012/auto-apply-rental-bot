"""
scrapers/email_reader.py — Shared Gmail IMAP reader.

Connects to the rental-bot Gmail inbox, searches for unread emails
from a given sender, marks them as Seen immediately, and returns
the parsed email.Message objects for the caller to extract listings from.
"""
from __future__ import annotations

import imaplib
import email
from email.header import decode_header as _decode_header
from config import EMAIL_FROM, EMAIL_PASSWORD

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


def fetch_unread(sender_address: str) -> list[tuple[str, email.message.Message]]:
    """
    Fetch all UNSEEN emails from sender_address in the INBOX.
    Marks each as \\Seen immediately (before returning) to prevent
    double-processing if the caller crashes mid-parse.

    Returns list of (imap_uid, email.Message) tuples.
    """
    results = []
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as conn:
            conn.login(EMAIL_FROM, EMAIL_PASSWORD)
            conn.select("INBOX")

            status, data = conn.search(
                None, "UNSEEN", f'FROM "{sender_address}"'
            )
            if status != "OK" or not data[0]:
                return []

            uid_list = data[0].split()
            for uid in uid_list:
                uid_str = uid.decode()
                # Mark as Seen BEFORE parsing — crash-safe
                conn.store(uid, "+FLAGS", "\\Seen")
                _, msg_data = conn.fetch(uid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                results.append((uid_str, msg))

    except Exception as e:
        print(f"  [email_reader] IMAP error for {sender_address}: {e}")

    return results


def get_html_body(msg: email.message.Message) -> str | None:
    """Extract the text/html part from a possibly multipart email."""
    if msg.is_multipart():
        for part in msg.walk():
            if (part.get_content_type() == "text/html"
                    and "attachment" not in str(part.get("Content-Disposition", ""))):
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
    elif msg.get_content_type() == "text/html":
        charset = msg.get_content_charset() or "utf-8"
        return msg.get_payload(decode=True).decode(charset, errors="replace")
    return None


def get_email_date(msg: email.message.Message) -> str | None:
    """Return ISO timestamp from the email's Date header, or None."""
    from email.utils import parsedate_to_datetime
    date_str = msg.get("Date")
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str).isoformat()
    except Exception:
        return None
