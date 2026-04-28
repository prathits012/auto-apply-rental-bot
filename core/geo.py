"""
core/geo.py — Geo helpers for radius filtering and geocoding.
"""
from __future__ import annotations
import math
import re
from config import SEARCH_CENTER_LAT, SEARCH_CENTER_LNG, SEARCH_RADIUS_MILES, GOOGLE_MAPS_API_KEY

_geocode_mem: dict[str, tuple[float, float] | None] = {}


def _haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in miles between two lat/lng points."""
    R = 3958.8  # Earth radius in miles
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def within_radius(lat: float | None, lng: float | None) -> bool:
    """Return True if the point is within SEARCH_RADIUS_MILES of the center."""
    if lat is None or lng is None:
        return True   # no coords → don't filter out, let other signals decide
    return _haversine_miles(SEARCH_CENTER_LAT, SEARCH_CENTER_LNG, lat, lng) <= SEARCH_RADIUS_MILES


def _normalize_address_key(address: str) -> str:
    """Normalize address string for use as a cache key."""
    a = address.lower().strip()
    a = re.sub(r",?\s*(san francisco|sf),?\s*(ca|california)?\s*,?\s*\d{5}?", "", a)
    return re.sub(r"\s+", " ", a).strip()


def _strip_unit(address: str) -> str:
    """Remove apartment/unit/suite numbers that confuse Nominatim."""
    return re.sub(
        r"\s*(?:#|apt\.?|apartment|unit|ste\.?|suite|floor|fl\.?)\s*[\w-]+",
        "", address, flags=re.I
    ).strip().rstrip(",")


def _geocode_nominatim(address: str) -> tuple[float | None, float | None, str | None]:
    """Fallback geocoder using Nominatim (OpenStreetMap). No API key needed."""
    try:
        from geopy.geocoders import Nominatim
        geolocator = Nominatim(user_agent="sf_rental_bot/1.0")
        # Try original address first, then stripped version
        for query in [address, _strip_unit(address)]:
            location = geolocator.geocode(query, timeout=8)
            if location:
                return location.latitude, location.longitude, location.address
    except Exception as e:
        print(f"  [geocode/nominatim] failed for '{address}': {e}")
    return None, None, None


def geocode_full(address: str) -> tuple[float | None, float | None, str | None]:
    """
    Convert an address to (lat, lng, formatted_address).
    Tries Google Maps API first (if key present), falls back to Nominatim.
    Returns (None, None, None) on complete failure.
    """
    if not address:
        return None, None, None

    key = _normalize_address_key(address)

    if key in _geocode_mem:
        result = _geocode_mem[key]
        return (result[0], result[1], None) if result else (None, None, None)

    from core.db import get_geocode_cache, set_geocode_cache
    cached = get_geocode_cache(key)
    if cached:
        _geocode_mem[key] = cached
        return cached[0], cached[1], None

    lat, lng, formatted = None, None, None

    # 1. Google Maps API (preferred — handles noisy input well)
    if GOOGLE_MAPS_API_KEY:
        try:
            import requests
            resp = requests.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": address, "key": GOOGLE_MAPS_API_KEY},
                timeout=5,
            )
            data = resp.json()
            if data.get("status") == "OK" and data.get("results"):
                result = data["results"][0]
                loc = result["geometry"]["location"]
                lat, lng = float(loc["lat"]), float(loc["lng"])
                formatted = result.get("formatted_address", "")
        except Exception as e:
            print(f"  [geocode/maps] failed for '{address}': {e}")

    # 2. Nominatim fallback
    if lat is None:
        # Nominatim needs a clean address — append city if missing
        query = address if "san francisco" in address.lower() else f"{address}, San Francisco, CA"
        lat, lng, formatted = _geocode_nominatim(query)

    if lat is not None:
        _geocode_mem[key] = (lat, lng)
        set_geocode_cache(key, lat, lng)
        return lat, lng, formatted

    _geocode_mem[key] = None
    return None, None, None


def geocode_and_cache(address: str) -> tuple[float | None, float | None]:
    """Convert an address string to (lat, lng). Uses geocode_full internally."""
    lat, lng, _ = geocode_full(address)
    return lat, lng


def bounding_box() -> dict:
    """Return a lat/lng bounding box dict for use in API queries."""
    delta_lat = SEARCH_RADIUS_MILES / 69.0
    delta_lng = SEARCH_RADIUS_MILES / (69.0 * math.cos(math.radians(SEARCH_CENTER_LAT)))
    return {
        "north": SEARCH_CENTER_LAT + delta_lat,
        "south": SEARCH_CENTER_LAT - delta_lat,
        "east":  SEARCH_CENTER_LNG + delta_lng,
        "west":  SEARCH_CENTER_LNG - delta_lng,
    }
