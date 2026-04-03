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
from notifications.email import send_alert, send_digest, start_webhook_server
from scrapers      import craigslist, redfin, apartments_com, padmapper
from config        import USE_LLM, APPLICANT_PROFILE


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


def run_pipeline():
    """Fetch all sources and process each listing."""
    print("\n── Running pipeline ──────────────────────────────")

    all_listings = []
    all_listings += craigslist.scrape()
    all_listings += redfin.scrape()
    all_listings += apartments_com.scrape()
    all_listings += padmapper.scrape()

    print(f"\n[pipeline] Processing {len(all_listings)} total listings...")
    for listing in all_listings:
        try:
            process_listing(listing)
        except Exception as e:
            print(f"  [pipeline] Error on {listing.get('id')}: {e}")

    send_digest()
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
