"""
scrapers/apartments_email.py — Parse Apartments.com rental alert emails.

Apartments.com sends alerts from automated@apartments.com (verify on
first email — update APARTMENTS_EMAIL_SENDER env var if different).
Each email may contain multiple property cards.
"""
from __future__ import annotations

import re
import hashlib
from bs4 import BeautifulSoup

from config import APARTMENTS_EMAIL_SENDER
from scrapers.email_reader import fetch_unread, get_html_body, get_email_date
from core.geo import geocode_full
from core.db import has_seen_email_uid, add_seen_email_uid


def _listing_id(url: str) -> str:
    # apartments.com URLs: /san-francisco-ca/complex-name/
    # Use MD5 of the clean URL as ID
    clean = url.split("?")[0].rstrip("/")
    return "apartments_email_" + hashlib.md5(clean.encode()).hexdigest()[:12]


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


def _parse_email(html: str, email_date: str | None) -> list[dict]:
    """
    Parse one Apartments.com alert email HTML → list of raw listing dicts.

    Apartments.com email format (clean text blocks):
      $PRICE [beds/baths] [Building Name] ADDRESS, San Francisco, CA ZIPCODE View Details
    """
    listings = []
    try:
        soup = BeautifulSoup(html, "html.parser")

        # Get listing URLs keyed by address — <a href> tags with "View Details" text
        url_map = {}
        for a_tag in soup.find_all("a", href=re.compile(r"apartments\.com", re.I)):
            if "view" in a_tag.get_text(strip=True).lower() or "detail" in a_tag.get_text(strip=True).lower():
                href = a_tag.get("href", "").split("?")[0]
                if href:
                    url_map[href] = href

        # Parse full text: split on "View Details" — each chunk is one listing
        full_text = soup.get_text(" ", strip=True)
        blocks = re.split(r"View Details", full_text, flags=re.IGNORECASE)

        urls = list(url_map.values())
        seen_addresses = set()

        for i, block in enumerate(blocks[:-1]):  # last block is footer
            # Address: find "123 Street Name ..., San Francisco, CA XXXXX"
            # Use \d{2,} to avoid matching "1 Bed" / "1 Bath" as house numbers
            addr_m = re.search(
                r"\b(\d{2,}\s+[A-Za-z][^•\n]*?"
                r"(?:St|Ave|Blvd|Dr|Rd|Way|Ln|Ct|Pl|Ter|Cir|Loop|Hwy|Fwy|Market|Mission)\b"
                r"[^,\n]*\bSan Francisco\b[^,\n]*,\s*CA(?:\s+\d{5})?)",
                block, re.I
            )
            if not addr_m:
                continue
            address = addr_m.group(1).strip()
            if address in seen_addresses:
                continue
            seen_addresses.add(address)

            price = _parse_price(block)
            beds = _parse_beds(block)
            baths = _parse_baths(block)
            sqft = _parse_sqft(block)

            url = urls[i] if i < len(urls) else ""
            listing_id = _listing_id(url or address)
            bd = f"{int(beds)}BD" if beds is not None else "?"
            ba = f"/{int(baths)}BA" if baths is not None else ""
            street = address.split(",")[0] if address else ""
            title = f"{bd}{ba} · {street}" if street else f"{bd}{ba}"

            img_tag = soup.find("img", src=re.compile(r"https?://.*\.(jpg|jpeg|png|webp)", re.I))
            image_url = img_tag["src"] if img_tag else ""

            listings.append({
                "id":          listing_id,
                "source":      "apartments_email",
                "url":         url,
                "title":       title,
                "address":     address,
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
        print(f"  [apartments_email] parse error: {e}")

    return listings


def scrape() -> list[dict]:
    """Fetch unread Apartments.com alert emails, parse listings, geocode, geo-filter."""
    results = []
    emails = fetch_unread(APARTMENTS_EMAIL_SENDER)

    if not emails:
        print("[apartments_email] No new alert emails")
        return []

    print(f"[apartments_email] Processing {len(emails)} alert email(s)...")

    for uid, msg in emails:
        if has_seen_email_uid(f"apartments_{uid}"):
            continue
        add_seen_email_uid(f"apartments_{uid}")

        html = get_html_body(msg)
        if not html:
            print(f"  [apartments_email] no HTML body in email uid={uid}")
            continue

        email_date = get_email_date(msg)
        raw_listings = _parse_email(html, email_date)

        if not raw_listings:
            print(f"  [apartments_email] 0 listings parsed from email uid={uid} (template may have changed)")
            continue

        for listing in raw_listings:
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
                print(f"  [apartments_email] no address found, skipping listing {listing['url']}")
                continue

            results.append(listing)

    print(f"[apartments_email] Parsed {len(results)} listings")
    return results
