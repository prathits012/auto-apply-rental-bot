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
from scrapers.email_reader import fetch_unread, get_html_body, get_email_date
from core.geo import geocode_and_cache, within_radius
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


def _parse_email(html: str, email_date: str | None) -> list[dict]:
    """
    Parse one Zillow alert email HTML → list of raw listing dicts.
    Never raises; logs parse errors and returns partial results.
    """
    listings = []
    try:
        soup = BeautifulSoup(html, "html.parser")

        # Zillow emails: listings are linked via <a href="https://www.zillow.com/...">
        # Each listing block contains address, price, beds/baths text nearby.
        # Strategy: find all zillow rental/homedetail links, then walk up to
        # the containing block to extract sibling text.
        seen_urls = set()

        # Find all <a> tags pointing to zillow listing pages
        for a_tag in soup.find_all("a", href=re.compile(r"zillow\.com.*(homedetails|rental)", re.I)):
            url = a_tag.get("href", "").split("?")[0].strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Walk up to find a container td/div/table that holds the listing block
            container = a_tag
            for _ in range(6):
                parent = container.parent
                if parent is None:
                    break
                container = parent
                # Stop at a reasonably sized block
                text = container.get_text(" ", strip=True)
                if len(text) > 30:
                    break

            text = container.get_text(" ", strip=True)

            # Extract fields from the container text
            price = _parse_price(text)
            beds = _parse_beds(text)
            baths = _parse_baths(text)
            sqft = _parse_sqft(text)

            # Address: look for an <address> tag or text that looks like a street
            address = ""
            addr_tag = container.find("address")
            if addr_tag:
                address = addr_tag.get_text(" ", strip=True)
            else:
                # Try to find address-like text: "123 Main St" pattern
                m = re.search(r"\d+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:St|Ave|Blvd|Dr|Ln|Way|Pl|Ct|Rd|Ter|Loop|Cir)\b[^$\n]*",
                               text)
                if m:
                    address = m.group(0).strip()

            # Image
            img_tag = container.find("img", src=re.compile(r"https?://"))
            image_url = img_tag["src"] if img_tag else ""

            if not url:
                continue

            listing_id = _listing_id(url)
            bd = f"{int(beds)}BD" if beds is not None else "?"
            ba = f"/{int(baths)}BA" if baths is not None else ""
            street = address.split(",")[0] if address else ""
            title = f"{bd}{ba} · {street}" if street else f"{bd}{ba}"

            listings.append({
                "id":          listing_id,
                "source":      "zillow_email",
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
        raw_listings = _parse_email(html, email_date)

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

            # Geocode address → lat/lng
            if listing["address"]:
                lat, lng = geocode_and_cache(listing["address"])
                listing["lat"] = lat
                listing["lng"] = lng
            else:
                # No address in email — drop, don't bypass geo filter
                print(f"  [zillow_email] no address found, skipping listing {listing['url']}")
                continue

            if not within_radius(listing["lat"], listing["lng"]):
                continue

            results.append(listing)

    print(f"[zillow_email] Found {len(results)} listings after filtering")
    return results
