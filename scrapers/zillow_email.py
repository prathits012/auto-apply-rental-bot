"""
scrapers/zillow_email.py — Parse Zillow rental alert emails.

Zillow sends alert emails from noreply@zillow.com when new listings
match your saved search. Each email may contain multiple listings.
We extract address/price/beds/url then geocode the address to apply
the 0.5mi radius filter.
"""
from __future__ import annotations

import re
import hashlib
from bs4 import BeautifulSoup

from config import ZILLOW_EMAIL_SENDER, FILTERS
from scrapers.email_reader import fetch_unread, get_html_body, get_email_date, get_email_subject
from core.geo import geocode_full, within_radius
from core.db import has_seen_email_uid, add_seen_email_uid


def _listing_id(url: str) -> str:
    # Prefer zpid from URL: /homedetails/.../123456_zpid/
    m = re.search(r"/(\d+)_zpid", url)
    if m:
        return f"zillow_email_{m.group(1)}"
    return "zillow_email_" + hashlib.md5(url.encode()).hexdigest()[:12]


def _parse_price(text: str) -> int | None:
    m = re.search(r"\$(\d[\d,]*)", text or "")
    return int(m.group(1).replace(",", "")) if m else None


def _parse_beds(text: str) -> float | None:
    t = (text or "").lower()
    if "studio" in t:
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:bd|bed|br)", t)
    return float(m.group(1)) if m else None


def _parse_baths(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ba|bath)", (text or "").lower())
    return float(m.group(1)) if m else None


def _parse_sqft(text: str) -> int | None:
    m = re.search(r"(\d[\d,]*)\s*(?:sq\.?\s*ft|sqft)", (text or "").lower())
    return int(m.group(1).replace(",", "")) if m else None


def _extract_subject_address(subject: str | None) -> str | None:
    """
    Zillow 'just listed' subjects look like:
      "3415 22nd St APT 11 just listed in 'For Rent near...'"
    Extract the address portion before 'just listed'.
    """
    if not subject:
        return None
    m = re.search(r"^(.+?)\s+just listed\b", subject, re.I)
    if m:
        return m.group(1).strip()
    return None


def _parse_email(html: str, email_date: str | None, subject: str | None = None) -> list[dict]:
    """
    Parse one Zillow alert email HTML → list of raw listing dicts.

    Strategy:
    1. For 'just listed' emails, the subject line contains a clean address.
    2. Otherwise, find price positions in the body and extract a text window.
       Pass the noisy window directly to geocode_full — Maps API handles noise well.
    """
    listings = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        full_text = soup.get_text(" ", strip=True)

        # Collect all click-tracking listing URLs
        listing_urls = []
        for a_tag in soup.find_all("a", href=re.compile(r"click\.mail\.zillow\.com", re.I)):
            link_text = a_tag.get_text(" ", strip=True)
            if re.search(r"\$[\d,]+", link_text) or re.search(r"\d+\s+bd", link_text, re.I):
                listing_urls.append(a_tag.get("href", ""))

        # Try subject-line address first (clean, no regex noise)
        subject_address = _extract_subject_address(subject)

        # Find all price occurrences — each marks a listing block
        price_positions = [m.start() for m in re.finditer(r"\$[\d,]+/mo", full_text)]

        # Image (shared across all listings in email)
        img_tag = soup.find("img", src=re.compile(r"https?://.*\.(jpg|jpeg|png|webp)", re.I))
        image_url = img_tag["src"] if img_tag else ""

        seen_raw = set()

        for i, pos in enumerate(price_positions):
            chunk = full_text[pos:pos + 400]

            price = _parse_price(chunk)
            beds  = _parse_beds(chunk)
            baths = _parse_baths(chunk)
            sqft  = _parse_sqft(chunk)

            # Address candidates, in priority order:
            # 1. Subject-line address (only for first/only listing)
            # 2. Clean regex match inside the chunk
            # 3. Whole chunk passed to Maps API (fallback)
            raw_address = None
            if i == 0 and subject_address:
                raw_address = subject_address
            else:
                m = re.search(
                    r"\b(\d+\s+[A-Za-z][^|\n]*?"
                    r"(?:St|Ave|Blvd|Dr|Rd|Way|Ln|Ct|Pl|Ter|Cir|Loop|Hwy|Bridge|Long)\b"
                    r"[^,\n]{0,30},\s*San Francisco,\s*CA(?:\s+\d{5})?)",
                    chunk, re.I
                )
                if m:
                    raw_address = m.group(1).strip()

            if not raw_address:
                # Last resort: pass the whole chunk to Maps — it filters noise
                raw_address = chunk[:300]

            # Normalize dedup: if this raw_address contains a previously seen
            # subject-line address, treat as same listing
            dedup_key = raw_address[:60]
            already_seen = dedup_key in seen_raw or any(
                s in raw_address for s in seen_raw if len(s) > 10
            )
            if already_seen:
                continue
            seen_raw.add(dedup_key)

            listing_url = listing_urls[i] if i < len(listing_urls) else (listing_urls[0] if listing_urls else "")
            listing_id = _listing_id(listing_url or raw_address)
            bd = f"{int(beds)}BD" if beds is not None else "?"
            ba = f"/{int(baths)}BA" if baths is not None else ""
            title = f"{bd}{ba}"  # address filled in after geocoding

            listings.append({
                "id":          listing_id,
                "source":      "zillow_email",
                "url":         listing_url,
                "title":       title,
                "address":     raw_address,   # may be noisy; geocode_full cleans it
                "price":       price,
                "beds":        beds,
                "baths":       baths,
                "sqft":        sqft,
                "lat":         None,
                "lng":         None,
                "description": "",
                "image_urls":  [image_url] if image_url else [],
                "listed_at":   email_date,
            })

    except Exception as e:
        print(f"  [zillow_email] parse error: {e}")

    return listings


def scrape() -> list[dict]:
    """Fetch unread Zillow alert emails, parse listings, geocode, geo-filter."""
    results = []
    emails = fetch_unread(ZILLOW_EMAIL_SENDER)

    if not emails:
        print("[zillow_email] No new alert emails")
        return []

    print(f"[zillow_email] Processing {len(emails)} alert email(s)...")

    for uid, msg in emails:
        # Belt-and-suspenders: skip if already processed
        if has_seen_email_uid(f"zillow_{uid}"):
            continue
        add_seen_email_uid(f"zillow_{uid}")

        html = get_html_body(msg)
        if not html:
            print(f"  [zillow_email] no HTML body in email uid={uid}")
            continue

        email_date = get_email_date(msg)
        subject = get_email_subject(msg)
        raw_listings = _parse_email(html, email_date, subject)

        if not raw_listings:
            print(f"  [zillow_email] 0 listings parsed from email uid={uid} (template may have changed)")
            continue

        for listing in raw_listings:
            # Apply price/beds pre-filter before geocoding (save API calls)
            price, beds = listing.get("price"), listing.get("beds")
            if price and FILTERS.get("max_price") and price > FILTERS["max_price"]:
                continue
            if price and FILTERS.get("min_price") and price < FILTERS["min_price"]:
                continue
            if beds is not None and FILTERS.get("max_beds") and beds > FILTERS["max_beds"]:
                continue
            if beds is not None and FILTERS.get("min_beds") and beds < FILTERS["min_beds"]:
                continue

            # Geocode address → lat/lng + clean formatted_address
            if listing["address"]:
                lat, lng, fmt_addr = geocode_full(listing["address"])
                listing["lat"] = lat
                listing["lng"] = lng
                if fmt_addr:
                    listing["address"] = fmt_addr
                    street = fmt_addr.split(",")[0]
                    bd = listing["title"].split("·")[0].strip() if "·" in listing["title"] else listing["title"]
                    listing["title"] = f"{bd} · {street}"
            else:
                print(f"  [zillow_email] no address found, skipping listing {listing['url']}")
                continue

            if not within_radius(listing["lat"], listing["lng"]):
                continue

            results.append(listing)

    print(f"[zillow_email] Found {len(results)} listings after filtering")
    return results
