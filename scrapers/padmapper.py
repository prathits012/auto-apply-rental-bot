"""
scrapers/padmapper.py — Padmapper rental scraper.
Uses Padmapper's internal POST JSON API — no headless browser needed.
Padmapper aggregates Zillow, Craigslist, and other sources, giving us
indirect Zillow coverage without hitting Zillow directly.

SF city_id = 2777 (discovered via page source inspection).
"""

import time
import requests
from datetime import datetime, timezone
from config import FILTERS, MAX_LISTING_AGE_HOURS
from core.geo import within_radius

SF_CITY_ID = 2777

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json",
    "Content-Type":    "application/json",
    "Referer":         "https://www.padmapper.com/apartments/san-francisco-ca",
    "Accept-Language": "en-US,en;q=0.9",
}

API_URL = "https://www.padmapper.com/api/t/1/listings"
PAGE_SIZE = 50


def _normalize(raw: dict):
    listing_id = raw.get("listing_id") or raw.get("pb_id")
    if not listing_id:
        return None

    # Price: use min_price (lowest unit in a building)
    price = raw.get("min_price") or raw.get("price")
    if isinstance(price, dict):
        price = price.get("value")
    try:
        price = int(price) if price else None
    except (TypeError, ValueError):
        price = None

    # Beds/baths
    beds = raw.get("bedrooms")
    baths = raw.get("bathrooms")
    try:
        beds  = float(beds)  if beds  is not None else None
        baths = float(baths) if baths is not None else None
    except (TypeError, ValueError):
        beds, baths = None, None

    sqft = raw.get("square_feet")
    try:
        sqft = int(sqft) if sqft else None
    except (TypeError, ValueError):
        sqft = None

    # Address
    address = raw.get("address", "")
    if isinstance(address, dict):
        parts = [
            address.get("street", ""),
            address.get("city", ""),
            address.get("state", ""),
            address.get("zip", ""),
        ]
        address = ", ".join(p for p in parts if p)

    # Coordinates
    loc = raw.get("listing_location") or {}
    lat = loc.get("latitude")  or loc.get("lat")
    lng = loc.get("longitude") or loc.get("lng")
    try:
        lat = float(lat) if lat is not None else None
        lng = float(lng) if lng is not None else None
    except (TypeError, ValueError):
        lat, lng = None, None

    # URL — prefer pa_url (Padmapper detail page) over external provider_url
    url = raw.get("pa_url") or raw.get("pl_url") or raw.get("provider_url") or ""
    if url and not url.startswith("http"):
        url = "https://www.padmapper.com/" + url.lstrip("/")

    # Listed date
    listed_at = None
    for field in ("listed_on", "created_on", "modified_on"):
        val = raw.get(field)
        if val:
            try:
                listed_at = datetime.fromisoformat(val.rstrip("Z")).replace(tzinfo=timezone.utc).isoformat()
                break
            except Exception:
                pass

    # Images
    image_urls = []
    for media in raw.get("media", []):
        if isinstance(media, dict):
            src = media.get("uri") or media.get("url") or media.get("src")
            if src:
                image_urls.append(src)

    # Title — ignore short floorplan codes like "A9", "A28", "A2-H"
    raw_title = raw.get("title") or ""
    if raw_title and len(raw_title) > 8 and " " in raw_title:
        title = raw_title
    else:
        bd = f"{int(beds)}BD" if beds is not None else "?"
        ba = f"/{int(baths)}BA" if baths is not None else ""
        street = address.split(",")[0] if address else ""
        title = f"{bd}{ba} · {street}"

    # Feed source tag (e.g. "zillow", "craigslist", "padmapper")
    feed = raw.get("feed_name", "")

    return {
        "id":          f"padmapper_{listing_id}",
        "source":      f"padmapper" + (f"/{feed}" if feed else ""),
        "url":         url,
        "title":       title,
        "address":     address,
        "price":       price,
        "beds":        beds,
        "baths":       baths,
        "sqft":        sqft,
        "lat":         lat,
        "lng":         lng,
        "description": raw.get("description") or raw.get("short_description") or "",
        "image_urls":  image_urls,
        "listed_at":   listed_at,
        "neighborhood": (raw.get("neighborhood") or {}).get("name", "") if isinstance(raw.get("neighborhood"), dict) else (raw.get("neighborhood") or ""),
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
    """
    Fetch SF rental listings from Padmapper's internal API.
    Paginates until results are exhausted or listings get too old.
    """
    all_listings = []
    seen_ids = set()
    offset = 0

    print("[padmapper] Scraping SF rentals...")

    while True:
        payload = {
            "city_id":     SF_CITY_ID,
            "limit":       PAGE_SIZE,
            "offset":      offset,
            "min_rent":    FILTERS.get("min_price", 0),
            "max_rent":    FILTERS.get("max_price", 9999),
            "min_bedrooms": int(FILTERS.get("min_beds", 0)),
        }

        raw_list = None
        for attempt in range(2):
            try:
                resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=15)
                if resp.status_code == 429:
                    if attempt == 0:
                        print(f"  [padmapper] 429 rate limit, retrying in 15s...")
                        time.sleep(15)
                        continue
                    else:
                        print(f"  [padmapper] 429 persists, stopping pagination")
                        break
                resp.raise_for_status()
                raw_list = resp.json()
                break
            except Exception as e:
                print(f"  [padmapper] API error at offset {offset} (attempt {attempt+1}): {e}")
                if attempt < 1:
                    time.sleep(5)
        if raw_list is None:
            break

        if not raw_list:
            break

        new_this_page = 0
        too_old = 0

        for raw in raw_list:
            listing = _normalize(raw)
            if not listing:
                continue
            if listing["id"] in seen_ids:
                continue

            # Age filter
            if listing.get("listed_at") and MAX_LISTING_AGE_HOURS:
                try:
                    age = (datetime.now(timezone.utc) -
                           datetime.fromisoformat(listing["listed_at"])).total_seconds() / 3600
                    if age > MAX_LISTING_AGE_HOURS:
                        too_old += 1
                        continue
                except Exception:
                    pass

            if not _apply_filters(listing):
                continue

            seen_ids.add(listing["id"])
            all_listings.append(listing)
            new_this_page += 1

        print(f"  [padmapper] offset={offset} → {new_this_page} kept, {too_old} too old")

        # Stop if whole page was too old or we got fewer results than page size
        if too_old == len(raw_list) or len(raw_list) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(1.5)

    print(f"[padmapper] Found {len(all_listings)} listings after filtering")
    return all_listings
