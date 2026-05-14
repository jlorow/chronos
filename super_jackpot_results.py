import requests
import json
import os
from datetime import datetime

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "referer": "https://www.mozzartbet.co.ke/en",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/147.0.0.0 Safari/537.36"
}

ROUNDS_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rounds")

def has_results(matches):
    """Check all matches have a valid result (1, X, or 2)"""
    for match in matches:
        result = match.get("shortResultDesc", "")
        if result not in ("1", "X", "2"):
            print(f"  DEBUG has_results FAILED: match row={match.get('rowNumber')} shortResultDesc='{result}'")
            return False
    return True

def is_super_jackpot(round_data):
    """Super Jackpot = 16 rows, 20M prize"""
    return round_data.get("totalRows") == 16

def clean_round(round_data):
    """Extract only what we need"""
    first_match_time = round_data["matches"][0]["time"] / 1000
    date_str = datetime.fromtimestamp(first_match_time).strftime("%Y-%m-%d %H:%M")

    matches = []
    for match in round_data["matches"]:
        odds = {o["bettingSubGameName"]: float(o["bettingSubGameOdds"]) for o in match["odds"]}
        matches.append({
            "row": match["rowNumber"],
            "home": match["home"].strip(),
            "away": match["visitor"].strip(),
            "league": match["competition"]["name"],
            "result": match["shortResultDesc"],
            "score": f"{match['homeResult']}-{match['visitorResult']}",
            "odds_1": odds.get("1"),
            "odds_x": odds.get("X"),
            "odds_2": odds.get("2")
        })

    return {
        "ticketId": round_data["id"],
        "roundId": round_data["roundId"],
        "date": date_str,
        "matches": matches
    }

def save_round(clean_data):
    """Save round using ticketId to avoid overwriting different rounds on same date"""
    os.makedirs(ROUNDS_FOLDER, exist_ok=True)
    filename = f"round_{clean_data['date'].split(' ')[0]}_{clean_data['ticketId']}.json"
    filepath = os.path.join(ROUNDS_FOLDER, filename)
    with open(filepath, "w") as f:
        json.dump(clean_data, f, indent=2)
    return filepath

def already_saved(ticket_id, date_str):
    """Check if this specific ticket is already saved"""
    date_part = date_str.split(" ")[0]
    filename = f"round_{date_part}_{ticket_id}.json"
    return os.path.exists(os.path.join(ROUNDS_FOLDER, filename))

def run():
    print("Fetching latest rounds from Mozzart...")

    resp = requests.get(
        "https://www.mozzartbet.co.ke/predefined-tickets-rounds",
        headers=HEADERS,
        timeout=15
    )

    if resp.status_code != 200:
        print(f"❌ Failed to fetch. Status: {resp.status_code}")
        return

    all_rounds = resp.json()
    print(f"Got {len(all_rounds)} rounds from API")

    # Filter: Super Jackpot only (16 rows), has results
    # Keep ALL settled rounds (one per unique ticket id) for debug visibility
    settled_all = []
    seen_tickets = set()
    for r in all_rounds:
        if not is_super_jackpot(r):
            continue
        if not has_results(r["matches"]):
            continue
        tid = r["id"]
        if tid not in seen_tickets:
            seen_tickets.add(tid)
            settled_all.append(r)

    # Sort by date descending
    settled_all.sort(
        key=lambda r: r["matches"][0]["time"],
        reverse=True
    )

    # ── DEBUG: show ALL available settled rounds before slicing ──────────────
    print(f"\nAll settled Super Jackpot rounds from API ({len(settled_all)} total):")
    for i, r in enumerate(settled_all):
        d = datetime.fromtimestamp(r["matches"][0]["time"] / 1000).strftime("%Y-%m-%d")
        saved_marker = " [already saved]" if already_saved(r["id"], datetime.fromtimestamp(r["matches"][0]["time"] / 1000).strftime("%Y-%m-%d %H:%M")) else ""
        print(f"  [{i}] date={d}  ticket={r['id']}  roundId={r['roundId']}{saved_marker}")
    # ─────────────────────────────────────────────────────────────────────────

    if not settled_all:
        print("\n⏳ No settled Super Jackpot rounds available yet.")
        return

    saved_count = 0
    for latest in settled_all:
        cleaned = clean_round(latest)

        first_match_time = latest["matches"][0]["time"] / 1000
        date_str = datetime.fromtimestamp(first_match_time).strftime("%Y-%m-%d %H:%M")

        if already_saved(latest["id"], date_str):
            print(f"\n⏭️  Round already saved (Ticket {latest['id']}, {cleaned['date']}). Skipping.")
            continue

        filepath = save_round(cleaned)
        saved_count += 1
        print(f"\n✅ Saved round:")
        print(f"   Ticket ID : {latest['id']}")
        print(f"   Round     : {latest['roundId']}")
        print(f"   Date      : {cleaned['date']}")
        print(f"   File      : {os.path.basename(filepath)}")
        print(f"   Matches   : {len(cleaned['matches'])}")

    if saved_count == 0:
        print("\nℹ️  No new rounds to save. Latest round on disk matches API.")
        print(f"   Latest API round: ticket={settled_all[0]['id']}, date={datetime.fromtimestamp(settled_all[0]['matches'][0]['time']/1000).strftime('%Y-%m-%d')}")

run()