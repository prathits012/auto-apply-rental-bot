"""
core/geo.py — Geo helpers for radius filtering.
"""
from __future__ import annotations
import math
from config import SEARCH_CENTER_LAT, SEARCH_CENTER_LNG, SEARCH_RADIUS_MILES


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
