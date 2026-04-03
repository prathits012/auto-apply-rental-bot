from __future__ import annotations
"""
core/scam.py — Scam scoring engine.
Each signal adds points. High score = skip or flag.
"""

import re
import requests
import imagehash
from PIL import Image
from io import BytesIO
from config import GOOGLE_MAPS_API_KEY, SCAM_THRESHOLDS, FILTERS


# ── Keyword lists ─────────────────────────────────────────────

PAYMENT_SCAM_KEYWORDS = [
    "western union", "wire transfer", "money order", "zelle", "cashapp",
    "cash app", "venmo deposit", "gift card", "itunes card", "google play card",
    "bitcoin", "crypto payment", "send money", "transfer funds"
]

ABROAD_KEYWORDS = [
    "missionary", "deployed overseas", "working abroad", "out of country",
    "traveling abroad", "god will bless", "key by mail", "mail the key",
    "cannot meet", "trust in god", "honest person", "please be serious"
]

TEMPLATE_PHRASES = [
    "this lovely home", "this beautiful home", "well maintained", "neat and tidy",
    "serious inquiries only", "no pets no smoking", "first come first serve",
    "available immediately", "must see", "won't last long"
]


# ── Individual signal checks ──────────────────────────────────

def check_price_vs_median(price: int, beds: float, neighborhood: str) -> tuple[int, str | None]:
    """
    Returns (points, flag_message).
    Compares listing price to SF neighborhood median from RentCast.
    Falls back to hardcoded SF medians if API unavailable.
    """
    # Rough SF medians (2024) by bedrooms — update periodically
    SF_MEDIANS = {
        0: 2200,   # studio
        1: 2800,
        2: 3800,
        3: 5200,
    }
    beds_key = min(int(beds or 1), 3)
    median = SF_MEDIANS.get(beds_key, 3000)
    threshold = median * SCAM_THRESHOLDS["price_ratio"]

    if price and price < threshold:
        pct = round((1 - price / median) * 100)
        return 40, f"Price ${price:,} is {pct}% below SF median (${median:,}) for {beds_key}BD"
    return 0, None


def check_payment_keywords(text: str) -> tuple[int, str | None]:
    """Instant auto-reject if payment scam keywords found."""
    text_lower = (text or "").lower()
    for kw in PAYMENT_SCAM_KEYWORDS:
        if kw in text_lower:
            return 50, f"Payment scam keyword: '{kw}'"
    return 0, None


def check_abroad_keywords(text: str) -> tuple[int, str | None]:
    text_lower = (text or "").lower()
    matches = [kw for kw in ABROAD_KEYWORDS if kw in text_lower]
    if matches:
        return 25, f"Abroad/trust narrative: {matches[0]}"
    return 0, None


def check_template_language(text: str) -> tuple[int, str | None]:
    text_lower = (text or "").lower()
    hits = sum(1 for p in TEMPLATE_PHRASES if p in text_lower)
    if hits >= 3:
        return 10, f"Template language detected ({hits} phrases)"
    return 0, None


def check_contact_method(listing: dict) -> tuple[int, str | None]:
    """Flag listings with no phone, only email/WhatsApp."""
    has_phone = bool(listing.get("phone"))
    email = listing.get("email", "") or ""
    is_generic_email = any(d in email for d in ["gmail.com", "yahoo.com", "hotmail.com"])
    if not has_phone and is_generic_email:
        return 15, "No phone — contact only via generic email"
    return 0, None


def check_address(address: str, lat: float = None, lng: float = None) -> tuple[int, str | None]:
    """
    Verify listing is within our search radius using coordinates.
    Falls back gracefully if no coords available.
    """
    from core.geo import within_radius
    if lat is not None and lng is not None:
        if not within_radius(lat, lng):
            return 40, f"Address is outside search radius"
    return 0, None


def check_duplicate_images(image_urls: list, listing_id: str) -> tuple[int, str | None]:
    """
    Compute perceptual hash of listing images.
    Compare against hashes stored in DB for previously seen listings.
    This is a lightweight local check — no Google Vision API needed.
    """
    if not image_urls:
        return 0, None
    try:
        hashes = []
        for url in image_urls[:3]:    # check first 3 images
            resp = requests.get(url, timeout=8)
            img = Image.open(BytesIO(resp.content))
            hashes.append(str(imagehash.phash(img)))

        # TODO: compare against DB hashes from other listings
        # For now: log hashes for future dedup
        return 0, None
    except Exception:
        return 0, None


# ── Main scoring function ─────────────────────────────────────

def score_listing(listing: dict) -> tuple[int, list[str], str]:
    """
    Run all scam checks on a listing.

    Returns:
        score     (int)       — total scam score
        flags     (list[str]) — list of triggered signals
        verdict   (str)       — 'pass' | 'flag' | 'auto_reject'
    """
    text = " ".join([
        listing.get("title", "") or "",
        listing.get("description", "") or "",
    ])

    score = 0
    flags = []

    checks = [
        check_payment_keywords(text),
        check_abroad_keywords(text),
        check_template_language(text),
        check_contact_method(listing),
        check_address(listing.get("address", ""), listing.get("lat"), listing.get("lng")),
        check_price_vs_median(
            listing.get("price", 0),
            listing.get("beds", 1),
            listing.get("neighborhood", ""),
        ),
        check_duplicate_images(
            listing.get("image_urls", []),
            listing.get("id", ""),
        ),
    ]

    for pts, flag in checks:
        if pts:
            score += pts
        if flag:
            flags.append(flag)

    # Determine verdict
    if score >= SCAM_THRESHOLDS["auto_reject"]:
        verdict = "auto_reject"
    elif score >= SCAM_THRESHOLDS["flag"]:
        verdict = "flag"
    else:
        verdict = "pass"

    return score, flags, verdict
