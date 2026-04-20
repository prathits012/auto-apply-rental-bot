from __future__ import annotations
"""
scrapers/redfin.py — Redfin rental scraper using Redfin's v1 rentals API.
No headless browser needed.

Endpoint: /stingray/api/v1/search/rentals
Response schema: homes[].homeData + homes[].rentalExtension
"""

import time
import requests
from datetime import datetime, timezone
from config import FILTERS, MAX_LISTING_AGE_HOURS
from core.geo import within_radius

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.redfin.com/",
}

# SF city region: region_id=17151, region_type=6
# Market slug: sanfrancisco
SF_REGION_ID   = "17151"
SF_REGION_TYPE = "6"
SF_MARKET      = "sanfrancisco"

SEARCH_URL = "https://www.redfin.com/stingray/api/v1/search/rentals"


def _safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _normalize(home: dict):
    """Convert v1 API home object to our internal listing format."""
    hd  = home.get("homeData", {})
    ext = home.get("rentalExtension", {})

    property_id = hd.get("propertyId")
    rental_id   = ext.get("rentalId")
    uid = rental_id or property_id
    if not uid:
        return None

    # Price: use min of range (lowest available unit)
    price_range = ext.get("rentPriceRange", {})
    price = price_range.get("min") or price_range.get("max")
    try:
        price = int(price) if price else None
    except (TypeError, ValueError):
        price = None

    # Beds/baths: use min of range
    bed_range  = ext.get("bedRange", {})
    bath_range = ext.get("bathRange", {})
    sqft_range = ext.get("sqftRange", {})
    beds  = _safe_float(bed_range.get("min"))
    baths = _safe_float(bath_range.get("min"))
    sqft  = sqft_range.get("min")
    try:
        sqft = int(sqft) if sqft else None
    except (TypeError, ValueError):
        sqft = None

    # Address
    addr_info = hd.get("addressInfo", {})
    street = addr_info.get("formattedStreetLine", "")
    city   = addr_info.get("city", "")
    state  = addr_info.get("state", "")
    zip_   = addr_info.get("zip", "")
    address = ", ".join(p for p in [street, city, state, zip_] if p)

    # Coordinates
    centroid = addr_info.get("centroid", {}).get("centroid", {})
    lat = _safe_float(centroid.get("latitude"))
    lng = _safe_float(centroid.get("longitude"))

    # URL
    url_path = hd.get("url", "")
    url = f"https://www.redfin.com{url_path}" if url_path.startswith("/") else url_path

    # Title
    bd = f"{int(beds)}BD" if beds is not None else "?"
    ba = f"/{int(baths)}BA" if baths is not None else ""
    title = f"{bd}{ba} · {street}"

    return {
        "id":          f"redfin_{uid}",
        "source":      "redfin",
        "url":         url,
        "title":       title,
        "address":     address,
        "price":       price,
        "beds":        beds,
        "baths":       baths,
        "sqft":        sqft,
        "lat":         lat,
        "lng":         lng,
        "description": ext.get("description", "") or "",
        "image_urls":  [],
        "listed_at":   None,
    }


def _apply_filters(listing: dict) -> bool:
    price = listing.get("price")
    beds  = listing.get("beds")

    if FILTERS.get("max_price") and price and price > FILTERS["max_price"]:
        return False
    if FILTERS.get("min_price") and price and price < FILTERS["min_price"]:
        return False
    if FILTERS.get("min_beds") is not None and beds is not None and beds < FILTERS["min_beds"]:
        return False
    if FILTERS.get("max_beds") is not None and beds is not None and beds > FILTERS["max_beds"]:
        return False
    if not within_radius(listing.get("lat"), listing.get("lng")):
        return False
    return True


def scrape() -> list[dict]:
    """Scrape Redfin SF rentals via v1 API, radius-filter to our area."""
    print("[redfin] Searching SF rentals (v1 API)...")

    params = {
        "al":                 1,
        "isRentals":          "true",
        "consolidateBuildings": "true",
        "market":             SF_MARKET,
        "num":                150,
        "region_id":          SF_REGION_ID,
        "region_type":        SF_REGION_TYPE,
        "status":             1,
        "v":                  8,
    }
    if FILTERS.get("min_price"):
        params["minPrice"] = FILTERS["min_price"]
    if FILTERS.get("max_price"):
        params["maxPrice"] = FILTERS["max_price"]

    try:
        resp = requests.get(SEARCH_URL, headers=HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        homes = data.get("homes", [])
    except Exception as e:
        print(f"  [redfin] API error: {e}")
        return []

    print(f"  → {len(homes)} raw results")

    all_listings = []
    seen_ids = set()
    for home in homes:
        listing = _normalize(home)
        if not listing or listing["id"] in seen_ids:
            continue
        if not _apply_filters(listing):
            continue
        seen_ids.add(listing["id"])
        all_listings.append(listing)

    print(f"[redfin] Found {len(all_listings)} listings after filtering")
    return all_listings
