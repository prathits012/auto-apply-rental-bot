# SF Rental Bot

Automated rental listing aggregator, scam detector, and application bot for San Francisco.

## Features
- Aggregates listings from Craigslist, Redfin, Apartments.com, and RentCast (Zillow proxy)
- Matches listings against your personal complex registry by address proximity + fuzzy name
- Scam detection scoring (price anomaly, duplicate photos, address verification, keyword flags)
- SMS confirmation via Twilio before any application is submitted
- Headless application automation via Playwright

## Project structure
```
sf_rental_bot/
├── core/
│   ├── db.py              # SQLite setup + all queries
│   ├── registry.py        # Complex registry: add, match, geocode
│   ├── dedup.py           # Cross-source deduplication
│   └── scam.py            # Scam scoring engine
├── scrapers/
│   ├── craigslist.py      # RSS + HTML scraper
│   ├── redfin.py          # Redfin headless scraper
│   ├── apartments.py      # Apartments.com headless scraper
│   └── rentcast.py        # RentCast API client
├── notifications/
│   └── sms.py             # Twilio SMS send + webhook receiver
├── apply/
│   └── bot.py             # Playwright application automation
├── data/
│   └── rentals.db         # SQLite database (auto-created)
├── config.py              # All settings + API keys (never commit)
├── main.py                # Orchestrator — runs the full pipeline
└── requirements.txt
```

## Quickstart
```bash
# 1. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 2. Copy and fill in your config
cp config.example.py config.py

# 3. Set up your complex watchlist
python -m core.registry add "The Avery SF" "488 Folsom St, San Francisco, CA"

# 4. Run once manually
python main.py

# 5. Set up cron (every 15 min)
*/15 * * * * cd /path/to/sf_rental_bot && python main.py >> logs/cron.log 2>&1
```

## SMS commands
- `Y` — proceed with auto-apply
- `N` — skip this listing
- `INFO` — get full listing details
- `STOP` — pause all alerts
