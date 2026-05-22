"""
sportpesa_forecast.py  —  SportPesa Mega Jackpot Forecast (Chronos)
===================================================================
17-match weekly jackpot + Pro variants (13/14/15/16 games).

Directory structure (run from C:/Users/User/Desktop/Chronos/):
    sportpesa/data/batches/    <- mega_jackpot_enriched.json
    sportpesa/data/cards/      <- mega_jackpot_parsed_*.json
    sportpesa/data/enriched/   <- footystats enriched (future)
    sportpesa/output/          <- all prediction outputs

Usage:
    cd C:/Users/User/Desktop/Chronos
    python sportpesa/sportpesa_forecast.py

    # Faster model:
    python sportpesa/sportpesa_forecast.py --model tiny

Key differences vs mozzart_forecast.py:
    - 17 matches per round (not 16)
    - Batch: flat list format, home_team/away_team, odds_1/x/2 floats
    - Card: home/away, tournament/country, home_odd/draw_odd/away_odd
    - Base rates ~33% each (vs Mozzart home-heavy 35/34/29)
    - Much wider league diversity (66 unique leagues in batch)
    - Pro tickets: top-N confidence picks from Base (13/14/15/16 games)
    - dc_mode tagging for future Dixon-Coles integration
      (full / playoff / european / international / unknown)

Tickets produced:
    Full 17-game: Conservative (P10) / Base (P50) / Draw-Heavy (P90)
    Pro variants: Pro-16 / Pro-15 / Pro-14 / Pro-13
"""

import json
import glob
import os
import sys
import argparse
import numpy as np
import torch
from chronos import ChronosPipeline
from datetime import datetime

# ================================================================
# PATHS
# ================================================================
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))  # sportpesa/
ROOT_DIR     = os.path.dirname(BASE_DIR)                   # Chronos/
BATCHES_DIR  = os.path.join(BASE_DIR, "data", "batches")
CARDS_DIR    = os.path.join(BASE_DIR, "data", "cards")
ENRICHED_DIR = os.path.join(BASE_DIR, "data", "enriched")
OUTPUT_DIR   = os.path.join(BASE_DIR, "output")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ================================================================
# LEAGUE MAP  (batch league string -> feature key)
# Covers all 66 unique leagues found in mega_jackpot_enriched.json
# ================================================================
LEAGUE_MAP = {
    # Top European leagues
    "premier league"           : "premier_count",
    "serie a"                  : "serie_a_count",
    "la liga"                  : "la_liga_count",
    "bundesliga"               : "bundesliga_count",
    "ligue 1"                  : "ligue1_count",
    "ligue 2"                  : "ligue2_count",
    "serie b"                  : "serie_b_count",
    "serie c"                  : "lega_pro_count",
    "segunda division"         : "segunda_count",
    "championship"             : "championship_count",
    "league one"               : "league_one_count",
    "league two"               : "league_two_count",
    "2. bundesliga"            : "bundesliga2_count",
    "eredivisie"               : "eredivisie_count",
    "primeira liga"            : "primeira_count",
    "liga portugal 2"          : "liga_portugal2_count",
    "belgian pro league"       : "belgium_count",
    "super lig"                : "turkey_count",
    "1. lig"                   : "turkey2_count",
    "scottish premiership"     : "scottish_count",
    "scottish championship"    : "scottish2_count",
    "scottish league two"      : "scottish2_count",
    "swiss super league"       : "swiss_count",
    "austrian bundesliga"      : "austrian_count",
    "super league greece"      : "greek_count",
    "ekstraklasa"              : "polish_count",
    "czech liga"               : "czech_count",
    "romanian liga i"          : "romanian_count",
    "russian premier league"   : "russian_count",
    "slovak super liga"        : "slovak_count",
    "slovenian prvaliga"       : "slovenian_count",
    "croatian supersport hnl"  : "croatian_count",
    "danish superliga"         : "danish_count",
    "eliteserien"              : "nordic_count",
    "allsvenskan"              : "nordic_count",
    "superettan"               : "nordic_count",
    "veikkausliiga"            : "nordic_count",
    "israeli premier league"   : "israeli_count",
    # Americas
    "mls"                      : "mls_count",
    "liga mx"                  : "liga_mx_count",
    "ascenso mx"               : "liga_mx_count",
    "brasileirao serie a"      : "brasileirao_count",
    "brasileirao serie b"      : "brasileirao_count",
    "argentine primera division": "arg_count",
    "uruguayan primera division": "arg_count",
    "colombian liga betplay"   : "copa_count",
    "usl championship"         : "mls_count",
    # Asia / Oceania
    "j1 league"                : "j_league_count",
    "j2 league"                : "j_league_count",
    "k league 1"               : "k_league_count",
    "k league 2"               : "k_league_count",
    "chinese super league"     : "chinese_count",
    "a-league"                 : "aus_count",
    "npl australia"            : "aus_count",
    "npl victoria"             : "aus_count",
    "liga 2 indonesia"         : "sea_count",
    # Africa / Middle East
    "south african psl"        : "africa_count",
    "algerian ligue professionnelle 1": "africa_count",
    "tunisian ligue 1"         : "africa_count",
    "egyptian premier league"  : "africa_count",
    # Other European
    "league of ireland premier division": "ireland_count",
    "league of ireland first division"  : "ireland_count",
    "division 1"               : "unknown_count",
    "international"            : "international_count",
    "unknown"                  : "unknown_count",
}

# Corrected draw rates per league key
LEAGUE_DRAW_PCT = {
    "premier_count"      : 0.25,
    "serie_a_count"      : 0.32,
    "la_liga_count"      : 0.25,
    "bundesliga_count"   : 0.23,
    "ligue1_count"       : 0.27,
    "ligue2_count"       : 0.37,
    "serie_b_count"      : 0.32,
    "lega_pro_count"     : 0.31,
    "segunda_count"      : 0.35,
    "championship_count" : 0.28,
    "league_one_count"   : 0.26,
    "league_two_count"   : 0.26,
    "bundesliga2_count"  : 0.27,
    "eredivisie_count"   : 0.25,
    "primeira_count"     : 0.26,
    "liga_portugal2_count": 0.28,
    "belgium_count"      : 0.22,
    "turkey_count"       : 0.25,
    "turkey2_count"      : 0.26,
    "scottish_count"     : 0.27,
    "scottish2_count"    : 0.26,
    "swiss_count"        : 0.27,
    "austrian_count"     : 0.26,
    "greek_count"        : 0.26,
    "polish_count"       : 0.27,
    "czech_count"        : 0.27,
    "romanian_count"     : 0.26,
    "russian_count"      : 0.27,
    "slovak_count"       : 0.26,
    "slovenian_count"    : 0.26,
    "croatian_count"     : 0.27,
    "danish_count"       : 0.27,
    "nordic_count"       : 0.27,
    "israeli_count"      : 0.25,
    "mls_count"          : 0.26,
    "liga_mx_count"      : 0.28,
    "brasileirao_count"  : 0.29,
    "arg_count"          : 0.29,
    "copa_count"         : 0.30,
    "j_league_count"     : 0.24,
    "k_league_count"     : 0.25,
    "chinese_count"      : 0.26,
    "aus_count"          : 0.27,
    "sea_count"          : 0.28,
    "africa_count"       : 0.28,
    "ireland_count"      : 0.27,
    "international_count": 0.25,
    "unknown_count"      : 0.28,
}

# Draw aversion correction per league
DRAW_AVERSION = {
    "ligue2_count"      : 0.09,
    "serie_a_count"     : 0.07,
    "segunda_count"     : 0.08,
    "championship_count": 0.06,
    "serie_b_count"     : 0.07,
    "lega_pro_count"    : 0.07,
    "copa_count"        : 0.06,
    "brasileirao_count" : 0.06,
    "default"           : 0.05,
}

HIGH_DRAW_LEAGUES = {
    "ligue2_count", "serie_b_count", "lega_pro_count",
    "copa_count", "segunda_count", "serie_a_count",
    "brasileirao_count", "arg_count",
}

# DC mode classification
EUROPEAN_CUPS    = {"champions league", "europa league", "conference league",
                    "champions league qualifying", "europa league qualifying"}
INTERNATIONAL    = {"international"}
PLAYOFF_KEYWORDS = {"play-off", "playoff", "play off", "final", "semi-final",
                    "quarter-final", "cup"}

LABEL_MAP = {"1": "Home Win", "2": "Away Win", "X": "Draw"}


# ================================================================
# 1. FILE DISCOVERY
# ================================================================
def find_latest(directory, pattern):
    files = glob.glob(os.path.join(directory, pattern))
    if not files:
        raise FileNotFoundError(
            f"No files matching '{pattern}' in:\n  {directory}"
        )
    return max(files, key=os.path.getmtime)


# ================================================================
# 2. LEAGUE NORMALISATION
# ================================================================
def normalise_league(raw):
    """
    Handles slash variants ('Serie B / Serie A') by taking first part.
    Falls back to partial matching.
    """
    if not raw:
        return "unknown_count"
    primary = raw.split("/")[0].strip().lower()
    if primary in LEAGUE_MAP:
        return LEAGUE_MAP[primary]
    for key, feat in LEAGUE_MAP.items():
        if key in primary:
            return feat
    return "unknown_count"


def classify_dc_mode(tournament, country=""):
    """
    Tag each match for future Dixon-Coles routing:
        full         -> domestic league, use DC directly
        playoff      -> use DC + draw boost
        european     -> use DC + league scaling or odds fallback
        international-> skip DC, use ELO or odds
        unknown      -> odds fallback
    """
    t = tournament.lower() if tournament else ""
    c = country.lower() if country else ""

    if t in INTERNATIONAL or "international" in t:
        return "international"
    for cup in EUROPEAN_CUPS:
        if cup in t:
            return "european"
    for kw in PLAYOFF_KEYWORDS:
        if kw in t:
            return "playoff"
    if t in ("unknown", "division 1", ""):
        return "unknown"
    return "full"


def get_draw_aversion(league_key):
    return DRAW_AVERSION.get(league_key, DRAW_AVERSION["default"])


# ================================================================
# 3. BATCH LOADER  (flat list format)
# ================================================================
def load_batch():
    path = os.path.join(BATCHES_DIR, "mega_jackpot_enriched.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Batch file not found:\n  {path}\n"
            "Copy mega_jackpot_enriched.json to sportpesa/data/batches/"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    valid = []
    skipped = 0
    for item in data:
        results = [g.get("result", "") for g in item["games"]]
        if all(r in ("1", "X", "2") for r in results):
            valid.append(item)
        else:
            skipped += 1

    print(f"  Batch   : mega_jackpot_enriched.json")
    print(f"  Rounds  : {len(valid)} valid  ({skipped} skipped)")
    return valid





# ================================================================
# ================================================================
# 4. CARD LOADER  (Supabase first, local fallback)
# ================================================================
def load_card():
    # Try Supabase first (for deployed app)
    print("  Trying Supabase for latest card...")
    sys.path.insert(0, ROOT_DIR)
    from db import get_latest_card
    raw, fetched_at = get_latest_card("sportpesa")
    if raw:
        source = f"supabase:{fetched_at}"
        print(f"  Loaded card from Supabase (fetched {fetched_at})")
    else:
        print("  No card in Supabase — trying local files...")
        local_files = glob.glob(os.path.join(CARDS_DIR, "mega_jackpot_parsed_*.json"))
        if local_files:
            path = max(local_files, key=os.path.getmtime)
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            source = os.path.basename(path)
        else:
            raise FileNotFoundError(
                "No card in Supabase and no local mega_jackpot_parsed_*.json files.\n"
                "Run fetch_sportpesa_mega_jackpot.py locally first."
            )

    # Normalise to internal format
    card = []
    for m in raw:
        card.append({
            "order"       : m["order"],
            "home"        : m["home"],
            "away"        : m["away"],
            "league"      : m.get("tournament", ""),
            "country"     : m.get("country", ""),
            "odds_1"      : float(m["home_odd"]),
            "odds_x"      : float(m["draw_odd"]),
            "odds_2"      : float(m["away_odd"]),
            "dc_mode"     : classify_dc_mode(
                                m.get("tournament", ""),
                                m.get("country", "")
                            ),
            "betting_open": m.get("betting_open", True),
        })

    print(f"  Card    : {source}  [auto-detected]")
    print(f"  Matches : {len(card)}")

    from collections import Counter
    dc_counts = Counter(m["dc_mode"] for m in card)
    print(f"  DC mode : " + "  ".join(f"{k}={v}" for k, v in dc_counts.items()))

    return card, source


# ================================================================
# 5. FEATURE ENGINEERING
# ================================================================
def is_upset(game):
    o1 = game["odds_1"]
    ox = game["odds_x"]
    o2 = game["odds_2"]
    min_odds = min(o1, ox, o2)
    fav = "1" if o1 == min_odds else ("X" if ox == min_odds else "2")
    return game["result"] != fav


def compute_round_features(item):
    games = item["games"]
    n     = len(games)

    odds1   = [g["odds_1"] for g in games]
    oddsx   = [g["odds_x"] for g in games]
    odds2   = [g["odds_2"] for g in games]
    results = [g["result"] for g in games]

    total_draws = results.count("X")
    total_homes = results.count("1")
    total_aways = results.count("2")

    mirror_count    = sum(1 for h,a in zip(odds1,odds2) if abs(h-a) <= 0.25)
    clear_fav_count = sum(1 for h,a in zip(odds1,odds2) if min(h,a) <= 2.00)
    away_fav_count  = sum(1 for a in odds2 if 2.01 <= a <= 2.40)
    home_odds_weak  = sum(1 for h in odds1 if h > 2.10)
    strong_fav_trap = sum(1 for h,a in zip(odds1,odds2) if min(h,a) < 1.60)
    avg_home_odds   = float(np.mean(odds1))
    avg_away_odds   = float(np.mean(odds2))
    odds_std        = float(np.std(odds1 + oddsx + odds2))
    upset_rate      = sum(is_upset(g) for g in games) / n

    league_counts = {k: 0 for k in LEAGUE_DRAW_PCT}
    for g in games:
        key = normalise_league(g.get("league", ""))
        league_counts[key] = league_counts.get(key, 0) + 1

    draw_pct_weighted = sum(
        league_counts.get(k, 0) * v for k, v in LEAGUE_DRAW_PCT.items()
    ) / n
    high_draw_ratio = sum(
        league_counts.get(k, 0) for k in HIGH_DRAW_LEAGUES
    ) / n
    same_league_cluster = int(any(v >= 3 for v in league_counts.values()))

    return {
        "round_id"            : item["jackpot_human_id"],
        "total_draws"         : total_draws,
        "total_homes"         : total_homes,
        "total_aways"         : total_aways,
        "mirror_count"        : mirror_count,
        "clear_fav_count"     : clear_fav_count,
        "away_fav_count"      : away_fav_count,
        "home_odds_weak_count": home_odds_weak,
        "strong_fav_trap"     : strong_fav_trap,
        "avg_home_odds"       : round(avg_home_odds, 3),
        "avg_away_odds"       : round(avg_away_odds, 3),
        "odds_std"            : round(odds_std, 3),
        "upset_rate"          : round(upset_rate, 3),
        "draw_pct_weighted"   : round(draw_pct_weighted, 3),
        "high_draw_ratio"     : round(high_draw_ratio, 3),
        "same_league_cluster" : same_league_cluster,
        **league_counts,
    }


def add_temporal_features(fm):
    draws = [f["total_draws"] for f in fm]
    for i, feat in enumerate(fm):
        feat["draws_t1"]       = draws[i-1] if i >= 1 else draws[0]
        feat["draws_t2"]       = draws[i-2] if i >= 2 else draws[0]
        feat["draws_t3"]       = draws[i-3] if i >= 3 else draws[0]
        feat["upset_rate_t1"]  = (
            fm[i-1]["upset_rate"] if i >= 1 else feat["upset_rate"]
        )

        streak = 0
        j = i - 1
        while j >= 0 and draws[j] >= 7:
            streak += 1; j -= 1
        feat["draw_heavy_streak"] = streak

        streak = 0
        j = i - 1
        while j >= 0 and draws[j] <= 4:
            streak += 1; j -= 1
        feat["decisive_streak"] = streak

        feat["mirror_ge3_flag"]     = int(feat["mirror_count"] >= 3)
        feat["clear_fav_ge3_flag"]  = int(feat["clear_fav_count"] >= 3)
        feat["away_fav_ge5_flag"]   = int(feat["away_fav_count"] >= 5)
        feat["home_odds_weak_flag"] = int(feat["home_odds_weak_count"] >= 14)
        feat["strong_fav_trap_flag"]= int(feat["strong_fav_trap"] >= 2)

    return fm


def compute_card_features(card, fm):
    n     = len(card)
    odds1 = [m["odds_1"] for m in card]
    oddsx = [m["odds_x"] for m in card]
    odds2 = [m["odds_2"] for m in card]

    mirror_count    = sum(1 for h,a in zip(odds1,odds2) if abs(h-a) <= 0.25)
    clear_fav_count = sum(1 for h,a in zip(odds1,odds2) if min(h,a) <= 2.00)
    away_fav_count  = sum(1 for a in odds2 if 2.01 <= a <= 2.40)
    home_odds_weak  = sum(1 for h in odds1 if h > 2.10)
    strong_fav_trap = sum(1 for h,a in zip(odds1,odds2) if min(h,a) < 1.60)

    league_counts = {k: 0 for k in LEAGUE_DRAW_PCT}
    for m in card:
        key = normalise_league(m.get("league", ""))
        league_counts[key] = league_counts.get(key, 0) + 1

    draw_pct_weighted = sum(
        league_counts.get(k, 0) * v for k, v in LEAGUE_DRAW_PCT.items()
    ) / n
    high_draw_ratio = sum(
        league_counts.get(k, 0) for k in HIGH_DRAW_LEAGUES
    ) / n

    draws_history = [f["total_draws"] for f in fm]

    return {
        "mirror_count"       : mirror_count,
        "clear_fav_count"    : clear_fav_count,
        "away_fav_count"     : away_fav_count,
        "home_odds_weak"     : home_odds_weak,
        "strong_fav_trap"    : strong_fav_trap,
        "draw_pct_weighted"  : round(draw_pct_weighted, 3),
        "high_draw_ratio"    : round(high_draw_ratio, 3),
        "same_league_cluster": int(any(v >= 3 for v in league_counts.values())),
        "mirror_ge3_flag"    : int(mirror_count >= 3),
        "clear_fav_ge3_flag" : int(clear_fav_count >= 3),
        "away_fav_ge5_flag"  : int(away_fav_count >= 5),
        "home_odds_weak_flag": int(home_odds_weak >= 14),
        "strong_fav_trap_flag": int(strong_fav_trap >= 2),
        "draws_t1"           : draws_history[-1] if len(draws_history) >= 1 else 6,
        "draws_t2"           : draws_history[-2] if len(draws_history) >= 2 else 6,
        "draws_t3"           : draws_history[-3] if len(draws_history) >= 3 else 6,
        "draw_heavy_streak"  : fm[-1].get("draw_heavy_streak", 0),
        "decisive_streak"    : fm[-1].get("decisive_streak", 0),
    }


# ================================================================
# 6. CHRONOS FORECAST
# ================================================================
def rules_based_draw_forecast(card_feat, total_matches):
    """
    Rules-based draw count forecast using Feature Set D signals.
    Replaces Chronos total_draws output.
    Returns dict matching forecast[target] structure: P10, P50, P90, context_len,
    plus a 'regime' key and 'source' key for logging.

    IMPORTANT: Do NOT use the pre-computed mirror_ge3_flag from card_feat.
    Read mirror_count directly and apply threshold >= 4.
    mirror_ge3_flag (threshold=3) is too sensitive — a single marginal match
    can fire it on a balanced card. Threshold=4 requires a stronger signal.
    clear_fav_ge3_flag keeps its existing threshold of >= 3 (unchanged).
    """
    DRAW_THRESHOLDS = {
        "Draw-Heavy": {"P10": 7, "P50": 8, "P90": 10},
        "Balanced"  : {"P10": 4, "P50": 6, "P90": 7},
        "Decisive"  : {"P10": 3, "P50": 4, "P90": 5},
    }
    TIEBREAK_THRESHOLD = 5

    mirror_count = card_feat["mirror_count"]   # read raw count, not the flag
    clear_fav    = card_feat["clear_fav_ge3_flag"]
    draws_t1     = card_feat["draws_t1"]

    mirror = int(mirror_count >= 4)   # local threshold — overrides mirror_ge3_flag

    if mirror == 1 and clear_fav == 0:
        regime = "Draw-Heavy"
    elif clear_fav == 1 and mirror == 0:
        regime = "Decisive"
    elif mirror == 0 and clear_fav == 0:
        regime = "Balanced"
    else:
        if draws_t1 >= TIEBREAK_THRESHOLD:
            regime = "Draw-Heavy"
        else:
            regime = "Decisive"

    thresholds = DRAW_THRESHOLDS[regime]
    return {
        "P10"        : thresholds["P10"],
        "P50"        : thresholds["P50"],
        "P90"        : thresholds["P90"],
        "context_len": "rules",
        "regime"     : regime,
        "source"     : "rules_classifier",
    }


def load_chronos(model_size="small"):
    model_name = f"amazon/chronos-t5-{model_size}"
    print(f"  Loading Chronos ({model_name}) ...")
    pipeline = ChronosPipeline.from_pretrained(
        model_name,
        device_map="cpu",
        torch_dtype=torch.float32,
    )
    print(f"  Chronos loaded.\n")
    return pipeline


def chronos_forecast(pipeline, series_values, context_len, num_samples=500):
    arr = np.array(series_values, dtype=np.float32)
    if len(arr) > context_len:
        arr = arr[-context_len:]
    context_tensor = torch.tensor(arr).unsqueeze(0)
    forecast = pipeline.predict(
        inputs=context_tensor,
        prediction_length= 1,
        num_samples      = num_samples,
    )
    samples = forecast[0, :, 0].numpy()
    return (
        round(float(np.percentile(samples, 10)), 2),
        round(float(np.percentile(samples, 50)), 2),
        round(float(np.percentile(samples, 90)), 2),
    )


def run_all_forecasts(pipeline, fm, context_lengths, num_samples):
    targets = ["total_draws", "total_homes", "total_aways"]
    all_fc  = {t: {} for t in targets}

    print(f"  {'Target':<15} {'CL':>4}  {'P10':>6} {'P50':>6} "
          f"{'P90':>6}  {'Spread':>7}")
    print("  " + "-" * 52)

    for target in targets:
        series = [f[target] for f in fm]
        for cl in context_lengths:
            p10, p50, p90 = chronos_forecast(pipeline, series, cl, num_samples)
            all_fc[target][cl] = (p10, p50, p90)
            spread = round(p90 - p10, 2)
            print(f"  {target:<15} {cl:>4}  {p10:>6.2f} {p50:>6.2f} "
                  f"{p90:>6.2f}  {spread:>7.2f}")

    best = {}
    for target in targets:
        best_cl = min(
            context_lengths,
            key=lambda cl: all_fc[target][cl][2] - all_fc[target][cl][0]
        )
        p10, p50, p90 = all_fc[target][best_cl]
        best[target] = {
            "P10": p10, "P50": p50, "P90": p90,
            "context_len": best_cl,
        }

    print(f"\n  Best context: " + "  ".join(
        f"{t.split('_')[1]}={best[t]['context_len']}" for t in targets
    ))

    # --- Recency bias correction (Improvement 4) ---
    draws_series = [f["total_draws"] for f in fm]
    recent_avg   = float(np.mean(draws_series[-5:])) if len(draws_series) >= 5 \
                   else float(np.mean(draws_series))
    hist_avg     = float(np.mean(draws_series))
    corrected_p50 = round(0.60 * recent_avg + 0.40 * hist_avg, 2)
    raw_p50       = best["total_draws"]["P50"]
    delta         = corrected_p50 - raw_p50

    best["total_draws"]["P10"] = round(best["total_draws"]["P10"] + delta, 2)
    best["total_draws"]["P50"] = round(corrected_p50, 2)
    best["total_draws"]["P90"] = round(best["total_draws"]["P90"] + delta, 2)
    best["total_draws"]["recency_corrected"] = True
    best["total_draws"]["raw_p50_before_correction"] = raw_p50
    best["total_draws"]["corrected_p50"] = corrected_p50
    best["total_draws"]["delta"] = round(delta, 2)

    print(f"\n  [Recency Correction] raw_p50={raw_p50}  "
          f"recent_avg={round(recent_avg,2)}  hist_avg={round(hist_avg,2)}  "
          f"corrected_p50={corrected_p50}  delta={round(delta,2)}")

    return best


# ================================================================
# 7. TICKET GENERATION
# ================================================================
def score_match(match, base_rates, dc_probs=None):
    o1 = match["odds_1"]
    ox = match["odds_x"]
    o2 = match["odds_2"]

    raw   = {"1": 1/o1, "X": 1/ox, "2": 1/o2}
    total = sum(raw.values())
    impl  = {k: v/total for k,v in raw.items()}

    # Draw aversion correction
    league_key  = normalise_league(match.get("league", ""))
    draw_corr   = get_draw_aversion(league_key)
    impl["X"]   = min(impl["X"] + draw_corr, 0.60)
    impl_total  = sum(impl.values())
    impl        = {k: v/impl_total for k,v in impl.items()}

    # Dixon-Coles blend hook (Improvement 3)
    if dc_probs is not None:
        dc_total = sum(dc_probs.values())
        dc_norm  = {k: v / dc_total for k, v in dc_probs.items()}
        impl     = {k: 0.25 * dc_norm[k] + 0.75 * impl[k] for k in impl}
        impl_total = sum(impl.values())
        impl     = {k: v / impl_total for k, v in impl.items()}

    # Favourite calibration
    fav_odds    = min(o1, o2)
    calibration = 1.0
    if fav_odds < 1.60:
        calibration = 0.82
    elif fav_odds < 1.80:
        calibration = 0.93

    scores = {
        "1": impl["1"] * 100 * (calibration if o1 == fav_odds else 1.0),
        "X": impl["X"] * 100,
        "2": impl["2"] * 100 * (calibration if o2 == fav_odds else 1.0),
    }

    # Away bias correction
    scores["2"] += 3.0

    return scores


def clamp_counts(nd, nh, na, total=17):
    nd = max(1, min(int(round(nd)), total - 2))
    nh = max(1, min(int(round(nh)), total - nd - 1))
    na = total - nd - nh
    if na < 1:
        na = 1
        nh = total - nd - na
    return nd, nh, na


def allocate_ticket(card, target_counts, base_rates):
    n        = len(card)
    scored   = [score_match(m, base_rates) for m in card]
    assigns  = [None] * n
    assigned = set()

    for outcome in ["X", "2", "1"]:
        budget = target_counts[outcome]
        ranked = sorted(
            [(scored[i][outcome], i)
             for i in range(n) if i not in assigned],
            reverse=True
        )
        for _, idx in ranked[:budget]:
            assigns[idx] = outcome
            assigned.add(idx)

    for i in range(n):
        if assigns[i] is None:
            assigns[i] = max(["1","X","2"], key=lambda o: scored[i][o])

    return assigns, scored


def regime_label(nd):
    if nd <= 4:   return "Decisive"
    elif nd <= 7: return "Balanced"
    else:         return "Draw-Heavy"


# ================================================================
# 8. PRO TICKET GENERATOR
# ================================================================
def make_pro_ticket(base_ticket, scored, n_games):
    """
    Select top-N highest-confidence picks from the 17-game base ticket.
    Confidence = margin between top score and second-best score.
    """
    margins = []
    for i, pred in enumerate(base_ticket):
        s = scored[i]
        top_two = sorted(s.values(), reverse=True)[:2]
        margin  = top_two[0] - top_two[1] if len(top_two) > 1 else top_two[0]
        margins.append((margin, i, pred))

    top_n    = sorted(margins, reverse=True)[:n_games]
    selected = sorted(top_n, key=lambda x: x[1])  # restore card order
    return [
        {"order": i+1, "match_idx": i, "pred": pred, "confidence": round(m, 2)}
        for m, i, pred in selected
    ]


# ================================================================
# 9. PICK TYPE CLASSIFIER
# ================================================================
def classify_match(match):
    o1  = match["odds_1"]
    ox  = match["odds_x"]
    o2  = match["odds_2"]
    spread   = max(o1, ox, o2) - min(o1, ox, o2)
    fav_odds = min(o1, o2)
    league_key   = normalise_league(match.get("league", ""))
    league_draw  = LEAGUE_DRAW_PCT.get(league_key, 0.28)
    dc_mode      = match.get("dc_mode", "unknown")

    raw   = {"1": 1/o1, "X": 1/ox, "2": 1/o2}
    total = sum(raw.values())
    probs = [v/total for v in raw.values()]
    entropy = round(-sum(p * np.log(p) for p in probs if p > 0), 4)

    if dc_mode == "international":
        pick_type = "International"
        reason    = "No DC available — odds only (ELO future)"
    elif dc_mode == "european":
        pick_type = "European"
        reason    = "Cross-league match — odds fallback"
    elif spread < 0.30:
        pick_type = "Speculative"
        reason    = f"spread={round(spread,2)} < 0.30 — coin flip"
    elif fav_odds < 1.60:
        pick_type = "Double Chance"
        reason    = f"fav={fav_odds} < 1.60 — jackpot trap"
    elif spread > 0.90 and 1.60 <= fav_odds <= 2.00:
        pick_type = "Banker"
        reason    = f"spread={round(spread,2)}, fav={fav_odds}"
    elif spread < 0.40 and league_draw >= 0.30:
        pick_type = "Draw"
        reason    = f"tight match, {league_key} draw {round(league_draw*100)}%"
    else:
        pick_type = "Double Chance"
        reason    = f"spread={round(spread,2)} — moderate uncertainty"

    return {
        "pick_type"  : pick_type,
        "entropy"    : entropy,
        "spread"     : round(spread, 3),
        "reason"     : reason,
        "league_draw": round(league_draw * 100, 1),
        "dc_mode"    : dc_mode,
    }


# ================================================================
# 10. PRINT HELPERS
# ================================================================
def print_signals(card_feat):
    print(f"\n  CARD SIGNALS:")
    rows = [
        ("mirror_count",    card_feat["mirror_count"],
         card_feat["mirror_ge3_flag"],     "*** DRAW-HEAVY ***", "normal"),
        ("clear_fav_count", card_feat["clear_fav_count"],
         card_feat["clear_fav_ge3_flag"],  "*** DECISIVE ***",   "normal"),
        ("away_fav_count",  card_feat["away_fav_count"],
         card_feat["away_fav_ge5_flag"],   "*** AWAY-HEAVY ***", "normal"),
        ("strong_fav_trap", card_feat["strong_fav_trap"],
         card_feat["strong_fav_trap_flag"],"*** WARNING ***",    "ok"),
    ]
    for name, val, flag, pos, neg in rows:
        print(f"    {name:<20} = {val}  ->  {pos if flag else neg}")

    print(f"\n    draws_t1={card_feat['draws_t1']}  "
          f"draws_t2={card_feat['draws_t2']}  "
          f"draws_t3={card_feat['draws_t3']}  "
          f"draw_heavy_streak={card_feat['draw_heavy_streak']}  "
          f"decisive_streak={card_feat['decisive_streak']}")
    print(f"    draw_pct_weighted={card_feat['draw_pct_weighted']}  "
          f"high_draw_ratio={card_feat['high_draw_ratio']}  "
          f"same_league_cluster={card_feat['same_league_cluster']}")


def print_pick_summary(classifications):
    from collections import Counter
    counts = Counter(c["pick_type"] for c in classifications)
    print(f"\n  PICK TYPE SUMMARY:")
    for pt in ["Banker","Draw","Double Chance","Speculative",
               "European","International"]:
        n   = counts.get(pt, 0)
        bar = "█" * n
        print(f"    {pt:<14} {n:>2}  {bar}")

    specs = [c for c in classifications if c["pick_type"] == "Speculative"]
    if specs:
        print(f"\n  SPECULATIVE MATCHES — consider double coverage:")
        for i, c in enumerate(classifications):
            if c["pick_type"] == "Speculative":
                print(f"    [{i+1}] {c['reason']}")


def print_ticket(ticket, card, classifications, label, counts, n=17):
    h = counts["1"]; d = counts["X"]; a = counts["2"]
    print(f"\n  [{label}]  H={h}  D={d}  A={a}  "
          f"Regime: {regime_label(d)}")
    print(f"  {'#':<4} {'Match':<40} {'Pred':<5} {'Type':<14} "
          f"{'DC':>5}  {'Entr':>6}")
    print("  " + "-" * 76)
    for i in range(n):
        c       = classifications[i]
        matchup = f"{card[i]['home']} vs {card[i]['away']}"
        print(f"  {i+1:<4} {matchup:<40} {ticket[i]:<5} "
              f"{c['pick_type']:<14} {c['dc_mode']:>5}  {c['entropy']:>6.3f}")
    print(f"\n  Ticket: {' - '.join(ticket)}")


def print_pro_ticket(pro, card, label):
    print(f"\n  [{label}]  ({len(pro)} games)")
    print(f"  {'#':<4} {'Match':<40} {'Pred':<5} {'Conf':>7}")
    print("  " + "-" * 60)
    for p in pro:
        i       = p["match_idx"]
        matchup = f"{card[i]['home']} vs {card[i]['away']}"
        print(f"  {p['order']:<4} {matchup:<40} {p['pred']:<5} "
              f"{p['confidence']:>7.2f}")
    print(f"\n  Ticket: {' - '.join(p['pred'] for p in pro)}")


# ================================================================
# 11. MAIN
# ================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="small",
                        choices=["tiny","small","base","large"])
    parser.add_argument("--samples", type=int, default=500)
    args = parser.parse_args()

    print("=" * 76)
    print("  SPORTPESA MEGA JACKPOT FORECAST — Chronos Edition")
    print(f"  Model: chronos-t5-{args.model}  |  Samples: {args.samples}")
    print("=" * 76)

    # --- Load ---
    rounds      = load_batch()
    card, card_name = load_card()

    # --- Base rates ---
    all_results = [g["result"] for item in rounds for g in item["games"]]
    total_m     = len(all_results)
    base_rates  = {
        "1": all_results.count("1") / total_m,
        "X": all_results.count("X") / total_m,
        "2": all_results.count("2") / total_m,
    }
    print(f"\n  Base rates: "
          f"Home {round(base_rates['1']*100,1)}%  "
          f"Draw {round(base_rates['X']*100,1)}%  "
          f"Away {round(base_rates['2']*100,1)}%")

    # --- Features ---
    print(f"\n  [Stage 1] Feature engineering ...")
    fm = [compute_round_features(r) for r in rounds]
    fm = add_temporal_features(fm)
    print(f"  Features computed for {len(fm)} rounds")

    latest = fm[-1]
    print(f"\n  Latest round (id={latest['round_id']}): "
          f"Draws={latest['total_draws']}  "
          f"Homes={latest['total_homes']}  "
          f"Aways={latest['total_aways']}")

    card_feat = compute_card_features(card, fm)
    print_signals(card_feat)

    # --- Pick type classification ---
    classifications = [classify_match(m) for m in card]
    print_pick_summary(classifications)

    # --- Chronos ---
    print(f"\n  [Stage 2] Chronos forecast ...")
    pipeline = load_chronos(args.model)
    context_lengths = [5, 10, 42]
    forecast = run_all_forecasts(pipeline, fm, context_lengths, args.samples)

    # --- Rules-based draw override (Improvement 1) ---
    rules_draw = rules_based_draw_forecast(card_feat, len(card))
    forecast["total_draws"] = rules_draw
    print(f"\n  [Draw Override] Regime: {rules_draw['regime']}  "
          f"P10={rules_draw['P10']}  P50={rules_draw['P50']}  P90={rules_draw['P90']}")

    print(f"\n  FORECAST SUMMARY:")
    print(f"  {'Target':<15} {'P10':>6} {'P50':>6} {'P90':>6}  CL")
    print("  " + "-" * 42)
    for t in ["total_draws","total_homes","total_aways"]:
        f = forecast[t]
        print(f"  {t:<15} {f['P10']:>6.1f} {f['P50']:>6.1f} "
              f"{f['P90']:>6.1f}  {f['context_len']}")

    # --- Build tickets ---
    print(f"\n  [Stage 3] Building tickets ...")
    scenarios = {
        "conservative": "P10",
        "base"        : "P50",
        "draw_heavy"  : "P90",
    }

    tickets     = {}
    counts_out  = {}
    base_scored = None

    for scenario, pct in scenarios.items():
        nd = forecast["total_draws"][pct]
        nh = forecast["total_homes"][pct]
        na = forecast["total_aways"][pct]
        nd, nh, na = clamp_counts(nd, nh, na, total=17)
        counts = {"1": nh, "X": nd, "2": na}
        counts_out[scenario] = counts
        ticket, scored = allocate_ticket(card, counts, base_rates)
        tickets[scenario] = ticket
        if scenario == "base":
            base_scored = scored

    # --- Pro tickets ---
    pro_tickets = {}
    for n_games in [16, 15, 14, 13]:
        pro_tickets[f"pro_{n_games}"] = make_pro_ticket(
            tickets["base"], base_scored, n_games
        )

    # --- Print ---
    print("\n" + "=" * 76)
    print("  PREDICTION TICKETS")
    print(f"  Card: {card_name}")
    print("=" * 76)

    for scenario, pct in scenarios.items():
        print_ticket(
            tickets[scenario], card, classifications,
            f"{scenario.upper()} ({pct})",
            counts_out[scenario], n=17
        )

    print("\n" + "=" * 76)
    print("  PRO VARIANT TICKETS  (top confidence from Base)")
    print("=" * 76)
    for key, pro in pro_tickets.items():
        n_games = int(key.split("_")[1])
        print_pro_ticket(pro, card, f"PRO-{n_games}")

    # --- Save ---
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_name  = f"sportpesa_forecast_{timestamp}.json"
    out_path  = os.path.join(OUTPUT_DIR, out_name)

    output = {
        "generated_at"  : timestamp,
        "card_file"     : card_name,
        "model"         : f"chronos-t5-{args.model}",
        "num_samples"   : args.samples,
        "num_games"     : 17,
        "base_rates"    : {k: round(v,4) for k,v in base_rates.items()},
        "card_signals"  : card_feat,
        "draw_classifier": {
            "regime" : rules_draw["regime"],
            "source" : "rules_classifier",
            "P10"    : rules_draw["P10"],
            "P50"    : rules_draw["P50"],
            "P90"    : rules_draw["P90"],
            "flags"  : {
                "mirror_count"         : card_feat["mirror_count"],
                "mirror_ge4_used"      : int(card_feat["mirror_count"] >= 4),
                "mirror_ge3_flag_raw"  : card_feat["mirror_ge3_flag"],
                "clear_fav_ge3"        : card_feat["clear_fav_ge3_flag"],
                "draws_t1"             : card_feat["draws_t1"],
            },
        },
        "forecast"      : forecast,
        "tickets"       : {
            scenario: {
                "percentile"   : pct,
                "target_counts": counts_out[scenario],
                "regime"       : regime_label(counts_out[scenario]["X"]),
                "ticket"       : tickets[scenario],
                "matches"      : [
                    {
                        "order"    : i+1,
                        "home"     : card[i]["home"],
                        "away"     : card[i]["away"],
                        "league"   : card[i]["league"],
                        "pred"     : tickets[scenario][i],
                        "label"    : LABEL_MAP[tickets[scenario][i]],
                        "pick_type": classifications[i]["pick_type"],
                        "dc_mode"  : classifications[i]["dc_mode"],
                        "entropy"  : classifications[i]["entropy"],
                    }
                    for i in range(17)
                ],
            }
            for scenario, pct in scenarios.items()
        },
        "pro_tickets"   : {
            key: [
                {
                    "order"     : p["order"],
                    "home"      : card[p["match_idx"]]["home"],
                    "away"      : card[p["match_idx"]]["away"],
                    "pred"      : p["pred"],
                    "label"     : LABEL_MAP[p["pred"]],
                    "confidence": p["confidence"],
                }
                for p in pro
            ]
            for key, pro in pro_tickets.items()
        },
        "match_analysis": [
            {
                "order"          : i+1,
                "home"           : card[i]["home"],
                "away"           : card[i]["away"],
                "league"         : card[i]["league"],
                "pick_type"      : classifications[i]["pick_type"],
                "dc_mode"        : classifications[i]["dc_mode"],
                "entropy"        : classifications[i]["entropy"],
                "spread"         : classifications[i]["spread"],
                "reason"         : classifications[i]["reason"],
                "league_draw_pct": classifications[i]["league_draw"],
            }
            for i in range(17)
        ],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Saved: sportpesa/output/{out_name}")
    print("=" * 76)


if __name__ == "__main__":
    main()
