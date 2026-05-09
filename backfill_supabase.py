"""
backfill_supabase.py  —  Backfill local forecast files to Supabase
===================================================================
One-time script. Run from C:/Users/User/Desktop/Chronos/

Reads all local forecast JSON files for all three jackpots,
checks if they already exist in Supabase (by generated_at + card_file),
and pushes any missing ones. Also pushes actuals if present.

Usage:
    cd C:/Users/User/Desktop/Chronos
    python backfill_supabase.py

    # Dry run — shows what would be pushed without writing anything
    python backfill_supabase.py --dry-run

    # Single jackpot only
    python backfill_supabase.py --jackpot mozzart
"""

import json
import glob
import os
import argparse
from supabase import create_client

# ================================================================
# CONFIG
# ================================================================
JACKPOTS = {
    "mozzart": {
        "label"   : "Mozzart Daily",
        "dir"     : "mozzart/output",
        "pattern" : "mozzart_forecast_*.json",
        "num_games": 16,
    },
    "midweek": {
        "label"   : "Mid-Week",
        "dir"     : "midweek/output",
        "pattern" : "midweek_forecast_*.json",
        "num_games": 13,
    },
    "sportpesa": {
        "label"   : "Mega Jackpot",
        "dir"     : "sportpesa/output",
        "pattern" : "sportpesa_forecast_*.json",
        "num_games": 17,
    },
}

ROOT = os.path.dirname(os.path.abspath(__file__))


# ================================================================
# SUPABASE CLIENT
# ================================================================
def get_client():
    """
    Reads Supabase credentials from .streamlit/secrets.toml
    so you do not need to hardcode them.
    """
    secrets_path = os.path.join(ROOT, ".streamlit", "secrets.toml")
    if not os.path.exists(secrets_path):
        raise FileNotFoundError(
            f"secrets.toml not found at:\n  {secrets_path}\n"
            "Make sure .streamlit/secrets.toml exists with "
            "SUPABASE_URL and SUPABASE_KEY."
        )

    url = None
    key = None
    with open(secrets_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("SUPABASE_URL"):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("SUPABASE_KEY"):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")

    if not url or not key:
        raise ValueError(
            "Could not read SUPABASE_URL or SUPABASE_KEY from secrets.toml"
        )

    return create_client(url, key)


# ================================================================
# CHECK EXISTING
# ================================================================
def get_existing_forecasts(client, jackpot: str) -> set:
    """
    Return a set of (generated_at, card_file) tuples already in Supabase.
    Used to skip duplicates.
    """
    try:
        result = (
            client.table("forecasts")
            .select("generated_at, card_file")
            .eq("jackpot", jackpot)
            .execute()
        )
        return {
            (row["generated_at"], row["card_file"])
            for row in (result.data or [])
        }
    except Exception as e:
        print(f"  WARNING: Could not fetch existing forecasts: {e}")
        return set()


# ================================================================
# PUSH FORECAST
# ================================================================
def push_forecast(client, jackpot: str, data: dict, dry_run: bool) -> str | None:
    """Push forecast row to Supabase. Returns inserted id."""
    # Infer num_games from ticket length if not set
    num_games = data.get("num_games")
    if not num_games:
        try:
            ticket = data["tickets"]["base"]["ticket"]
            num_games = len(ticket)
        except (KeyError, TypeError):
            num_games = JACKPOTS[jackpot]["num_games"]

    row = {
        "jackpot"      : jackpot,
        "generated_at" : data.get("generated_at", ""),
        "card_file"    : data.get("card_file", ""),
        "card_signals" : data.get("card_signals", {}),
        "forecast"     : data.get("forecast", {}),
        "tickets"      : data.get("tickets", {}),
        "num_games"    : num_games,
        "match_analysis": data.get("match_analysis", []),
        "base_rates"   : data.get("base_rates", {}),
    }

    if dry_run:
        return "dry-run-id"

    try:
        result = client.table("forecasts").insert(row).execute()
        return result.data[0]["id"] if result.data else None
    except Exception as e:
        print(f"    ERROR pushing forecast: {e}")
        return None


# ================================================================
# PUSH ACTUALS
# ================================================================
def push_actuals(client, jackpot: str, forecast_id: str,
                 actuals: dict, dry_run: bool) -> bool:
    """Push actuals row linked to forecast_id."""
    row = {
        "forecast_id"       : forecast_id,
        "jackpot"           : jackpot,
        "logged_at"         : actuals.get("logged_at", ""),
        "results"           : actuals.get("results", []),
        "scores"            : actuals.get("scores", []),
        "ticket_scores"     : actuals.get("ticket_scores", {}),
        "distribution_error": actuals.get("distribution_error", {}),
        "signal_accuracy"   : actuals.get("signal_accuracy", {}),
        "per_match_accuracy": actuals.get("per_match_accuracy", {}),
        "best_score"        : actuals.get("best_score", 0),
        "best_ticket"       : actuals.get("best_ticket", ""),
    }

    if dry_run:
        return True

    try:
        client.table("actuals").insert(row).execute()
        return True
    except Exception as e:
        print(f"    ERROR pushing actuals: {e}")
        return False


# ================================================================
# PROCESS ONE JACKPOT
# ================================================================
def process_jackpot(client, jackpot: str, dry_run: bool):
    cfg     = JACKPOTS[jackpot]
    pattern = os.path.join(ROOT, cfg["dir"], cfg["pattern"])
    files   = sorted(glob.glob(pattern), key=os.path.getmtime)

    if not files:
        print(f"  No files found in {cfg['dir']}")
        return

    print(f"\n  {cfg['label']} — {len(files)} local file(s) found")

    # Get already-existing forecasts to skip duplicates
    existing = get_existing_forecasts(client, jackpot)
    print(f"  Already in Supabase: {len(existing)}")

    pushed_fc  = 0
    pushed_act = 0
    skipped    = 0
    errors     = 0

    for fpath in files:
        fname = os.path.basename(fpath)

        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"    [ERROR] Could not read {fname}: {e}")
            errors += 1
            continue

        generated_at = data.get("generated_at", "")
        card_file    = data.get("card_file", "")
        has_actuals  = "actuals" in data

        # Skip if already in Supabase
        if (generated_at, card_file) in existing:
            print(f"    [SKIP]  {fname} — already in Supabase")
            skipped += 1
            continue

        # Push forecast
        tag = "[DRY]" if dry_run else "[PUSH]"
        print(f"    {tag}  {fname}", end="")

        forecast_id = push_forecast(client, jackpot, data, dry_run)

        if forecast_id:
            pushed_fc += 1
            print(f" → forecast {'queued' if dry_run else 'saved'}", end="")

            # Push actuals if present
            if has_actuals:
                ok = push_actuals(
                    client, jackpot, forecast_id,
                    data["actuals"], dry_run
                )
                if ok:
                    pushed_act += 1
                    best = data["actuals"].get("best_score", "?")
                    n    = data.get("num_games") or cfg["num_games"]
                    print(f" + actuals (score={best}/{n})", end="")
                else:
                    print(f" + actuals FAILED", end="")
            else:
                print(f" (no actuals)", end="")
            print()
        else:
            print(f" → FAILED")
            errors += 1

    print(f"\n  Summary: pushed {pushed_fc} forecasts, "
          f"{pushed_act} actuals, "
          f"{skipped} skipped, "
          f"{errors} errors")
    return pushed_fc, pushed_act


# ================================================================
# MAIN
# ================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Backfill local Chronos forecast files to Supabase."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be pushed without writing to Supabase"
    )
    parser.add_argument(
        "--jackpot", default=None,
        choices=["mozzart", "midweek", "sportpesa"],
        help="Process only one jackpot (default: all three)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  SUPABASE BACKFILL")
    if args.dry_run:
        print("  MODE: DRY RUN — nothing will be written")
    print("=" * 60)

    # Connect
    print("\n  Connecting to Supabase...")
    try:
        client = get_client()
        # Quick connectivity test
        client.table("forecasts").select("id").limit(1).execute()
        print("  Connected.\n")
    except Exception as e:
        print(f"  ERROR: Could not connect to Supabase:\n  {e}")
        return

    # Process jackpots
    jackpots = (
        [args.jackpot] if args.jackpot
        else list(JACKPOTS.keys())
    )

    total_fc  = 0
    total_act = 0

    for jackpot in jackpots:
        result = process_jackpot(client, jackpot, args.dry_run)
        if result:
            total_fc  += result[0]
            total_act += result[1]

    print("\n" + "=" * 60)
    action = "Would push" if args.dry_run else "Pushed"
    print(f"  TOTAL: {action} {total_fc} forecasts, {total_act} actuals")
    print("=" * 60)


if __name__ == "__main__":
    main()
