"""
main.py — Pipeline orchestrator.
Run this on a cron every 15 minutes.

  python main.py           # run full pipeline once
  python main.py --daemon  # run continuously + keep webhook alive
"""

import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.db       import init_db, insert_listing, update_listing_status
from core.registry import match_listing_to_complex
from core.dedup    import is_duplicate
from core.scam     import score_listing as rule_based_score
from core.llm      import analyze_scam, enrich_listing, check_ollama
from notifications.email import send_alert, send_digest, start_webhook_server, clear_pending
from scrapers      import craigslist, zillow_email, redfin_email, apartments_email
from config        import USE_LLM, APPLICANT_PROFILE, PROFILES
from core.geo      import get_commute_minutes
import math as _math


def process_listing(listing: dict):
    """Run one listing through the full pipeline."""
    listing_id = listing["id"]

    # 1. Dedup
    if is_duplicate(listing):
        return

    # 2. Match to complex registry
    complex_id = match_listing_to_complex(listing)
    listing["complex_id"] = complex_id

    # 3. Scam check — LLM if available, rule-based fallback
    if USE_LLM:
        scam_score, scam_flags, verdict = analyze_scam(listing)
        if scam_score == 0 and not scam_flags:
            # LLM unavailable or parse failed — fall back to rules
            scam_score, scam_flags, verdict = rule_based_score(listing)
    else:
        scam_score, scam_flags, verdict = rule_based_score(listing)

    listing["scam_score"] = scam_score
    listing["scam_flags"] = scam_flags

    if verdict == "auto_reject":
        print(f"  [pipeline] Auto-rejected (score={scam_score}): {listing.get('title', '')[:50]}")
        listing["status"] = "skipped"
        insert_listing(listing)
        return

    # 4. LLM enrichment — extract neighborhood, pets, parking, etc.
    if USE_LLM and listing.get("description"):
        enriched = enrich_listing(listing)
        if enriched:
            listing.update({k: v for k, v in enriched.items() if v is not None})

    # 5. Filter: if registry has entries and this listing doesn't match, skip
    from core.db import get_all_complexes
    watching = get_all_complexes(status="watching")
    if watching and not complex_id:
        listing["status"] = "skipped"
        insert_listing(listing)
        return

    # 6. Save to DB
    listing["status"] = "new"
    insert_listing(listing)

    # 7. Generate cover letter for SMS INFO command (stored, not sent automatically)
    if USE_LLM and APPLICANT_PROFILE:
        from core.llm import generate_cover_letter
        cover = generate_cover_letter(listing, APPLICANT_PROFILE)
        if cover:
            listing["cover_letter"] = cover

    # 8. Send SMS alert
    print(f"  [pipeline] Alerting: {listing.get('title', '')[:60]} (score={scam_score})")

    def on_confirm():
        """Called when you reply Y."""
        try:
            from apply.bot import apply_to_listing
            apply_to_listing(listing)
        except Exception as e:
            print(f"  [apply] Error: {e}")
            update_listing_status(listing_id, "error")

    send_alert(
        listing,
        scam_score=scam_score,
        scam_flags=scam_flags,
        on_confirm=on_confirm,
        on_skip=lambda: None,
    )


def _passes_profile(listing: dict, profile: dict) -> bool:
    """Return True if the listing matches a profile's price/beds/geo criteria."""
    price = listing.get("price")
    beds  = listing.get("beds")
    lat   = listing.get("lat")
    lng   = listing.get("lng")

    if price:
        if profile.get("max_price") and price > profile["max_price"]:
            return False
        if profile.get("min_price") and price < profile["min_price"]:
            return False
    if beds is not None:
        if profile.get("max_beds") and beds > profile["max_beds"]:
            return False
        if profile.get("min_beds") and beds < profile["min_beds"]:
            return False

    # Geo filter — if geocoding failed (lat/lng None) allow through
    if lat is not None and lng is not None:
        R = 3958.8
        clat, clng = profile["center_lat"], profile["center_lng"]
        d_lat = _math.radians(lat - clat)
        d_lng = _math.radians(lng - clng)
        a = (_math.sin(d_lat/2)**2
             + _math.cos(_math.radians(clat)) * _math.cos(_math.radians(lat))
             * _math.sin(d_lng/2)**2)
        dist = R * 2 * _math.asin(_math.sqrt(a))
        if dist > profile.get("radius_miles", 0.5):
            return False

    return True


def run_pipeline():
    """Fetch all sources, then filter and alert per search profile."""
    print("\n── Running pipeline ──────────────────────────────")

    all_listings = []
    all_listings += craigslist.scrape()
    all_listings += zillow_email.scrape()
    all_listings += redfin_email.scrape()
    all_listings += apartments_email.scrape()

    # In-run dedup: collapse same building + same beds to cheapest unit
    from core.dedup import _normalize_address
    seen_keys: set = set()
    deduped = []
    for listing in sorted(all_listings, key=lambda x: (x.get("price") or 99999)):
        addr = _normalize_address(listing.get("address") or "").strip()
        beds = listing.get("beds")
        key = (addr, beds) if addr else None
        if key and key in seen_keys:
            continue
        if key:
            seen_keys.add(key)
        deduped.append(listing)
    if len(deduped) < len(all_listings):
        print(f"  [pipeline] In-run dedup: {len(all_listings)} → {len(deduped)} listings")
    all_listings = deduped

    # Process once per profile
    for profile in PROFILES:
        profile_listings = [l for l in all_listings if _passes_profile(l, profile)]
        recipients = profile.get("recipients")
        name = profile.get("name", "Search")
        print(f"\n[pipeline] Profile '{name}': {len(profile_listings)} listings → {recipients}")

        commute_dest = profile.get("commute_destination")
        for listing in profile_listings:
            try:
                # Add commute time if profile has a destination and listing has coords
                if commute_dest and listing.get("lat") and listing.get("lng"):
                    mins = get_commute_minutes(listing["lat"], listing["lng"], commute_dest)
                    if mins is not None:
                        listing["commute_minutes"] = mins
                        listing["commute_destination"] = commute_dest
                process_listing(listing)
            except Exception as e:
                print(f"  [pipeline] Error on {listing.get('id')}: {e}")

        send_digest(recipients=recipients)
        clear_pending()

    print(f"[pipeline] Done.\n")


def main():
    init_db()
    start_webhook_server()
    run_pipeline()
    # Keep process alive briefly so Flask can handle any Apply/Skip clicks
    # that arrive immediately after the digest email
    print("[main] Pipeline complete. Staying alive for 5 min for button clicks...")
    time.sleep(5 * 60)


if __name__ == "__main__":
    main()
