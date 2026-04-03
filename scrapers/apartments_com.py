from __future__ import annotations
"""
scrapers/apartments_com.py — Apartments.com SF rental scraper.
Uses Playwright (headless Chromium) to render JS-heavy pages.
Run `playwright install chromium` once after pip install.
"""

import json
import re
import time
import random
import hashlib
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
from config import (
    APARTMENTS_COM_CITY,
    APARTMENTS_COM_MAX_PAGES,
    APARTMENTS_COM_HEADLESS,
    FILTERS,
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

BASE_URL = "https://www.apartments.com"


def _listing_id(url: str) -> str:
    return "apartments_com_" + hashlib.md5(url.encode()).hexdigest()[:12]


def _parse_price(text: str) -> int | None:
    """Extract the low end of a price range like '$2,500 - $3,200' or '$2,500'."""
    match = re.search(r"\$(\d[\d,]*)", text or "")
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def _parse_beds(text: str) -> float | None:
    text = (text or "").lower()
    if "studio" in text:
        return 0.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:bd|bed|br)", text)
    if match:
        return float(match.group(1))
    return None


def _parse_baths(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:ba|bath)", (text or "").lower())
    if match:
        return float(match.group(1))
    return None


def _parse_sqft(text: str) -> int | None:
    match = re.search(r"(\d[\d,]*)\s*(?:sq\.?\s*ft|sqft)", (text or "").lower())
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def _random_delay(lo: float = 1.0, hi: float = 2.5) -> None:
    time.sleep(random.uniform(lo, hi))


def _build_search_url(page_num: int = 1) -> str:
    """
    Apartments.com URL pattern:
      https://www.apartments.com/{city}/{price-range}/{beds-filter}/{page}/
    Filters are optional path segments.
    """
    segments = [BASE_URL, APARTMENTS_COM_CITY]

    min_price = FILTERS.get("min_price")
    max_price = FILTERS.get("max_price")
    if min_price or max_price:
        lo = min_price or 0
        hi = max_price or 99999
        segments.append(f"{lo}-{hi}")

    min_beds = FILTERS.get("min_beds")
    if min_beds and min_beds > 0:
        segments.append(f"min-{int(min_beds)}-bedrooms")

    if page_num > 1:
        segments.append(str(page_num))

    return "/".join(segments) + "/"


def _collect_listing_urls(page) -> list[str]:
    """Extract detail-page URLs from a search results page."""
    urls = []
    cards = page.query_selector_all("article.placard")
    for card in cards:
        link = card.query_selector("a.property-link")
        if link:
            href = link.get_attribute("href")
            if href and href.startswith("http"):
                urls.append(href)
    return urls


def _fetch_detail(page, url: str) -> dict:
    """Visit a listing detail page and extract structured data."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        _random_delay()

        result = {
            "title": "",
            "price": None,
            "beds": None,
            "baths": None,
            "sqft": None,
            "address": "",
            "lat": None,
            "lng": None,
            "description": "",
            "image_urls": [],
        }

        # --- JSON-LD structured data (most reliable) ---
        ld_tags = page.query_selector_all("script[type='application/ld+json']")
        for tag in ld_tags:
            try:
                data = json.loads(tag.inner_text())
                # Handle list wrappers
                if isinstance(data, list):
                    data = data[0]
                schema_type = data.get("@type", "")
                if schema_type in ("Apartment", "ApartmentComplex", "Place", "RealEstateListing"):
                    result["title"] = data.get("name", "")
                    geo = data.get("geo", {})
                    result["lat"] = geo.get("latitude") or geo.get("latitude ")
                    result["lng"] = geo.get("longitude") or geo.get("longitude ")
                    addr = data.get("address", {})
                    if isinstance(addr, dict):
                        parts = [
                            addr.get("streetAddress", ""),
                            addr.get("addressLocality", ""),
                            addr.get("addressRegion", ""),
                            addr.get("postalCode", ""),
                        ]
                        result["address"] = ", ".join(p for p in parts if p)
                    elif isinstance(addr, str):
                        result["address"] = addr
                    result["description"] = data.get("description", "")
                    # Images from JSON-LD
                    images = data.get("image", [])
                    if isinstance(images, str):
                        images = [images]
                    result["image_urls"] = [
                        img.get("url", img) if isinstance(img, dict) else img
                        for img in images
                    ]
                    break
            except Exception:
                continue

        # --- Property title fallback ---
        if not result["title"]:
            el = page.query_selector("h1.propertyName") or page.query_selector("h1[class*='property']")
            if el:
                result["title"] = el.inner_text().strip()

        # --- Address fallback ---
        if not result["address"]:
            el = page.query_selector(".propertyAddress") or page.query_selector("[class*='address']")
            if el:
                result["address"] = el.inner_text().strip()

        # --- Price ---
        price_el = (
            page.query_selector(".rentLabel")
            or page.query_selector("[class*='rent']")
            or page.query_selector("[class*='price']")
        )
        if price_el:
            result["price"] = _parse_price(price_el.inner_text())

        # --- Beds / baths / sqft from summary bar ---
        summary = page.query_selector(".priceBedRangeInfo") or page.query_selector("[class*='priceBed']")
        if summary:
            text = summary.inner_text()
            result["beds"]  = result["beds"]  or _parse_beds(text)
            result["baths"] = result["baths"] or _parse_baths(text)
            result["sqft"]  = result["sqft"]  or _parse_sqft(text)

        # Fallback: scan floor plan table for beds/baths/sqft
        if result["beds"] is None:
            fp_row = page.query_selector("tr.floorplan") or page.query_selector("[class*='floorplan']")
            if fp_row:
                text = fp_row.inner_text()
                result["beds"]  = result["beds"]  or _parse_beds(text)
                result["baths"] = result["baths"] or _parse_baths(text)
                result["sqft"]  = result["sqft"]  or _parse_sqft(text)

        # --- Description fallback ---
        if not result["description"]:
            desc_el = (
                page.query_selector(".descriptionSection")
                or page.query_selector("[class*='description']")
            )
            if desc_el:
                result["description"] = desc_el.inner_text().strip()

        # --- Images fallback ---
        if not result["image_urls"]:
            imgs = page.query_selector_all("img[src*='apartments.com']")
            result["image_urls"] = [
                img.get_attribute("src") for img in imgs
                if img.get_attribute("src") and "logo" not in (img.get_attribute("src") or "")
            ]

        return result

    except Exception as e:
        print(f"  [apartments_com] detail fetch failed for {url}: {e}")
        return {}


def scrape() -> list[dict]:
    """
    Scrape Apartments.com SF listings using Playwright.
    Returns list of normalized listing dicts.
    """
    print("[apartments_com] Starting scrape...")
    listings = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=APARTMENTS_COM_HEADLESS)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        context.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
        page = context.new_page()

        # --- Collect listing URLs from search pages ---
        detail_urls = []
        for page_num in range(1, APARTMENTS_COM_MAX_PAGES + 1):
            url = _build_search_url(page_num)
            print(f"[apartments_com] Fetching search page {page_num}: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Wait for listing cards to appear
                page.wait_for_selector("article.placard", timeout=15000)
            except Exception as e:
                print(f"  [apartments_com] search page {page_num} failed: {e}")
                break

            urls = _collect_listing_urls(page)
            if not urls:
                print(f"  [apartments_com] No listings found on page {page_num}, stopping.")
                break

            detail_urls.extend(urls)
            print(f"  [apartments_com] Found {len(urls)} listings on page {page_num}")
            _random_delay()

        # Deduplicate URLs
        detail_urls = list(dict.fromkeys(detail_urls))
        print(f"[apartments_com] Fetching details for {len(detail_urls)} listings...")

        # --- Fetch each detail page ---
        for detail_url in detail_urls:
            try:
                detail = _fetch_detail(page, detail_url)
                if not detail:
                    continue

                listing = {
                    "id":          _listing_id(detail_url),
                    "source":      "apartments_com",
                    "url":         detail_url,
                    "title":       detail.get("title", ""),
                    "price":       detail.get("price"),
                    "beds":        detail.get("beds"),
                    "baths":       detail.get("baths"),
                    "sqft":        detail.get("sqft"),
                    "address":     detail.get("address", ""),
                    "lat":         detail.get("lat"),
                    "lng":         detail.get("lng"),
                    "description": detail.get("description", ""),
                    "image_urls":  detail.get("image_urls", []),
                    "listed_at":   datetime.now(timezone.utc).isoformat(),
                    "complex_id":  None,
                }

                # Apply basic filters
                if FILTERS.get("min_beds") and (listing["beds"] or 0) < FILTERS["min_beds"]:
                    continue
                if FILTERS.get("min_sqft") and (listing["sqft"] or 0) < FILTERS["min_sqft"]:
                    continue
                if FILTERS.get("max_price") and listing["price"] and listing["price"] > FILTERS["max_price"]:
                    continue
                if FILTERS.get("min_price") and listing["price"] and listing["price"] < FILTERS["min_price"]:
                    continue

                listings.append(listing)

            except Exception as e:
                print(f"  [apartments_com] error processing {detail_url}: {e}")
                continue

        browser.close()

    print(f"[apartments_com] Found {len(listings)} listings")
    return listings
