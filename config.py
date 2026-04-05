"""
config.py — All secrets loaded from environment variables.
For local dev: copy .env.example to .env and fill in values.
For Railway: set these in the Railway dashboard under Variables.
"""

import os
from dotenv import load_dotenv
load_dotenv()

def _req(key):
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val

def _opt(key, default=None):
    return os.environ.get(key, default)

# ── Email (Gmail SMTP) ────────────────────────────────────────
EMAIL_FROM     = _req("EMAIL_FROM")
EMAIL_PASSWORD = _req("EMAIL_PASSWORD")
EMAIL_TO       = _req("EMAIL_TO")   # comma-separated for multiple recipients

# ── Google Maps ───────────────────────────────────────────────
GOOGLE_MAPS_API_KEY = _opt("GOOGLE_MAPS_API_KEY", "")

# ── Scraper settings ──────────────────────────────────────────
CRAIGSLIST_CITY   = _opt("CRAIGSLIST_CITY", "sfbay")
CRAIGSLIST_REGION = _opt("CRAIGSLIST_REGION", "sfc")

MAX_LISTING_AGE_HOURS = int(_opt("MAX_LISTING_AGE_HOURS", "24"))

# ── Apartments.com ───────────────────────────────────────────
APARTMENTS_COM_CITY      = _opt("APARTMENTS_COM_CITY", "san-francisco-ca")
APARTMENTS_COM_MAX_PAGES = int(_opt("APARTMENTS_COM_MAX_PAGES", "3"))
APARTMENTS_COM_HEADLESS  = _opt("APARTMENTS_COM_HEADLESS", "true").lower() == "true"

# ── Search area (center + radius) ─────────────────────────────
# Center: 4th & King Caltrain Station, SF
SEARCH_CENTER_LAT   = float(_opt("SEARCH_CENTER_LAT", "37.7764"))
SEARCH_CENTER_LNG   = float(_opt("SEARCH_CENTER_LNG", "-122.3947"))
SEARCH_RADIUS_MILES = float(_opt("SEARCH_RADIUS_MILES", "0.5"))

# ── Filter criteria ───────────────────────────────────────────
FILTERS = {
    "max_price":   int(_opt("FILTER_MAX_PRICE", "4500")),
    "min_price":   int(_opt("FILTER_MIN_PRICE", "2500")),
    "min_beds":    int(_opt("FILTER_MIN_BEDS", "1")),
    "max_beds":    int(_opt("FILTER_MAX_BEDS", "2")),
    "min_baths":   int(_opt("FILTER_MIN_BATHS", "1")),
    "min_sqft":    int(_opt("FILTER_MIN_SQFT", "0")),
    "require_pets":    _opt("FILTER_REQUIRE_PETS", "false").lower() == "true",
    "require_parking": _opt("FILTER_REQUIRE_PARKING", "false").lower() == "true",
    "require_laundry": _opt("FILTER_REQUIRE_LAUNDRY", "false").lower() == "true",
}

# ── Scam detection ────────────────────────────────────────────
SCAM_THRESHOLDS = {
    "auto_reject": int(_opt("SCAM_AUTO_REJECT", "50")),
    "flag":        int(_opt("SCAM_FLAG", "20")),
    "price_ratio": float(_opt("SCAM_PRICE_RATIO", "0.70")),
}

# ── Confirmation timeout ──────────────────────────────────────
SMS_CONFIRM_TIMEOUT_MINUTES = int(_opt("CONFIRM_TIMEOUT_MINUTES", "15"))

# ── Webhook server ────────────────────────────────────────────
WEBHOOK_PORT = int(_opt("PORT", "5001"))   # Railway injects PORT automatically
WEBHOOK_HOST = "0.0.0.0"

# ── LLM (Ollama) ─────────────────────────────────────────────
OLLAMA_MODEL = _opt("OLLAMA_MODEL", "llama3.1")
OLLAMA_HOST  = _opt("OLLAMA_HOST", "http://localhost:11434")
USE_LLM      = _opt("USE_LLM", "false").lower() == "true"  # off by default on server

# ── Applicant profile ─────────────────────────────────────────
APPLICANT_PROFILE = _opt("APPLICANT_PROFILE", "Software engineer, no pets, non-smoker, stable income, flexible move-in date")

# ── Database ──────────────────────────────────────────────────
DB_PATH = _opt("DB_PATH", "data/rentals.db")

# ── Complex registry ──────────────────────────────────────────
GEOCODE_MATCH_METERS  = int(_opt("GEOCODE_MATCH_METERS", "50"))
FUZZY_MATCH_THRESHOLD = int(_opt("FUZZY_MATCH_THRESHOLD", "85"))
