from __future__ import annotations
"""
core/db.py — SQLite setup and all database queries.
Single source of truth for schema + data access.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from config import DB_PATH


def get_conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS complexes (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            address     TEXT NOT NULL,
            lat         REAL,
            lng         REAL,
            aliases     TEXT DEFAULT '[]',   -- JSON array
            mgmt_phone  TEXT,
            mgmt_email  TEXT,
            status      TEXT DEFAULT 'watching',  -- watching|applied|paused
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS listings (
            id          TEXT PRIMARY KEY,   -- "{source}_{source_id}"
            complex_id  TEXT REFERENCES complexes(id),
            source      TEXT NOT NULL,      -- craigslist|redfin|apartments|rentcast
            url         TEXT,
            title       TEXT,
            address     TEXT,
            price       INTEGER,
            beds        REAL,
            baths       REAL,
            sqft        INTEGER,
            lat         REAL,
            lng         REAL,
            description TEXT,
            image_urls  TEXT DEFAULT '[]',  -- JSON array
            listed_at   TEXT,
            seen_at     TEXT DEFAULT (datetime('now')),
            status      TEXT DEFAULT 'new', -- new|alerted|applied|skipped|expired
            scam_score  INTEGER DEFAULT 0,
            scam_flags  TEXT DEFAULT '[]'   -- JSON array of triggered signals
        );

        CREATE TABLE IF NOT EXISTS applications (
            id          TEXT PRIMARY KEY,
            listing_id  TEXT REFERENCES listings(id),
            submitted_at TEXT,
            status      TEXT DEFAULT 'pending',  -- pending|submitted|error
            notes       TEXT
        );

        CREATE TABLE IF NOT EXISTS sms_log (
            id          TEXT PRIMARY KEY,
            direction   TEXT,   -- outbound|inbound
            body        TEXT,
            listing_id  TEXT,
            sent_at     TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_listings_complex ON listings(complex_id);
        CREATE INDEX IF NOT EXISTS idx_listings_status  ON listings(status);
        CREATE INDEX IF NOT EXISTS idx_listings_seen    ON listings(seen_at);
        """)


# ── Complexes ─────────────────────────────────────────────────

def upsert_complex(data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO complexes (id, name, address, lat, lng, aliases, status)
            VALUES (:id, :name, :address, :lat, :lng, :aliases, :status)
            ON CONFLICT(id) DO UPDATE SET
                lat=excluded.lat, lng=excluded.lng,
                aliases=excluded.aliases
        """, {**data, "aliases": json.dumps(data.get("aliases", []))})


def get_all_complexes(status: str = None) -> list:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM complexes WHERE status=?", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM complexes").fetchall()
    result = [dict(r) for r in rows]
    for r in result:
        r["aliases"] = json.loads(r["aliases"] or "[]")
    return result


def update_complex_status(complex_id: str, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE complexes SET status=? WHERE id=?", (status, complex_id)
        )


# ── Listings ──────────────────────────────────────────────────

def listing_exists(listing_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM listings WHERE id=?", (listing_id,)
        ).fetchone()
    return row is not None


def insert_listing(data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO listings
                (id, complex_id, source, url, title, address,
                 price, beds, baths, sqft, lat, lng, description,
                 image_urls, listed_at, status, scam_score, scam_flags)
            VALUES
                (:id, :complex_id, :source, :url, :title, :address,
                 :price, :beds, :baths, :sqft, :lat, :lng, :description,
                 :image_urls, :listed_at, :status, :scam_score, :scam_flags)
        """, {
            "complex_id":  data.get("complex_id"),
            "source":      data.get("source", ""),
            "url":         data.get("url", ""),
            "title":       data.get("title", ""),
            "address":     data.get("address", ""),
            "price":       data.get("price"),
            "beds":        data.get("beds"),
            "baths":       data.get("baths"),
            "sqft":        data.get("sqft"),
            "lat":         data.get("lat"),
            "lng":         data.get("lng"),
            "description": data.get("description", ""),
            "image_urls":  json.dumps(data.get("image_urls", [])),
            "listed_at":   data.get("listed_at"),
            "status":      data.get("status", "new"),
            "scam_score":  data.get("scam_score", 0),
            "scam_flags":  json.dumps(data.get("scam_flags", [])),
            "id":          data["id"],
        })


def update_listing_status(listing_id: str, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE listings SET status=? WHERE id=?", (status, listing_id)
        )


def get_listing(listing_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM listings WHERE id=?", (listing_id,)
        ).fetchone()
    if not row:
        return None
    r = dict(row)
    r["image_urls"] = json.loads(r["image_urls"] or "[]")
    r["scam_flags"] = json.loads(r["scam_flags"] or "[]")
    return r


def get_pending_listings() -> list:
    """Listings that need SMS confirmation."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM listings WHERE status='alerted'"
        ).fetchall()
    return [dict(r) for r in rows]


# ── SMS log ───────────────────────────────────────────────────

def log_sms(direction: str, body: str, listing_id: str = None):
    import uuid
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sms_log (id, direction, body, listing_id) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), direction, body, listing_id)
        )
