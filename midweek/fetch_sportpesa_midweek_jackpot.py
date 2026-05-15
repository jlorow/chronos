"""
SportPesa Mid Week Jackpot Scraper  (v6 - fixed)

Fixes vs v5:
  1. Sort by kickoff time (ascending), not smsId
  2. Odds were swapped: shortName "1" = away, "2" = home in this API schema
     (confirmed by cross-referencing required output vs actual output)
  3. Date/time converted from UTC → EAT (UTC+3) and formatted as DD/MM/YY HH:MM
  4. Console table now includes the kickoff date/time column
  5. Sequential match numbers (1-13) used in display instead of raw smsId
"""

import json
import os
import gzip
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

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

# EAT = UTC+3 (East Africa Time, used in Kenya / SportPesa local times)
EAT = timezone(timedelta(hours=3))

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

    FIX — Odds mapping:
      In this endpoint's schema, shortName "1" = away team odds,
      shortName "2" = home team odds (opposite of the usual convention).
      This was confirmed by cross-referencing actual API output against
      the required/expected output for known matches.

    FIX — Sort:
      Rows are sorted by kickoff datetime ascending, then by smsId as
      a tiebreaker — matching the required display order.

    FIX — Timezone:
      Kickoff stored as UTC ISO string; converted to EAT (UTC+3) for
      display, formatted DD/MM/YY HH:MM.
    """
    rows = []
    for e in events:
        comps = e.get("competitors", [])
        home  = comps[0].get("name", "?") if len(comps) > 0 else "?"
        away  = comps[1].get("name", "?") if len(comps) > 1 else "?"

        # BUG FIX: shortName "1" → away odds, "2" → home odds in this API
        h_odd = d_odd = a_odd = "-"
        for market in e.get("markets", []):
            for sel in market.get("selections", []):
                sn = sel.get("shortName", "")
                if sn == "2":   h_odd = sel.get("odds", "-")   # ← was "1"
                elif sn == "X": d_odd = sel.get("odds", "-")
                elif sn == "1": a_odd = sel.get("odds", "-")   # ← was "2"

        raw_date = e.get("date", "")
        ko_dt_utc = None
        kickoff_display = raw_date  # fallback
        kickoff_sort    = raw_date  # fallback for sorting
        try:
            ko_dt_utc = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            ko_dt_eat = ko_dt_utc.astimezone(EAT)
            # FIX: format as DD/MM/YY HH:MM (EAT local time)
            kickoff_display = ko_dt_eat.strftime("%d/%m/%y %H:%M")
            kickoff_sort    = ko_dt_utc  # datetime object for reliable sorting
        except Exception:
            pass

        score = e.get("state", {}).get("result", "")
        if score in ("-:-", "", None):
            score = None

        rows.append({
            "sms_id":       e.get("smsId", ""),
            "event_id":     e.get("id", ""),
            "home":         home,
            "away":         away,
            "tournament":   e.get("competition", {}).get("name", ""),
            "country":      e.get("country", {}).get("name", ""),
            "kickoff":      kickoff_display,   # human-readable EAT
            "kickoff_sort": kickoff_sort,       # used only for sorting
            "home_odd":     h_odd,
            "draw_odd":     d_odd,
            "away_odd":     a_odd,
            "betting_open": True,
            "score":        score,
        })

    # FIX: sort by kickoff time ascending, smsId as tiebreaker
    rows.sort(key=lambda r: (
        r["kickoff_sort"] if isinstance(r["kickoff_sort"], datetime) else datetime.max,
        r["sms_id"] if isinstance(r["sms_id"], int) else 0,
    ))

    # Assign sequential display numbers after sorting
    for i, r in enumerate(rows, start=1):
        r["order"] = i

    return rows


# ── Output ─────────────────────────────────────────────────────────────────────

def save_matches(rows: list, raw_events: list) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    raw_path    = os.path.join(OUTPUT_DIR, f"jackpot_raw_{now}.json")
    parsed_path = os.path.join(OUTPUT_DIR, f"jackpot_parsed_{now}.json")

    # Strip kickoff_sort (datetime object) before serialising to JSON
    serialisable = [{k: v for k, v in r.items() if k != "kickoff_sort"} for r in rows]

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_events, f, indent=2, ensure_ascii=False)
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(serialisable, f, indent=2, ensure_ascii=False)

    print(f"\n  💾  Raw    → {raw_path}")
    print(f"  💾  Parsed → {parsed_path}")

    col_h = max((len(r["home"]) for r in rows), default=10)
    col_a = max((len(r["away"]) for r in rows), default=10)
    col_h, col_a = max(col_h, 6), max(col_a, 6)

    # FIX: header now includes Kickoff column (DD/MM/YY HH:MM = 14 chars)
    header = (
        f"\n  {'#':<4} {'smsId':<6} {'Kickoff':<14} "
        f"{'Home':<{col_h}} {'Away':<{col_a}} "
        f"{'1':>6} {'X':>6} {'2':>6}  Country"
    )
    print(header)
    print("  " + "─" * (len(header) - 2))
    for r in rows:
        score = f"  [{r['score']}]" if r["score"] else ""
        print(
            f"  {str(r['order'])+'.':<4} "
            f"{str(r['sms_id']):<6} "
            f"{r['kickoff']:<14} "
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
    print("  SportPesa Mid Week Jackpot Scraper  v6")
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

    # ── Upload card to Supabase so Streamlit Cloud can read it ────────────────
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from db import save_card
        serialisable = [{k: v for k, v in r.items() if k != "kickoff_sort"} for r in rows]
        ok = save_card("midweek", serialisable, events)
        print(f"  {'✓' if ok else '✗'}  Supabase card upload {'succeeded' if ok else 'failed'}")
    except Exception as e:
        print(f"  ✗  Supabase card upload error: {e}")

    print("\n✅  Done.\n")


if __name__ == "__main__":
    scrape()