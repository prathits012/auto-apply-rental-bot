"""
scrapers/redfin_email.py — Parse Redfin rental alert emails.

Redfin sends alerts from listings@redfin.com when new listings
match your saved search. Redfin's email HTML is well-structured
with consistent class names and often includes data-listingid attributes.
"""
from __future__ import annotations

import re
import hashlib
from bs4 import BeautifulSoup

from config import REDFIN_EMAIL_SENDER, FILTERS
from scrapers.email_reader import fetch_unread, get_html_body, get_email_date
from core.geo import geocode_and_cache, within_radius
from core.db import has_seen_email_uid, add_seen_email_uid


def _listing_id(url: str, data_id: str = "") -> str:
    if data_id:
        return f"redfin_email_{data_id}"
    # Extract numeric ID from URL path
    m = re.search(r"/(\d+)(?:/|$)", url)
    if m:
        return f"redfin_email_{m.group(1)}"
    return "redfin_email_" + hashlib.md5(url.encode()).hexdigest()[:12]


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
    Parse one Redfin alert email HTML → list of raw listing dicts.
    Never raises; logs parse errors and returns partial results.
    """
    listings = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        seen_urls = set()

        # Redfin emails: find all <a> tags linking to redfin.com listing pages
        for a_tag in soup.find_all("a", href=re.compile(r"redfin\.com.*/home/\d+", re.I)):
            url = a_tag.get("href", "").split("?")[0].strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Check for data-listingid on the element or its parent
            data_id = a_tag.get("data-listingid", "")
            container = a_tag
            for _ in range(6):
                parent = container.parent
                if parent is None:
                    break
                container = parent
                if not data_id:
                    data_id = container.get("data-listingid", "")
                text = container.get_text(" ", strip=True)
                if len(text) > 30:
                    break

            text = container.get_text(" ", strip=True)

            price = _parse_price(text)
            beds = _parse_beds(text)
            baths = _parse_baths(text)
            sqft = _parse_sqft(text)

            # Address: Redfin often has a clear address element
            address = ""
            for sel in ["[class*='address']", "[class*='street']", "address"]:
                tag = container.select_one(sel)
                if tag:
                    address = tag.get_text(" ", strip=True)
                    break
            if not address:
                m = re.search(r"\d+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:St|Ave|Blvd|Dr|Ln|Way|Pl|Ct|Rd|Ter|Loop|Cir)\b[^$\n]*",
                               text)
                if m:
                    address = m.group(0).strip()

            img_tag = container.find("img", src=re.compile(r"https?://"))
            image_url = img_tag["src"] if img_tag else ""

            listing_id = _listing_id(url, data_id)
            bd = f"{int(beds)}BD" if beds is not None else "?"
            ba = f"/{int(baths)}BA" if baths is not None else ""
            street = address.split(",")[0] if address else ""
            title = f"{bd}{ba} · {street}" if street else f"{bd}{ba}"

            listings.append({
                "id":          listing_id,
                "source":      "redfin_email",
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
        print(f"  [redfin_email] parse error: {e}")

    return listings


def scrape() -> list[dict]:
    """Fetch unread Redfin alert emails, parse listings, geocode, geo-filter."""
    results = []
    emails = fetch_unread(REDFIN_EMAIL_SENDER)

    if not emails:
        print("[redfin_email] No new alert emails")
        return []

    print(f"[redfin_email] Processing {len(emails)} alert email(s)...")

    for uid, msg in emails:
        if has_seen_email_uid(f"redfin_{uid}"):
            continue
        add_seen_email_uid(f"redfin_{uid}")

        html = get_html_body(msg)
        if not html:
            print(f"  [redfin_email] no HTML body in email uid={uid}")
            continue

        email_date = get_email_date(msg)
        raw_listings = _parse_email(html, email_date)

        if not raw_listings:
            print(f"  [redfin_email] 0 listings parsed from email uid={uid} (template may have changed)")
            continue

        for listing in raw_listings:
            price, beds = listing.get("price"), listing.get("beds")
            if price and FILTERS.get("max_price") and price > FILTERS["max_price"]:
                continue
            if price and FILTERS.get("min_price") and price < FILTERS["min_price"]:
                continue
            if beds is not None and FILTERS.get("max_beds") and beds > FILTERS["max_beds"]:
                continue
            if beds is not None and FILTERS.get("min_beds") and beds < FILTERS["min_beds"]:
                continue

            if listing["address"]:
                lat, lng = geocode_and_cache(listing["address"])
                listing["lat"] = lat
                listing["lng"] = lng
            else:
                print(f"  [redfin_email] no address found, skipping listing {listing['url']}")
                continue

            if not within_radius(listing["lat"], listing["lng"]):
                continue

            results.append(listing)

    print(f"[redfin_email] Found {len(results)} listings after filtering")
    return results
