from __future__ import annotations
"""
core/registry.py — Complex registry management.
Add buildings by name+address. Geocodes once, matches forever.
"""

import re
import json
import requests
from geopy.distance import geodesic
from rapidfuzz import fuzz
from config import GOOGLE_MAPS_API_KEY, GEOCODE_MATCH_METERS, FUZZY_MATCH_THRESHOLD
from core.db import upsert_complex, get_all_complexes, update_complex_status


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _normalize_name(name: str) -> str:
    """Strip generic suffixes for fuzzy matching."""
    stopwords = [
        "apartments", "apartment", "condos", "condo", "at", "the",
        "sf", "san francisco", "flats", "residences", "living"
    ]
    n = name.lower()
    for w in stopwords:
        n = re.sub(rf"\b{w}\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def geocode_address(address: str) -> tuple[float, float] | None:
    """Return (lat, lng) for an address using Google Maps API."""
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address + ", San Francisco, CA", "key": GOOGLE_MAPS_API_KEY}
    resp = requests.get(url, params=params, timeout=10)
    data = resp.json()
    if data.get("status") == "OK":
        loc = data["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    return None


def add_complex(name: str, address: str, aliases: list = None, status: str = "watching"):
    """
    Add a building to your watchlist.
    Geocodes the address and stores everything in SQLite.

    Usage:
        add_complex("The Avery SF", "488 Folsom St, San Francisco, CA")
    """
    print(f"  Geocoding {address}...")
    coords = geocode_address(address)
    if not coords:
        print(f"  WARNING: Could not geocode '{address}'. Add it manually later.")
        lat, lng = None, None
    else:
        lat, lng = coords
        print(f"  → {lat:.5f}, {lng:.5f}")

    complex_id = _slugify(name)
    upsert_complex({
        "id":      complex_id,
        "name":    name,
        "address": address,
        "lat":     lat,
        "lng":     lng,
        "aliases": aliases or [],
        "status":  status,
    })
    print(f"  Saved: {name} [{complex_id}]")
    return complex_id


def match_listing_to_complex(listing: dict) -> str | None:
    """
    Try to match a listing to a known complex.
    Returns complex_id if matched, else None.

    Matching layers (in order):
      1. Geocode proximity (within GEOCODE_MATCH_METERS)
      2. Fuzzy name match against complex names + aliases
    """
    complexes = get_all_complexes(status="watching")
    if not complexes:
        return None

    # Layer 1: geo proximity
    if listing.get("lat") and listing.get("lng"):
        listing_point = (listing["lat"], listing["lng"])
        for c in complexes:
            if c.get("lat") and c.get("lng"):
                dist = geodesic(listing_point, (c["lat"], c["lng"])).meters
                if dist <= GEOCODE_MATCH_METERS:
                    print(f"  [registry] Geo match → {c['name']} ({dist:.0f}m)")
                    return c["id"]

    # Layer 2: fuzzy name match
    listing_name = _normalize_name(listing.get("title", "") or listing.get("address", ""))
    if listing_name:
        for c in complexes:
            candidates = [c["name"]] + c.get("aliases", [])
            for candidate in candidates:
                score = fuzz.ratio(listing_name, _normalize_name(candidate))
                if score >= FUZZY_MATCH_THRESHOLD:
                    print(f"  [registry] Fuzzy match → {c['name']} (score={score})")
                    return c["id"]

    return None


def list_complexes():
    """Print all complexes in the registry."""
    complexes = get_all_complexes()
    if not complexes:
        print("No complexes in registry. Add one with: add_complex('Name', 'Address')")
        return
    print(f"\n{'Name':<30} {'Status':<12} {'Address'}")
    print("-" * 80)
    for c in complexes:
        print(f"  {c['name']:<28} {c['status']:<12} {c['address']}")


def pause_complex(complex_id: str):
    update_complex_status(complex_id, "paused")
    print(f"Paused: {complex_id}")


def watch_complex(complex_id: str):
    update_complex_status(complex_id, "watching")
    print(f"Watching: {complex_id}")


# ── CLI entry point ───────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from core.db import init_db
    init_db()

    args = sys.argv[1:]
    if not args:
        list_complexes()
    elif args[0] == "add" and len(args) >= 3:
        add_complex(args[1], args[2])
    elif args[0] == "list":
        list_complexes()
    elif args[0] == "pause" and len(args) >= 2:
        pause_complex(args[1])
    elif args[0] == "watch" and len(args) >= 2:
        watch_complex(args[1])
    else:
        print("Usage:")
        print("  python -m core.registry add 'Complex Name' 'Address'")
        print("  python -m core.registry list")
        print("  python -m core.registry pause <id>")
        print("  python -m core.registry watch <id>")
