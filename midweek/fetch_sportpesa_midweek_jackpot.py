"""
SportPesa Mid Week Jackpot Scraper  (v5 - correct endpoint)

Uses https://www.ke.sportpesa.com/api/jackpots/events?type=regular
which returns the Mid Week card directly (13 events, different schema).
"""

import json
import os
import gzip
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Configuration ──────────────────────────────────────────────────────────────

API_MIDWEEK = "https://www.ke.sportpesa.com/api/jackpots/events?type=regular"

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cards")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent":      UA,
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.ke.sportpesa.com/en/jackpot",
    "Origin":          "https://www.ke.sportpesa.com",
    "Connection":      "keep-alive",
}

# ── HTTP helper ────────────────────────────────────────────────────────────────

def fetch_json(url: str):
    print(f"  🔗  GET {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw_bytes = resp.read()
            encoding  = resp.headers.get("Content-Encoding", "")
            if encoding == "gzip" or raw_bytes[:2] == b"\x1f\x8b":
                raw_bytes = gzip.decompress(raw_bytes)
            raw = raw_bytes.decode("utf-8", errors="replace")
            if not raw.strip():
                print("  ✗  Empty response body")
                return None
            data = json.loads(raw)
            print(f"  ✓  Response received ({len(raw):,} bytes, type={type(data).__name__})")
            return data
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        print(f"  ✗  HTTP {e.code} — {e.reason}  |  {body[:200]}")
    except urllib.error.URLError as e:
        print(f"  ✗  URL error: {e.reason}")
    except json.JSONDecodeError as e:
        print(f"  ✗  JSON decode error: {e}")
    except Exception as e:
        print(f"  ✗  Unexpected error: {e}")
    return None


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_events(events: list) -> list:
    """
    Parse the events list from /api/jackpots/events?type=regular
    Schema: id, smsId, competitors[{id,name}], date, country.name,
            markets[{selections:[{shortName,odds}]}], state.result
    """
    rows = []
    for e in events:
        comps = e.get("competitors", [])
        home  = comps[0].get("name", "?") if len(comps) > 0 else "?"
        away  = comps[1].get("name", "?") if len(comps) > 1 else "?"

        h_odd = d_odd = a_odd = "-"
        for market in e.get("markets", []):
            for sel in market.get("selections", []):
                sn = sel.get("shortName", "")
                if sn == "1":   h_odd = sel.get("odds", "-")
                elif sn == "X": d_odd = sel.get("odds", "-")
                elif sn == "2": a_odd = sel.get("odds", "-")

        raw_date = e.get("date", "")
        try:
            ko_dt   = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            kickoff = ko_dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            kickoff = raw_date

        score = e.get("state", {}).get("result", "")
        if score in ("-:-", "", None):
            score = None

        rows.append({
            "order":        e.get("smsId", ""),
            "event_id":     e.get("id", ""),
            "home":         home,
            "away":         away,
            "tournament":   e.get("competition", {}).get("name", ""),
            "country":      e.get("country", {}).get("name", ""),
            "kickoff":      kickoff,
            "home_odd":     h_odd,
            "draw_odd":     d_odd,
            "away_odd":     a_odd,
            "betting_open": True,
            "score":        score,
        })

    rows.sort(key=lambda r: r["order"] if isinstance(r["order"], int) else 0)
    return rows


# ── Output ─────────────────────────────────────────────────────────────────────

def save_matches(rows: list, raw_events: list) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    raw_path    = os.path.join(OUTPUT_DIR, f"jackpot_raw_{now}.json")
    parsed_path = os.path.join(OUTPUT_DIR, f"jackpot_parsed_{now}.json")

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_events, f, indent=2, ensure_ascii=False)
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"\n  💾  Raw    → {raw_path}")
    print(f"  💾  Parsed → {parsed_path}")

    col_h = max((len(r["home"]) for r in rows), default=10)
    col_a = max((len(r["away"]) for r in rows), default=10)
    col_h, col_a = max(col_h, 6), max(col_a, 6)

    header = (
        f"\n  {'#':<6} {'Home':<{col_h}} {'Away':<{col_a}} "
        f"{'1':>6} {'X':>6} {'2':>6}  Country"
    )
    print(header)
    print("  " + "─" * (len(header) - 2))
    for r in rows:
        score = f"  [{r['score']}]" if r["score"] else ""
        print(
            f"  {str(r['order']):<6} "
            f"{r['home']:<{col_h}} "
            f"{r['away']:<{col_a}} "
            f"{str(r['home_odd']):>6} "
            f"{str(r['draw_odd']):>6} "
            f"{str(r['away_odd']):>6}  "
            f"{r['country']}{score}"
        )


# ── Main ───────────────────────────────────────────────────────────────────────

def scrape():
    print(f"\n{'='*65}")
    print("  SportPesa Mid Week Jackpot Scraper  v5")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*65}\n")

    events = fetch_json(API_MIDWEEK)

    if not events:
        print("\n❌  Could not retrieve Mid Week Jackpot data.")
        return

    if not isinstance(events, list):
        print(f"\n❌  Unexpected response type: {type(events)}. Expected list.")
        return

    print(f"\n  ✓  {len(events)} event(s) received")

    rows = parse_events(events)
    save_matches(rows, events)
    print("\n✅  Done.\n")


if __name__ == "__main__":
    scrape()
