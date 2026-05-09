"""
SportPesa Mid Week Jackpot Scraper  (v3 - with click interaction + direct API fallback)

The /api/jackpots/events endpoint is only triggered AFTER the user clicks into
a specific jackpot on the page.  This version:
  1. Tries a direct HTTP request to the API first (fastest, no browser needed)
  2. Falls back to Playwright: loads the jackpot page, clicks every jackpot
     card/tab it can find, and waits for the events API call to fire
"""

from playwright.sync_api import sync_playwright
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Configuration ──────────────────────────────────────────────────────────────

JACKPOT_PAGE    = "https://www.ke.sportpesa.com/en/jackpot"
EVENTS_API      = "https://www.ke.sportpesa.com/api/jackpots/events?type=regular"
EVENTS_PATTERN  = "/api/jackpots/events"
META_PATTERN    = "/api/jackpots/multi"

OUTPUT_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "midweek", "data", "cards")
HEADLESS    = False        # keep False so you can see what the browser is doing
WAIT_MS     = 45_000       # increased to 45s

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# ── Attempt 1: direct HTTP request ────────────────────────────────────────────

def try_direct_api():
    """
    Hit the events endpoint directly with a plain HTTP request.
    Works if the endpoint does not require session cookies.
    """
    print("🔗  Trying direct API request...")
    req = urllib.request.Request(
        EVENTS_API,
        headers={
            "User-Agent":      UA,
            "Accept":          "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer":         JACKPOT_PAGE,
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if isinstance(data, list) and len(data) > 0:
                print(f"  ✓ Direct API success — {len(data)} events")
                return data
            print(f"  ℹ  Direct API returned unexpected shape: {type(data)}")
            return data
    except urllib.error.HTTPError as e:
        print(f"  ✗ Direct API HTTP {e.code} — need browser session")
    except Exception as e:
        print(f"  ✗ Direct API failed: {e}")
    return None


# ── Interceptor ────────────────────────────────────────────────────────────────

class Interceptor:
    def __init__(self):
        self.events   = None
        self.meta     = None
        self.all_urls = []   # log every API URL seen — helps debugging

    def on_response(self, response):
        if response.status != 200:
            return
        ct  = response.headers.get("content-type", "")
        url = response.url

        if any(k in url for k in ["/api/", "sportpesa"]):
            self.all_urls.append(url)

        if "json" not in ct:
            return
        try:
            data = response.json()
        except Exception:
            return

        if EVENTS_PATTERN in url:
            self.events = data
            print(f"  ✓ EVENTS captured  ({len(str(data))} bytes)  →  {url[:100]}")

        elif META_PATTERN in url:
            self.meta = data
            print(f"  ✓ META  captured   ({len(str(data))} bytes)  →  {url[:100]}")

        elif any(k in url for k in ["/api/", "sportpesa"]):
            print(f"  [API] {url[:120]}")


# ── Attempt 2: Playwright with clicks ─────────────────────────────────────────

# Selectors to try — covers most SportPesa jackpot page layouts
CLICK_SELECTORS = [
    "a[href*='jackpot']",
    "button:has-text('Mid Week')",
    "button:has-text('Midweek')",
    "button:has-text('Jackpot')",
    "[class*='jackpot']",
    "[class*='tab']",
    "li[class*='jackpot']",
    "div[class*='jackpot-item']",
    "div[class*='jackpot-card']",
    ".jackpot-list > *",
    "[data-type*='jackpot']",
]

def try_playwright(interceptor):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        ctx = browser.new_context(
            user_agent=UA,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        page.on("response", interceptor.on_response)

        print(f"\n🌐  Loading: {JACKPOT_PAGE}")
        print(f"⏳  Waiting up to {WAIT_MS // 1000}s...\n")

        try:
            page.goto(JACKPOT_PAGE, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:
            print(f"⚠️  Page load warning: {e}")

        # Give the page 3s to settle before clicking
        page.wait_for_timeout(3_000)

        if interceptor.events is None:
            print("  🖱️  Events not yet loaded — trying clicks...\n")
            for sel in CLICK_SELECTORS:
                try:
                    els = page.query_selector_all(sel)
                    if els:
                        print(f"  Found {len(els)} element(s) matching '{sel}' — clicking first")
                        els[0].scroll_into_view_if_needed()
                        els[0].click(timeout=3_000)
                        page.wait_for_timeout(2_000)
                        if interceptor.events is not None:
                            print("  ✓ Events fired after click!")
                            break
                except Exception:
                    pass

        # Keep waiting for the remainder of WAIT_MS
        waited = 5_000
        while waited < WAIT_MS:
            page.wait_for_timeout(1_000)
            waited += 1_000
            if interceptor.events is not None:
                print(f"\n  ✓ Events captured after ~{waited // 1000}s")
                break
            if waited % 5_000 == 0:
                print(f"  ... {waited // 1000}s elapsed, still waiting...")

        # Last-ditch: dump all API URLs seen so you can find the right one
        if interceptor.events is None:
            print("\n  📋  All API URLs seen during this session:")
            for u in interceptor.all_urls:
                print(f"       {u}")

        browser.close()


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_events(events):
    rows = []
    for e in events:
        comps = e.get("competitors", [])
        home  = comps[0].get("name") if len(comps) > 0 else "?"
        away  = comps[1].get("name") if len(comps) > 1 else "?"

        h_odd = d_odd = a_odd = "-"
        for market in e.get("markets", []):
            for sel in market.get("selections", []):
                sn = sel.get("shortName", "")
                if sn == "1":   h_odd = sel.get("odds", "-")
                elif sn == "X": d_odd = sel.get("odds", "-")
                elif sn == "2": a_odd = sel.get("odds", "-")

        rows.append({
            "order":    e.get("smsId", ""),
            "event_id": e.get("id"),
            "home":     home,
            "away":     away,
            "country":  e.get("country", {}).get("name", ""),
            "kickoff":  e.get("date", ""),
            "home_odd": h_odd,
            "draw_odd": d_odd,
            "away_odd": a_odd,
        })
    return rows


# ── Output ─────────────────────────────────────────────────────────────────────

def save_matches(rows, raw_events):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    raw_path    = os.path.join(OUTPUT_DIR, f"jackpot_raw_{now}.json")
    parsed_path = os.path.join(OUTPUT_DIR, f"jackpot_parsed_{now}.json")

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_events, f, indent=2, ensure_ascii=False)
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"\n💾  Raw    → {raw_path}")
    print(f"💾  Parsed → {parsed_path}")

    print(f"\n    {'SMS':<6} {'Home':<28} {'Away':<28} {'1':>6} {'X':>6} {'2':>6}  Country")
    print("    " + "-" * 98)
    for r in rows:
        print(
            f"    {str(r['order']):<6} "
            f"{r['home']:<28} "
            f"{r['away']:<28} "
            f"{str(r['home_odd']):>6} "
            f"{str(r['draw_odd']):>6} "
            f"{str(r['away_odd']):>6}  "
            f"{r['country']}"
        )


# ── Main ───────────────────────────────────────────────────────────────────────

def scrape():
    print(f"\n{'='*60}")
    print("  SportPesa Jackpot Scraper v3")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # --- Attempt 1: direct HTTP (no browser needed) ---------------------------
    events = try_direct_api()

    # --- Attempt 2: Playwright with page clicks -------------------------------
    if not events:
        interceptor = Interceptor()
        try_playwright(interceptor)
        events = interceptor.events

    if not events:
        print("\n❌  Could not capture jackpot events.")
        print("    Run with HEADLESS=False and check the 'All API URLs' list above.")
        print("    Find the URL that returns the match list, then update EVENTS_PATTERN.")
        return

    events_list = events if isinstance(events, list) else []
    print(f"\n  ✓ {len(events_list)} match event(s) ready to parse")

    rows = parse_events(events_list)
    save_matches(rows, events_list)
    print("\n✅  Done.\n")


if __name__ == "__main__":
    scrape()