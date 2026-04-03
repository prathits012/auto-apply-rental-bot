"""
core/dedup.py — Cross-source deduplication.
Same unit posted by multiple agents on multiple sites = one alert.
"""

import math
from core.db import get_conn


def _haversine_meters(lat1, lng1, lat2, lng2) -> float:
    R = 6_371_000
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _normalize_address(addr: str) -> str:
    """Lowercase, strip unit/apt noise for fuzzy address matching."""
    import re
    addr = addr.lower()
    addr = re.sub(r'\b(apt|unit|#|suite|ste|floor|fl)\.?\s*\w+', '', addr)
    addr = re.sub(r'\bst\b', 'street', addr)
    addr = re.sub(r'\bave\b', 'avenue', addr)
    addr = re.sub(r'\bblvd\b', 'boulevard', addr)
    addr = re.sub(r'\bdr\b', 'drive', addr)
    addr = re.sub(r'[^a-z0-9\s]', ' ', addr)
    return ' '.join(addr.split())


def is_duplicate(listing: dict) -> bool:
    """
    Returns True if we've already seen this listing recently.

    Checks (in order):
      1. Exact ID match
      2. Same complex + price + beds within 24h
      3. Geo-proximity: within 50m + same price + beds within 48h
         (catches same unit listed on Redfin vs Padmapper with different addresses)
      4. Normalized address + price within 48h
    """
    with get_conn() as conn:

        # 1. Exact ID
        if conn.execute("SELECT 1 FROM listings WHERE id=?", (listing["id"],)).fetchone():
            return True

        # 2. Same complex + price + beds within 24h
        if listing.get("complex_id") and listing.get("price") and listing.get("beds"):
            row = conn.execute("""
                SELECT 1 FROM listings
                WHERE complex_id=? AND price=? AND beds=?
                  AND status != 'expired'
                  AND seen_at > datetime('now', '-24 hours')
            """, (listing["complex_id"], listing["price"], listing["beds"])).fetchone()
            if row:
                return True

        # 3. Geo-proximity: same price+beds, coords within 50m
        lat, lng = listing.get("lat"), listing.get("lng")
        price, beds = listing.get("price"), listing.get("beds")
        if lat and lng and price and beds is not None:
            # Pull nearby listings (rough bbox first for speed, then exact distance)
            delta = 0.0005  # ~55m in degrees
            nearby = conn.execute("""
                SELECT lat, lng FROM listings
                WHERE price=? AND beds=?
                  AND lat BETWEEN ? AND ?
                  AND lng BETWEEN ? AND ?
                  AND status != 'expired'
                  AND seen_at > datetime('now', '-48 hours')
            """, (price, beds, lat - delta, lat + delta, lng - delta, lng + delta)).fetchall()
            for row in nearby:
                if row["lat"] and row["lng"]:
                    dist = _haversine_meters(lat, lng, row["lat"], row["lng"])
                    if dist <= 50:
                        return True

        # 4. Normalized address + price within 48h
        if listing.get("address") and price:
            norm = _normalize_address(listing["address"])
            rows = conn.execute("""
                SELECT address FROM listings
                WHERE price=?
                  AND status != 'expired'
                  AND seen_at > datetime('now', '-48 hours')
            """, (price,)).fetchall()
            for row in rows:
                if row["address"] and _normalize_address(row["address"]) == norm:
                    return True

    return False
