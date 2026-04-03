from __future__ import annotations
"""
core/llm.py — Local LLM analysis via Ollama.
Runs Llama 3.1 8B (Q4_K_M) entirely on your Mac. No API calls, no data leaves your machine.

Setup (one-time):
    brew install ollama
    ollama pull llama3.1          # pulls Q4_K_M by default (~4.8 GB)
    ollama serve                  # starts background server on localhost:11434

For 8GB MacBook, use the 3B model instead:
    ollama pull llama3.2          # ~2.5 GB
    # then set MODEL = "llama3.2" in config.py
"""

import json
import requests
from config import OLLAMA_MODEL, OLLAMA_HOST

OLLAMA_URL = f"{OLLAMA_HOST}/api/generate"


def _call(prompt: str, max_tokens: int = 300) -> str | None:
    """Raw call to local Ollama server. Returns response text or None."""
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.0,      # deterministic — we want consistent scoring
            "top_p": 1.0,
        }
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except requests.ConnectionError:
        print("[llm] Ollama not running. Start it with: ollama serve")
        return None
    except Exception as e:
        print(f"[llm] Error: {e}")
        return None


def _parse_json(text: str) -> dict | None:
    """Extract JSON from model output, tolerating extra prose."""
    if not text:
        return None
    # Find the first { ... } block
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


# ── Scam analysis ─────────────────────────────────────────────

SCAM_PROMPT = """\
You are a rental scam detector for San Francisco listings.

Analyze this listing and return ONLY a JSON object — no explanation, no markdown.

Listing:
---
{text}
---

Return this exact JSON structure:
{{
  "scam_score": <integer 0-100>,
  "verdict": "<pass|flag|auto_reject>",
  "flags": ["<short signal description>", ...],
  "reasoning": "<one sentence max>"
}}

Scoring guide:
- 0-20:  Looks legitimate
- 21-49: Suspicious, needs review
- 50+:   Likely scam, flag for user
- auto_reject: Wire transfer, gift cards, or obvious fraud — never show user

Key scam signals to look for:
- Payment via wire, Zelle, gift cards, Bitcoin
- Owner claims to be overseas / missionary / deployed
- Price significantly below SF market (1BD < $2000, 2BD < $2800)
- Urgency pressure ("must decide today", "many applicants")
- Asks to skip showing / "key by mail"
- Generic template language that doesn't describe the actual unit
- Requests personal info before any viewing
"""


def analyze_scam(listing: dict) -> tuple[int, list[str], str]:
    """
    Use local LLM to analyze listing text for scam signals.
    Returns (score, flags, verdict) — same interface as rule-based scam.py.
    Falls back to (0, [], 'pass') if Ollama unavailable.
    """
    text = "\n".join(filter(None, [
        f"Title: {listing.get('title', '')}",
        f"Price: ${listing.get('price', 'unknown')}/mo",
        f"Beds: {listing.get('beds', '?')}  Baths: {listing.get('baths', '?')}",
        f"Address: {listing.get('address', '')}",
        f"Description:\n{listing.get('description', '')[:1500]}",  # cap at 1500 chars
    ]))

    raw = _call(SCAM_PROMPT.format(text=text), max_tokens=250)
    result = _parse_json(raw)

    if not result:
        print(f"[llm] scam parse failed — raw: {raw[:100] if raw else 'no response'}")
        return 0, [], "pass"

    score   = int(result.get("scam_score", 0))
    flags   = result.get("flags", [])
    verdict = result.get("verdict", "pass")

    print(f"[llm] Scam analysis: score={score} verdict={verdict} flags={flags}")
    return score, flags, verdict


# ── Listing enrichment ────────────────────────────────────────

ENRICH_PROMPT = """\
You are parsing a San Francisco rental listing.

Extract structured data and return ONLY a JSON object — no explanation, no markdown.

Listing:
---
{text}
---

Return this exact JSON structure:
{{
  "neighborhood": "<SF neighborhood name or null>",
  "pet_friendly": <true|false|null>,
  "parking": <true|false|null>,
  "laundry": "<in-unit|in-building|none|null>",
  "furnished": <true|false|null>,
  "lease_term": "<month-to-month|12-month|6-month|null>",
  "available_date": "<YYYY-MM-DD or null>",
  "highlights": ["<1 key feature>", "<1 key feature>"],
  "concerns": ["<any red flags about the unit itself, not scam>"]
}}

Only include what's clearly stated. Use null for anything not mentioned.
SF neighborhoods: SoMa, Mission, Castro, Noe Valley, Hayes Valley, Potrero Hill,
Bernal Heights, NOPA, Richmond, Sunset, Marina, Pacific Heights, Tenderloin, etc.
"""


def enrich_listing(listing: dict) -> dict:
    """
    Use local LLM to extract structured fields from listing description.
    Returns a dict of enriched fields to merge into the listing.
    Returns {} if Ollama unavailable.
    """
    text = "\n".join(filter(None, [
        f"Title: {listing.get('title', '')}",
        f"Address: {listing.get('address', '')}",
        f"Description:\n{listing.get('description', '')[:2000]}",
    ]))

    raw = _call(ENRICH_PROMPT.format(text=text), max_tokens=300)
    result = _parse_json(raw)

    if not result:
        print(f"[llm] enrich parse failed")
        return {}

    print(f"[llm] Enriched: neighborhood={result.get('neighborhood')} "
          f"pets={result.get('pet_friendly')} parking={result.get('parking')}")
    return result


# ── Cover letter generator ────────────────────────────────────

COVER_LETTER_PROMPT = """\
Write a short, genuine rental application message for this San Francisco apartment.

Listing details:
{listing_summary}

Applicant profile:
{applicant_profile}

Requirements:
- 3-4 sentences max
- Warm but professional tone
- Mention 1-2 specific details from the listing to show genuine interest
- End with a request to schedule a viewing
- No filler phrases like "I am writing to express my interest"
- Do NOT mention price negotiation

Return only the message text, no subject line, no JSON.
"""


def generate_cover_letter(listing: dict, applicant_profile: str) -> str | None:
    """
    Generate a personalized cover letter for a specific listing.
    applicant_profile: short string describing the applicant.
      e.g. "Software engineer, no pets, non-smoker, looking to move Feb 1st"
    """
    listing_summary = "\n".join(filter(None, [
        f"Title: {listing.get('title', '')}",
        f"Neighborhood: {listing.get('neighborhood', listing.get('address', ''))}",
        f"Price: ${listing.get('price', '?')}/mo",
        f"Description excerpt: {(listing.get('description') or '')[:500]}",
    ]))

    raw = _call(
        COVER_LETTER_PROMPT.format(
            listing_summary=listing_summary,
            applicant_profile=applicant_profile,
        ),
        max_tokens=200,
    )

    if not raw:
        return None

    print(f"[llm] Cover letter generated ({len(raw)} chars)")
    return raw


# ── Health check ──────────────────────────────────────────────

def check_ollama() -> bool:
    """Returns True if Ollama is running and the model is available."""
    try:
        resp = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        available = any(OLLAMA_MODEL in m for m in models)
        if not available:
            print(f"[llm] Model '{OLLAMA_MODEL}' not found. Run: ollama pull {OLLAMA_MODEL}")
        return available
    except Exception:
        print(f"[llm] Ollama not reachable at {OLLAMA_HOST}. Run: ollama serve")
        return False
