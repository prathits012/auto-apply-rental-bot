"""
apply/bot.py — Playwright application automation.
Called after you reply Y to an SMS alert.
This is a stub — each platform needs its own apply flow.
"""

from playwright.sync_api import sync_playwright


def apply_to_listing(listing: dict):
    """Route to the correct apply function based on source."""
    source = listing.get("source", "")
    print(f"[apply] Starting application for {listing.get('title', '')[:60]}")

    if source == "craigslist":
        _apply_craigslist(listing)
    elif source == "rentcast":
        _apply_generic(listing)
    else:
        print(f"[apply] No apply handler for source '{source}' — opening URL for manual apply")
        _open_url(listing.get("url", ""))


def _apply_craigslist(listing: dict):
    """Craigslist: reply via email."""
    # TODO: extract reply email from listing detail page and send templated email
    print(f"[apply] Craigslist email apply — URL: {listing.get('url', '')}")


def _apply_generic(listing: dict):
    """Open the listing in a browser for semi-manual apply."""
    url = listing.get("url", "")
    if not url:
        print("[apply] No URL — cannot apply")
        return
    print(f"[apply] Opening: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)   # visible so you can intervene
        page = browser.new_page()
        page.goto(url)
        input("[apply] Press Enter when done to close browser...")
        browser.close()


def _open_url(url: str):
    import subprocess, sys
    if sys.platform == "darwin":
        subprocess.run(["open", url])
    else:
        subprocess.run(["xdg-open", url])
