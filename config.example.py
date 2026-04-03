"""
config.py — copy this to config.py and fill in your values.
NEVER commit config.py to git. It's in .gitignore.
"""

# ── Twilio (SMS) ──────────────────────────────────────────────
TWILIO_ACCOUNT_SID = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TWILIO_AUTH_TOKEN  = "your_auth_token"
TWILIO_FROM_NUMBER = "+14155550100"   # your Twilio number
YOUR_PHONE_NUMBER  = "+14155550199"   # your real mobile number

# ── Google Maps (geocoding + address verification) ────────────
GOOGLE_MAPS_API_KEY = "your_google_maps_key"

# ── Scraper settings ──────────────────────────────────────────
CRAIGSLIST_CITY   = "sfbay"              # CL subdomain
CRAIGSLIST_REGION = "sfc"               # SF specific

# How old a listing can be before we ignore it (hours)
MAX_LISTING_AGE_HOURS = 24

# ── Filter criteria ───────────────────────────────────────────
FILTERS = {
    "max_price":   5500,   # USD/month
    "min_price":   1500,
    "min_beds":    1,      # 0 = studio OK
    "min_baths":   1,
    "min_sqft":    500,    # set to 0 to disable
    "neighborhoods": [     # leave empty [] to allow all SF
        "SoMa", "Noe Valley", "Mission", "Hayes Valley",
        "Castro", "Potrero Hill", "Bernal Heights", "NOPA"
    ],
    "require_pets":    False,
    "require_parking": False,
    "require_laundry": False,
}

# ── Scam detection ────────────────────────────────────────────
SCAM_THRESHOLDS = {
    "auto_reject": 50,   # silent drop, no SMS
    "flag":        20,   # SMS with warning, require Y+confirm
    "price_ratio": 0.70, # flag if price < 70% of neighborhood median
}

# ── SMS confirmation ──────────────────────────────────────────
SMS_CONFIRM_TIMEOUT_MINUTES = 15   # no reply = skip

# ── Webhook server (receives your SMS replies) ────────────────
WEBHOOK_PORT = 5001
WEBHOOK_HOST = "0.0.0.0"

# ── Local LLM (Ollama) ───────────────────────────────────────
# Setup: brew install ollama && ollama pull llama3.1 && ollama serve
#
# Model recommendations:
#   8GB  Mac  → "llama3.2"   (3B, Q4_K_M, ~2.5 GB RAM)
#   16GB Mac  → "llama3.1"   (8B, Q4_K_M, ~4.8 GB RAM)  ← default
#   32GB+ Mac → "llama3.1"   (8B, Q8_0,   ~8.5 GB RAM)  set via: ollama pull llama3.1:8b-instruct-q8_0
#
OLLAMA_MODEL = "llama3.1"       # change to "llama3.2" on 8GB machines
OLLAMA_HOST  = "http://localhost:11434"

# Set to False to skip LLM and use rule-based scam detection only
# (useful if Ollama isn't running or you want faster pipeline)
USE_LLM = True

# Your applicant profile — used for cover letter generation
APPLICANT_PROFILE = "Software engineer, no pets, non-smoker, stable income, flexible move-in date"

# ── Database ──────────────────────────────────────────────────
DB_PATH = "data/rentals.db"

# ── Complex registry proximity match ─────────────────────────
GEOCODE_MATCH_METERS = 50   # listings within 50m = same complex
FUZZY_MATCH_THRESHOLD = 85  # 0-100, higher = stricter name match
