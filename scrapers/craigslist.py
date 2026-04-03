from __future__ import annotations
"""
scrapers/craigslist.py — Craigslist SF rental scraper.
Uses RSS feed (reliable) + HTML detail page for full description.
No headless browser needed.
"""

import re
import hashlib
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from config import CRAIGSLIST_CITY, CRAIGSLIST_REGION, FILTERS, MAX_LISTING_AGE_HOURS
from core.geo import within_radius


RSS_URL = (
    f"https://{CRAIGSLIST_CITY}.craigslist.org/search/{CRAIGSLIST_REGION}/apa"
    f"?format=rss"
    f"&min_price={FILTERS.get('min_price', 0)}"
    f"&max_price={FILTERS.get('max_price', 9999)}"
    f"&min_bedrooms={int(FILTERS.get('min_beds', 0))}"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36"
}


def _listing_id(url: str) -> str:
    # Extract CL post ID from URL like .../1234567890.html
    match = re.search(r"/(\d{10})\.html", url)
    if match:
        return f"craigslist_{match.group(1)}"
    return f"craigslist_{hashlib.md5(url.encode()).hexdigest()[:10]}"


def _parse_price(text: str) -> int | None:
    match = re.search(r"\$(\d[\d,]*)", text or "")
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def _parse_beds(text: str) -> float | None:
    """Extract bedroom count from title like '2br' or '1BD'."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:br|bd|bed)", text.lower())
    if match:
        return float(match.group(1))
    if "studio" in text.lower():
        return 0
    return None


def _fetch_detail(url: str) -> dict:
    """Fetch the full listing page to get description, images, address."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(resp.text, "html.parser")

        description = ""
        desc_el = soup.select_one("#postingbody")
        if desc_el:
            description = desc_el.get_text(separator=" ", strip=True)
            description = re.sub(r"QR Code Link to This Post\s*", "", description)

        # Images
        image_urls = []
        for img in soup.select("img.thumb"):
            src = img.get("src", "")
            if src:
                image_urls.append(src.replace("50x50c", "600x450"))

        # Address / map
        address = ""
        map_el = soup.select_one(".mapaddress")
        if map_el:
            address = map_el.get_text(strip=True)

        # Lat/lng from map
        lat, lng = None, None
        map_tag = soup.select_one("#map")
        if map_tag:
            lat = map_tag.get("data-latitude")
            lng = map_tag.get("data-longitude")
            lat = float(lat) if lat else None
            lng = float(lng) if lng else None

        # Attributes (beds, baths, sqft)
        attrs = {}
        for span in soup.select(".attrgroup span"):
            t = span.get_text(strip=True).lower()
            if "br" in t or "bd" in t:
                attrs["beds"] = _parse_beds(t)
            elif "ba" in t:
                m = re.search(r"(\d+(?:\.\d+)?)", t)
                if m:
                    attrs["baths"] = float(m.group(1))
            elif "ft²" in t or "sqft" in t:
                m = re.search(r"(\d+)", t)
                if m:
                    attrs["sqft"] = int(m.group(1))

        return {
            "description": description,
            "image_urls":  image_urls,
            "address":     address,
            "lat":         lat,
            "lng":         lng,
            **attrs,
        }
    except Exception as e:
        print(f"  [craigslist] detail fetch failed: {e}")
        return {}


def scrape() -> list[dict]:
    """
    Fetch and parse Craigslist SF rental RSS feed.
    Returns list of normalized listing dicts.
    """
    print(f"[craigslist] Fetching RSS feed...")
    feed = feedparser.parse(RSS_URL)
    listings = []

    for entry in feed.entries:
        try:
            url   = entry.link
            title = entry.get("title", "")
            price = _parse_price(title) or _parse_price(entry.get("summary", ""))
            beds  = _parse_beds(title)

            # Rough age check from RSS pubdate
            listed_at = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                listed_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - listed_at).total_seconds() / 3600
                if age_hours > MAX_LISTING_AGE_HOURS:
                    continue

            listing_id = _listing_id(url)

            listing = {
                "id":        listing_id,
                "source":    "craigslist",
                "url":       url,
                "title":     title,
                "price":     price,
                "beds":      beds,
                "baths":     None,
                "sqft":      None,
                "address":   "",
                "lat":       None,
                "lng":       None,
                "listed_at": listed_at.isoformat() if listed_at else None,
            }

            # Fetch detail page for full info
            print(f"  [craigslist] Fetching detail: {title[:60]}...")
            detail = _fetch_detail(url)
            listing.update(detail)

            # Apply filters
            beds = listing.get("beds")
            if FILTERS.get("min_beds") and (beds or 0) < FILTERS["min_beds"]:
                continue
            if FILTERS.get("max_beds") is not None and beds is not None and beds > FILTERS["max_beds"]:
                continue
            if not within_radius(listing.get("lat"), listing.get("lng")):
                continue

            listings.append(listing)

        except Exception as e:
            print(f"  [craigslist] parse error: {e}")
            continue

    print(f"[craigslist] Found {len(listings)} listings")
    return listings
