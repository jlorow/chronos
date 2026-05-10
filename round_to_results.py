"""
round_to_results.py  —  Universal Results Converter
====================================================
Converts the latest round file from any jackpot into results.json format.
Handles all three jackpot formats automatically:

    Mozzart  : matches[] with result, row, score "2-1"
    Mega     : games[]   with pick,   game_num, score "3:1"
    Midweek  : results[] with outcome, game_num, score "3:2"

File naming convention in rounds/:
    round_YYYY-MM-DD_<id>.json          <- Mozzart
    round_mega_YYYY-MM-DD_<id>.json     <- Mega
    round_midweek_YYYY-MM-DD_<id>.json  <- Midweek

Usage:
    python round_to_results.py                    # auto-detects latest
    python round_to_results.py --jackpot mega     # latest mega round
    python round_to_results.py --jackpot midweek  # latest midweek round
    python round_to_results.py --jackpot mozzart  # latest mozzart round
    python round_to_results.py --file rounds/round_mega_2026-05-03_179.json
"""

import json
import os
import glob
import argparse

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
ROUNDS_FOLDER = os.path.join(BASE_DIR, "rounds")
RESULTS_FILE  = os.path.join(BASE_DIR, "results.json")


# ================================================================
# FORMAT DETECTION
# ================================================================
def detect_format(data: dict) -> str:
    """
    Auto-detect which jackpot format this round file is.
    Returns: 'mozzart' | 'mega' | 'midweek' | 'unknown'
    """
    if "matches" in data:
        return "mozzart"
    if "games" in data:
        return "mega"
    if "results" in data:
        return "midweek"
    return "unknown"


# ================================================================
# SCORE NORMALISATION
# ================================================================
def normalise_score(score_str: str) -> str:
    """
    Normalise score to dash format regardless of input separator.
    '3:1' -> '3-1'
    '3-1' -> '3-1'
    None  -> None
    """
    if not score_str:
        return None
    return str(score_str).replace(":", "-").strip()


# ================================================================
# FORMAT CONVERTERS
# ================================================================
def convert_mozzart(data: dict) -> tuple[list, list]:
    """
    Mozzart format:
        matches[]: { row, result (1/X/2), score "2-1" }
    """
    matches = sorted(data["matches"], key=lambda m: m["row"])
    results = [m["result"] for m in matches]
    scores  = [normalise_score(m.get("score")) for m in matches]
    return results, scores


def convert_mega(data: dict) -> tuple[list, list]:
    """
    Mega format:
        games[]: { game_num, pick (1/X/2), score "3:1" }
    """
    games   = sorted(data["games"], key=lambda g: g["game_num"])
    results = [g["pick"] for g in games]
    scores  = [normalise_score(g.get("score")) for g in games]
    return results, scores


def convert_midweek(data: dict) -> tuple[list, list]:
    """
    Midweek format:
        results[]: { game_num, outcome (1/X/2), score "3:2" }
    """
    matches = sorted(data["results"], key=lambda m: m["game_num"])
    results = [m["outcome"] for m in matches]
    scores  = [normalise_score(m.get("score")) for m in matches]
    return results, scores


CONVERTERS = {
    "mozzart": convert_mozzart,
    "mega"   : convert_mega,
    "midweek": convert_midweek,
}


# ================================================================
# FILE DISCOVERY
# ================================================================
def extract_date_from_filename(filepath: str) -> str:
    """
    Extract date string from round filename for reliable sorting.
    Works regardless of file modification time (fixes Streamlit Cloud issue
    where all files get the same mtime after git clone).

    round_2026-05-09_1000028349.json         -> 2026-05-09
    round_mega_2026-05-03_179.json           -> 2026-05-03
    round_midweek_2026-05-08_830.json        -> 2026-05-08
    """
    import re
    name = os.path.basename(filepath)
    match = re.search(r'(\d{4}-\d{2}-\d{2})', name)
    if match:
        return match.group(1)
    # Fallback: use mtime if no date in filename
    return str(os.path.getmtime(filepath))


def get_latest_round_file(jackpot: str = None) -> str | None:
    """
    Get latest round file sorted by DATE in filename (not mtime).
    Reliable on Streamlit Cloud where all files share the same mtime
    after a git clone.
    If jackpot specified, filter by prefix pattern.
    """
    if jackpot == "mega":
        pattern = os.path.join(ROUNDS_FOLDER, "round_mega_*.json")
    elif jackpot == "midweek":
        pattern = os.path.join(ROUNDS_FOLDER, "round_midweek_*.json")
    elif jackpot == "mozzart":
        all_files = glob.glob(os.path.join(ROUNDS_FOLDER, "round_*.json"))
        files = [
            f for f in all_files
            if "mega" not in os.path.basename(f)
            and "midweek" not in os.path.basename(f)
        ]
        if not files:
            return None
        files.sort(key=extract_date_from_filename, reverse=True)
        return files[0]
    else:
        pattern = os.path.join(ROUNDS_FOLDER, "round_*.json")

    files = glob.glob(pattern)
    if not files:
        return None
    files.sort(key=extract_date_from_filename, reverse=True)
    return files[0]


# ================================================================
# MAIN CONVERSION
# ================================================================
def convert_file(filepath: str) -> dict:
    """
    Read a round file, auto-detect format, convert to results.json format.
    Returns {"results": [...], "scores": [...], "format": "...", "source": "..."}
    """
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    fmt = detect_format(data)

    if fmt == "unknown":
        raise ValueError(
            f"Cannot detect format for: {os.path.basename(filepath)}\n"
            f"Expected one of: matches[], games[], results[]"
        )

    converter      = CONVERTERS[fmt]
    results, scores = converter(data)

    # Validate
    invalid = [r for r in results if r not in ("1", "X", "2")]
    if invalid:
        raise ValueError(
            f"Invalid result values found: {invalid}\n"
            f"All results must be 1, X, or 2."
        )

    return {
        "results": results,
        "scores" : scores,
        "format" : fmt,
        "source" : os.path.basename(filepath),
    }


# ================================================================
# SAVE HELPERS FOR MEGA AND MIDWEEK
# (Call these from your existing results scripts)
# ================================================================
def save_mega_round(data: dict) -> str:
    """
    Save a Mega Jackpot results dict to rounds/ with correct naming.
    Call this from your Mega results script instead of saving directly.

    Expected data format: sportpesa_latest_jackpot.json structure
    Returns: saved filepath
    """
    os.makedirs(ROUNDS_FOLDER, exist_ok=True)

    jackpot_id = data.get("jackpot_human_id", "unknown")
    finished   = data.get("finished_at", data.get("scraped_at", ""))[:10]
    filename   = f"round_mega_{finished}_{jackpot_id}.json"
    filepath   = os.path.join(ROUNDS_FOLDER, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return filepath


def save_midweek_round(data: dict) -> str:
    """
    Save a Midweek Jackpot results dict to rounds/ with correct naming.
    Call this from your Midweek results script instead of saving directly.

    Expected data format: sportpesa_midweek_results.json structure
    Returns: saved filepath
    """
    os.makedirs(ROUNDS_FOLDER, exist_ok=True)

    jackpot_id = data.get("jackpot_id", "unknown")
    first_game = data.get("first_game_utc", data.get("scraped_at", ""))[:10]
    filename   = f"round_midweek_{first_game}_{jackpot_id}.json"
    filepath   = os.path.join(ROUNDS_FOLDER, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return filepath


# ================================================================
# RUN
# ================================================================
def run(jackpot: str = None, filepath: str = None):
    os.makedirs(ROUNDS_FOLDER, exist_ok=True)

    # Resolve file
    if filepath:
        target = filepath
        if not os.path.exists(target):
            print(f"ERROR: File not found: {target}")
            return
    else:
        target = get_latest_round_file(jackpot)
        if not target:
            label = f"{jackpot} " if jackpot else ""
            print(f"ERROR: No {label}round files found in: {ROUNDS_FOLDER}")
            if jackpot in ("mega", "midweek"):
                print(
                    f"       Run your {jackpot} results script first,\n"
                    f"       then call save_{jackpot}_round() to save to rounds/"
                )
            return

    print(f"Round file : {os.path.basename(target)}")

    try:
        output = convert_file(target)
    except ValueError as e:
        print(f"ERROR: {e}")
        return

    print(f"Format     : {output['format']}")
    print(f"Matches    : {len(output['results'])}")

    # Write results.json
    save_data = {
        "results": output["results"],
        "scores" : output["scores"],
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(save_data, f, indent=2)

    print(f"Saved      : results.json")
    print(f"Results    : {output['results']}")
    print(f"Scores     : {output['scores']}")


# ================================================================
# ENTRY POINT
# ================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert latest round file to results.json"
    )
    parser.add_argument(
        "--jackpot",
        choices=["mozzart", "mega", "midweek"],
        default=None,
        help="Filter by jackpot type (default: auto-detect latest)"
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Specific round file to convert"
    )
    args = parser.parse_args()
    run(jackpot=args.jackpot, filepath=args.file)