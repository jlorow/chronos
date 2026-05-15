"""
SportPesa Mid Week Jackpot Scraper  (v4 - pure HTTP, no browser needed)

Uses the same jackpot-offer-api endpoint as the Mega Jackpot scraper.
Identifies the Mid Week Jackpot by its 13-event count (vs 17 for Mega).
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Configuration ──────────────────────────────────────────────────────────────

API_ACTIVE = "https://jackpot-offer-api.ke.sportpesa.com/api/jackpots/active"
API_ALL    = "https://jackpot-offer-api.ke.sportpesa.com/api/jackpots"

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cards")

MIDWEEK_EVENT_COUNT = 13

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent":      UA,
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.ke.sportpesa.com/en/jackpot",
    "Origin":          "https://www.ke.sportpesa.com",
}

# ── HTTP helpers ───────────────────────────────────────────────────────────────

def fetch_json(url: str):
    print(f"  🔗  GET {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw  = resp.read().decode("utf-8")
            data = json.loads(raw)
            print(f"  ✓  Response received ({len(raw):,} bytes, type={type(data).__name__})")
            return data
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:300]
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

def is_midweek_jackpot(jp: dict) -> bool:
    jid = str(jp.get("id", "") or jp.get("humanId", "")).lower()
    if "mid" in jid or "midweek" in jid or "mid_week" in jid:
        return True

    settings = jp.get("settings", {})
    types = [t.lower() for t in settings.get("jackpotTypes", [])]
    if "13/13" in types:
        return True

    if settings.get("numberOfEvents") == MIDWEEK_EVENT_COUNT:
        return True

    if len(jp.get("events", [])) == MIDWEEK_EVENT_COUNT:
        return True

    return False


def find_midweek_jackpot(data):
    if isinstance(data, list):
        print(f"  ℹ  Response is a list of {len(data)} jackpot(s)")
        midweek = [jp for jp in data if is_midweek_jackpot(jp)]
        if midweek:
            print(f"  ✓  Found {len(midweek)} Mid Week Jackpot candidate(s) — using first")
            return midweek[0]
        # Fallback: pick the one closest to 13 events that isn't 17
        non_mega = [jp for jp in data if len(jp.get("events", [])) != 17]
        if non_mega:
            best = max(non_mega, key=lambda jp: len(jp.get("events", [])))
            print(f"  ⚠  No Mid Week Jackpot identified — using non-mega jackpot with {len(best.get('events', []))} events")
            return best
        return None

    if isinstance(data, dict):
        print("  ℹ  Response is a single jackpot object")
        return data

    print(f"  ✗  Unexpected response type: {type(data)}")
    return None


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_events(jp: dict) -> list:
    rows = []
    for event in jp.get("events", []):
        comps = event.get("competitors", [])
        home_team = next((c["competitorName"] for c in comps if c.get("isHome")), "?")
        away_team = next((c["competitorName"] for c in comps if not c.get("isHome")), "?")

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

def save_matches(rows: list, raw_jp: dict) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    raw_path    = os.path.join(OUTPUT_DIR, f"jackpot_raw_{now}.json")
    parsed_path = os.path.join(OUTPUT_DIR, f"jackpot_parsed_{now}.json")

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_jp, f, indent=2, ensure_ascii=False)
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"\n  💾  Raw    → {raw_path}")
    print(f"  💾  Parsed → {parsed_path}")

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
    print("  SportPesa Mid Week Jackpot Scraper  v4")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*65}\n")

    jackpot = None

    print("── Step 1: /api/jackpots/active ──────────────────────────────────")
    data = fetch_json(API_ACTIVE)
    if data is not None:
        jackpot = find_midweek_jackpot(data)

    if jackpot is None:
        print("\n── Step 2: /api/jackpots (fallback list) ─────────────────────────")
        data = fetch_json(API_ALL)
        if data is not None:
            jackpot = find_midweek_jackpot(data)

    if jackpot is None:
        print("\n❌  Could not retrieve Mid Week Jackpot data.")
        return

    events = jackpot.get("events", [])
    if not events:
        print("\n⚠  Jackpot found but contains 0 events.")
        return

    print(f"\n  ✓  Mid Week Jackpot #{jackpot.get('humanId', '?')} — {len(events)} event(s)")
    print(f"     Betting status : {jackpot.get('bettingStatus', '?')}")

    rows = parse_events(jackpot)
    save_matches(rows, jackpot)
    print("\n✅  Done.\n")


if __name__ == "__main__":
    scrape()
