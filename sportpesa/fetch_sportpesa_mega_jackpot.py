"""
SportPesa Mega Jackpot Scraper  (v1)

Uses the direct jackpot-offer-api endpoint which returns all active jackpots
(Mega, Mid Week, etc.) with events embedded — no browser/Playwright needed.

Endpoints tried in order:
  1. https://jackpot-offer-api.ke.sportpesa.com/api/jackpots/active  (list)
  2. https://jackpot-offer-api.ke.sportpesa.com/api/jackpots          (fallback list)

The script identifies the Mega Jackpot by scanning jackpotTypes for "17/17"
with numberOfEvents == 17, or by matching a "mega" keyword in the jackpot id/type.
"""

import json
import os
import gzip
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Configuration ──────────────────────────────────────────────────────────────

# Primary endpoint — returns the active Mega Jackpot
API_ACTIVE   = "https://jackpot-offer-api.ke.sportpesa.com/api/jackpots/active"
# Fallback: the events endpoint used by the main site
API_ALL      = "https://www.ke.sportpesa.com/api/jackpots/events?type=regular"

OUTPUT_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cards")

# Mega Jackpot has 17 events; Mid Week has 13.
# Adjust if SportPesa changes the format.
MEGA_EVENT_COUNT = 17

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent":        UA,
    "Accept":            "application/json, text/plain, */*",
    "Accept-Language":   "en-US,en;q=0.9",
    "Accept-Encoding":   "gzip, deflate, br",
    "Referer":           "https://www.ke.sportpesa.com/en/jackpot",
    "Origin":            "https://www.ke.sportpesa.com",
    "Connection":        "keep-alive",
}

# ── HTTP helpers ───────────────────────────────────────────────────────────────

def fetch_json(url: str) -> dict | list | None:
    """Fetch JSON from a URL. Returns parsed data or None on failure."""
    import gzip
    print(f"  🔗  GET {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw_bytes = resp.read()
            # Decompress if gzip-encoded
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding == "gzip" or (raw_bytes[:2] == b"\x1f\x8b"):
                raw_bytes = gzip.decompress(raw_bytes)
            raw = raw_bytes.decode("utf-8", errors="replace")
            if not raw.strip():
                print(f"  ✗  Empty response body")
                return None
            data = json.loads(raw)
            print(f"  ✓  Response received ({len(raw):,} bytes, type={type(data).__name__})")
            return data
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        print(f"  ✗  HTTP {e.code} — {e.reason}  |  {body}")
    except urllib.error.URLError as e:
        print(f"  ✗  URL error: {e.reason}")
    except json.JSONDecodeError as e:
        print(f"  ✗  JSON decode error: {e}")
    except Exception as e:
        print(f"  ✗  Unexpected error: {e}")
    return None


# ── Jackpot selection ──────────────────────────────────────────────────────────

def is_mega_jackpot(jp: dict) -> bool:
    """
    Return True if this jackpot entry looks like the Mega Jackpot.
    Heuristics (in priority order):
      1. humanId field contains "mega" (case-insensitive)
      2. jackpotTypes list contains "17/17"
      3. numberOfEvents == MEGA_EVENT_COUNT  (17)
      4. The embedded events list has MEGA_EVENT_COUNT entries
    """
    jid = str(jp.get("id", "") or jp.get("humanId", "")).lower()
    if "mega" in jid:
        return True

    settings = jp.get("settings", {})
    types     = [t.lower() for t in settings.get("jackpotTypes", [])]
    if "17/17" in types:
        return True

    if settings.get("numberOfEvents") == MEGA_EVENT_COUNT:
        return True

    events = jp.get("events", [])
    if len(events) == MEGA_EVENT_COUNT:
        return True

    return False


def find_mega_jackpot(data) -> dict | None:
    """
    Given a response that is either a single jackpot dict or a list of jackpots,
    return the Mega Jackpot dict (or the first entry if only one exists).
    """
    if isinstance(data, list):
        print(f"  ℹ  Response is a list of {len(data)} jackpot(s)")
        mega = [jp for jp in data if is_mega_jackpot(jp)]
        if mega:
            print(f"  ✓  Found {len(mega)} Mega Jackpot candidate(s) — using first")
            return mega[0]
        # Fallback: pick the one with the most events
        if data:
            best = max(data, key=lambda jp: len(jp.get("events", [])))
            n    = len(best.get("events", []))
            print(f"  ⚠  No Mega Jackpot identified — using jackpot with most events ({n})")
            return best
        return None

    if isinstance(data, dict):
        print("  ℹ  Response is a single jackpot object")
        return data

    print(f"  ✗  Unexpected response type: {type(data)}")
    return None


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_events(jp: dict) -> list[dict]:
    """
    Parse the 'events' array from the jackpot object.
    Handles the jackpot-offer-api schema:
      event.competitors[].competitorName / isHome
      event.home / event.draw / event.away  (odds)
      event.utcKickOffTime
      event.tournamentName / countryName
      event.order
    """
    rows = []
    for event in jp.get("events", []):
        comps = event.get("competitors", [])
        home_team = next(
            (c["competitorName"] for c in comps if c.get("isHome")), "?"
        )
        away_team = next(
            (c["competitorName"] for c in comps if not c.get("isHome")), "?"
        )

        # Kick-off: convert UTC ISO to local-friendly string
        raw_ko = event.get("utcKickOffTime", "")
        try:
            ko_dt  = datetime.fromisoformat(raw_ko.replace("Z", "+00:00"))
            kickoff = ko_dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            kickoff = raw_ko

        rows.append({
            "order":        event.get("order", ""),
            "event_id":     event.get("id", ""),
            "home":         home_team,
            "away":         away_team,
            "tournament":   event.get("tournamentName", ""),
            "country":      event.get("countryName", ""),
            "kickoff":      kickoff,
            "home_odd":     event.get("home", "-"),
            "draw_odd":     event.get("draw", "-"),
            "away_odd":     event.get("away", "-"),
            "betting_open": event.get("bettingStatus", "") == "Open",
            "score":        event.get("score"),
        })

    rows.sort(key=lambda r: r["order"] if isinstance(r["order"], int) else 0)
    return rows


# ── Output ─────────────────────────────────────────────────────────────────────

def save_matches(rows: list[dict], raw_jp: dict) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    raw_path    = os.path.join(OUTPUT_DIR, f"mega_jackpot_raw_{now}.json")
    parsed_path = os.path.join(OUTPUT_DIR, f"mega_jackpot_parsed_{now}.json")

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_jp, f, indent=2, ensure_ascii=False)
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"\n  💾  Raw    → {raw_path}")
    print(f"  💾  Parsed → {parsed_path}")

    # ── Pretty table ──────────────────────────────────────────────────────────
    col_h = max((len(r["home"]) for r in rows), default=10)
    col_a = max((len(r["away"]) for r in rows), default=10)
    col_h = max(col_h, 6)
    col_a = max(col_a, 6)

    header = (
        f"\n  {'#':<4} "
        f"{'Home':<{col_h}} "
        f"{'Away':<{col_a}} "
        f"{'1':>6} {'X':>6} {'2':>6}  "
        f"{'Tournament':<20} "
        f"Kick-off (UTC)"
    )
    print(header)
    print("  " + "─" * (len(header) - 2))

    for r in rows:
        status = "" if r["betting_open"] else " 🔒"
        score  = f"  [{r['score']}]" if r["score"] else ""
        print(
            f"  {str(r['order']):<4} "
            f"{r['home']:<{col_h}} "
            f"{r['away']:<{col_a}} "
            f"{str(r['home_odd']):>6} "
            f"{str(r['draw_odd']):>6} "
            f"{str(r['away_odd']):>6}  "
            f"{r['tournament']:<20} "
            f"{r['kickoff']}"
            f"{score}{status}"
        )


# ── Main ───────────────────────────────────────────────────────────────────────

def scrape():
    print(f"\n{'='*65}")
    print("  SportPesa Mega Jackpot Scraper  v1")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*65}\n")

    jackpot = None

    # ── Step 1: try the /active endpoint (single or list) ─────────────────────
    print("── Step 1: /api/jackpots/active ──────────────────────────────────")
    data = fetch_json(API_ACTIVE)
    if data is not None:
        jackpot = find_mega_jackpot(data)

    # ── Step 2: fallback to /api/jackpots list ────────────────────────────────
    if jackpot is None:
        print("\n── Step 2: /api/jackpots (fallback list) ─────────────────────────")
        data = fetch_json(API_ALL)
        if data is not None:
            jackpot = find_mega_jackpot(data)

    if jackpot is None:
        print("\n❌  Could not retrieve Mega Jackpot data.")
        print("    Possible reasons:")
        print("    • The API requires session cookies — try adding Cookie headers")
        print("    • The endpoint URL has changed — inspect network traffic on")
        print("      https://www.ke.sportpesa.com/en/jackpot and update API_ACTIVE")
        print("    • There is no active Mega Jackpot at this time")
        return

    events = jackpot.get("events", [])
    if not events:
        print("\n⚠  Jackpot found but contains 0 events. Raw jackpot data:")
        print(json.dumps(jackpot, indent=2)[:1000])
        return

    print(f"\n  ✓  Mega Jackpot #{jackpot.get('humanId', '?')} — {len(events)} event(s)")
    print(f"     Betting status : {jackpot.get('bettingStatus', '?')}")
    settings = jackpot.get("settings", {})
    print(f"     Prize tiers    : {', '.join(settings.get('jackpotTypes', []))}")

    rows = parse_events(jackpot)
    save_matches(rows, jackpot)

    # ── Upload card to Supabase so Streamlit Cloud can read it ────────────────
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from db import save_card
        ok = save_card("sportpesa", rows, jackpot)
        print(f"  {'✓' if ok else '✗'}  Supabase card upload {'succeeded' if ok else 'failed'}")
    except Exception as e:
        print(f"  ✗  Supabase card upload error: {e}")

    # ── Save to rounds/ if the round is settled (all events have scores) ──────
    if all(r.get("score") for r in rows):
        def _pick(score_str: str) -> str:
            try:
                h, a = (int(x) for x in str(score_str).replace("-", ":").split(":"))
                return "1" if h > a else ("X" if h == a else "2")
            except Exception:
                return ""

        finished_at = (
            jackpot.get("settledAt")
            or jackpot.get("finishedAt")
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )

        data = {
            "jackpot_human_id":    jackpot.get("humanId", jackpot.get("id", "unknown")),
            "status":              jackpot.get("bettingStatus", "settled"),
            "finished_at":         finished_at,
            "num_games":           len(rows),
            "winning_combination": [_pick(r["score"]) for r in rows],
            "games": [
                {
                    "game_num": r["order"],
                    "home":     r["home"],
                    "away":     r["away"],
                    "score":    str(r["score"]).replace("-", ":"),
                    "pick":     _pick(r["score"]),
                }
                for r in rows
            ],
        }

        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from round_to_results import save_mega_round
        round_path = save_mega_round(data)
        print(f"  💾  Round  → {round_path}")
    else:
        print("  ℹ  Round not yet settled — skipping rounds/ save.")

    print("\n✅  Done.\n")


if __name__ == "__main__":
    scrape()