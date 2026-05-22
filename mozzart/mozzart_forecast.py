"""
mozzart_forecast.py  —  Mozzart Daily Jackpot Forecast (Chronos)
================================================================
Replaces predict.py from the TimesFM project.

Directory structure (run from C:/Users/User/Desktop/Chronos/):
    mozzart/data/batches/    <- batch-1.json, batch-2.json, batch-3.json
    mozzart/data/cards/      <- mozzart_daily_*.json  (this week's card)
    mozzart/data/enriched/   <- footystats_enriched_*.json (optional)
    mozzart/output/          <- all prediction outputs

Usage:
    cd C:/Users/User/Desktop/Chronos
    python mozzart/mozzart_forecast.py

What Chronos does here:
    Forecasts round-level distribution (how many draws/homes/aways)
    using genuine quantile outputs — P10, P50, P90 — from the model's
    learned probability distribution. No approximation hacks.

What the allocator does:
    Given the TimesFM budget (H=N, D=N, A=N), assigns specific matches
    to each outcome using odds-implied probability + draw aversion
    correction + away bias correction from backtest findings.

Three tickets produced:
    Conservative  <- P10 draw count (fewer draws expected)
    Base          <- P50 draw count (primary ticket)
    Draw-Heavy    <- P90 draw count (more draws expected)
"""

import json
import glob
import os
import sys
import argparse
import numpy as np
import pandas as pd
import torch
from chronos import ChronosPipeline
from datetime import datetime

# ================================================================
# PATHS  (relative to C:/Users/User/Desktop/Chronos/)
# ================================================================
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))  # mozzart/
ROOT_DIR     = os.path.dirname(BASE_DIR)                   # Chronos/
BATCHES_DIR  = os.path.join(BASE_DIR, "data", "batches")
CARDS_DIR    = os.path.join(BASE_DIR, "data", "cards")
ENRICHED_DIR = os.path.join(BASE_DIR, "data", "enriched")
OUTPUT_DIR   = os.path.join(BASE_DIR, "output")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ================================================================
# LEAGUE MAP  (string -> feature key)
# ================================================================
LEAGUE_MAP = {
    "france - ligue 2"            : "ligue2_count",
    "italy - serie b"             : "serie_b_count",
    "italy - lega pro"            : "lega_pro_count",
    "copa sudamericana"           : "copa_count",
    "copa libertadores"           : "copa_count",
    "netherlands - eerste divisie": "eerste_divisie_count",
    "netherlands - erste divisie" : "eerste_divisie_count",
    "england - the championship"  : "championship_count",
    "england - premier"           : "premier_count",
    "italy - serie a"             : "serie_a_count",
    "spain - primera division"    : "la_liga_count",
    "spain - segunda division"    : "segunda_count",
    "england - league one"        : "league_one_count",
    "england - league two"        : "league_two_count",
    "germany - 1. bundesliga"     : "bundesliga_count",
    "germany - 2. bundesliga"     : "bundesliga2_count",
    "france - ligue 1"            : "ligue1_count",
    "turkey - 1. super ligi"      : "turkey_count",
    "belgium 1 play off i"        : "belgium_count",
    "belgium 1 play off ii"       : "belgium_count",
    "scotland premier play out"   : "scottish_count",
    "scotland first division"     : "scottish_count",
    "scotland - premiership"      : "scottish_count",
    "norway 2"                    : "nordic_count",
    "norway - eliteserien"        : "nordic_count",
    "denmark - superliga"         : "danish_count",
    "switzerland - challenge league": "swiss_count",
}

# Corrected draw rates from knowledge base
LEAGUE_DRAW_PCT = {
    "ligue2_count"        : 0.37,
    "serie_b_count"       : 0.32,
    "lega_pro_count"      : 0.31,
    "copa_count"          : 0.30,
    "eerste_divisie_count": 0.20,
    "championship_count"  : 0.28,
    "premier_count"       : 0.25,
    "bundesliga2_count"   : 0.27,
    "segunda_count"       : 0.35,
    "league_one_count"    : 0.26,
    "league_two_count"    : 0.26,
    "ligue1_count"        : 0.27,
    "bundesliga_count"    : 0.23,
    "la_liga_count"       : 0.25,
    "serie_a_count"       : 0.32,
    "turkey_count"        : 0.25,
    "belgium_count"       : 0.22,
    "scottish_count"      : 0.27,
    "nordic_count"        : 0.27,
    "danish_count"        : 0.27,
    "swiss_count"         : 0.27,
    "unknown_count"       : 0.28,
}

# Draw aversion correction (draws undervalued in markets)
DRAW_AVERSION = {
    "ligue2_count"      : 0.09,
    "serie_a_count"     : 0.07,
    "segunda_count"     : 0.08,
    "championship_count": 0.06,
    "serie_b_count"     : 0.07,
    "default"           : 0.05,
}

HIGH_DRAW_LEAGUES = {
    "ligue2_count", "serie_b_count", "lega_pro_count",
    "copa_count", "segunda_count", "serie_a_count",
}

LABEL_MAP = {"1": "Home Win", "2": "Away Win", "X": "Draw"}


# ================================================================
# 1. FILE DISCOVERY
# ================================================================
def find_latest(directory, pattern):
    files = glob.glob(os.path.join(directory, pattern))
    if not files:
        raise FileNotFoundError(
            f"No files matching '{pattern}' in {directory}"
        )
    return max(files, key=os.path.getmtime)


# ================================================================
# 2. LEAGUE NORMALISATION
# ================================================================
def normalise_league(raw):
    if not raw:
        return "unknown_count"
    key = raw.lower().strip()
    for pattern, feat in LEAGUE_MAP.items():
        if pattern in key:
            return feat
    return "unknown_count"


def get_draw_aversion(league_key):
    return DRAW_AVERSION.get(league_key, DRAW_AVERSION["default"])


# ================================================================
# 3. BATCH LOADER
# ================================================================
def load_batches():
    files = sorted(glob.glob(os.path.join(BATCHES_DIR, "batch-*.json")))
    if not files:
        raise FileNotFoundError(f"No batch files in {BATCHES_DIR}")

    rounds = []
    for fpath in files:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        for rnd in data["rounds"]:
            results = [m.get("result", "") for m in rnd["matches"]]
            if all(r in ("1", "X", "2") for r in results):
                rounds.append(rnd)
            else:
                print(f"  [SKIP] round {rnd.get('round_id')} — incomplete")

    print(f"  Batches : {[os.path.basename(f) for f in files]}")
    print(f"  Rounds  : {len(rounds)} valid rounds loaded")
    return rounds


# ================================================================
# 4. CARD LOADER
# ================================================================
def load_card():
    today = datetime.now().strftime("%Y-%m-%d")
    today_files = glob.glob(os.path.join(CARDS_DIR, f"mozzart_daily_{today}_*.json"))
    if today_files:
        path = max(today_files, key=os.path.getmtime)
        print(f"  Card    : {os.path.basename(path)}  [today's card]")
    else:
        path = find_latest(CARDS_DIR, "mozzart_daily_*.json")
        print(f"  Card    : {os.path.basename(path)}  [latest available — no today's card found]")
    with open(path, encoding="utf-8") as f:
        card = json.load(f)
    print(f"  Matches : {len(card)}")
    return card, os.path.basename(path)


# ================================================================
# 5. FEATURE ENGINEERING
# ================================================================
def is_upset(match):
    o1 = float(match["odds"]["1"])
    ox = float(match["odds"]["X"])
    o2 = float(match["odds"]["2"])
    min_odds = min(o1, ox, o2)
    fav = "1" if o1 == min_odds else ("X" if ox == min_odds else "2")
    return match["result"] != fav


def compute_round_features(rnd):
    matches = rnd["matches"]
    n       = len(matches)

    odds1   = [float(m["odds"]["1"]) for m in matches]
    oddsx   = [float(m["odds"]["X"]) for m in matches]
    odds2   = [float(m["odds"]["2"]) for m in matches]
    results = [m["result"] for m in matches]

    total_draws = results.count("X")
    total_homes = results.count("1")
    total_aways = results.count("2")

    mirror_count      = sum(1 for h,a in zip(odds1,odds2) if abs(h-a) <= 0.25)
    clear_fav_count   = sum(1 for h,a in zip(odds1,odds2) if min(h,a) <= 2.00)
    away_fav_count    = sum(1 for a in odds2 if 2.01 <= a <= 2.40)
    home_odds_weak    = sum(1 for h in odds1 if h > 2.10)
    strong_fav_trap   = sum(1 for h,a in zip(odds1,odds2) if min(h,a) < 1.60)
    avg_home_odds     = float(np.mean(odds1))
    avg_away_odds     = float(np.mean(odds2))
    odds_std          = float(np.std(odds1 + oddsx + odds2))
    upset_rate        = sum(is_upset(m) for m in matches) / n

    league_counts = {k: 0 for k in LEAGUE_DRAW_PCT}
    for m in matches:
        key = normalise_league(m.get("league", ""))
        league_counts[key] = league_counts.get(key, 0) + 1

    draw_pct_weighted = sum(
        league_counts.get(k, 0) * v for k, v in LEAGUE_DRAW_PCT.items()
    ) / n
    high_draw_ratio = sum(
        league_counts.get(k, 0) for k in HIGH_DRAW_LEAGUES
    ) / n
    same_league_cluster = int(any(v >= 3 for v in league_counts.values()))

    return {
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
        feat["draws_t1"]      = draws[i-1] if i >= 1 else draws[0]
        feat["draws_t2"]      = draws[i-2] if i >= 2 else draws[0]
        feat["draws_t3"]      = draws[i-3] if i >= 3 else draws[0]
        feat["upset_rate_t1"] = fm[i-1]["upset_rate"] if i >= 1 else feat["upset_rate"]

        streak = 0
        j = i - 1
        while j >= 0 and draws[j] >= 7:
            streak += 1
            j -= 1
        feat["draw_heavy_streak"] = streak

        streak = 0
        j = i - 1
        while j >= 0 and draws[j] <= 3:
            streak += 1
            j -= 1
        feat["decisive_streak"] = streak

        feat["mirror_ge3_flag"]    = int(feat["mirror_count"] >= 3)
        feat["clear_fav_ge3_flag"] = int(feat["clear_fav_count"] >= 3)
        feat["away_fav_ge5_flag"]  = int(feat["away_fav_count"] >= 5)
        feat["home_odds_weak_flag"]= int(feat["home_odds_weak_count"] >= 13)
        feat["strong_fav_trap_flag"]= int(feat["strong_fav_trap"] >= 2)

    return fm


def compute_card_features(card, fm):
    """Compute features for the new card + temporal context from history."""
    n     = len(card)
    odds1 = [float(m["odds"]["1"]) for m in card]
    oddsx = [float(m["odds"]["X"]) for m in card]
    odds2 = [float(m["odds"]["2"]) for m in card]

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
        "mirror_count"      : mirror_count,
        "clear_fav_count"   : clear_fav_count,
        "away_fav_count"    : away_fav_count,
        "home_odds_weak"    : home_odds_weak,
        "strong_fav_trap"   : strong_fav_trap,
        "draw_pct_weighted" : round(draw_pct_weighted, 3),
        "high_draw_ratio"   : round(high_draw_ratio, 3),
        "same_league_cluster": int(any(v >= 3 for v in league_counts.values())),
        "mirror_ge3_flag"   : int(mirror_count >= 3),
        "clear_fav_ge3_flag": int(clear_fav_count >= 3),
        "away_fav_ge5_flag" : int(away_fav_count >= 5),
        "home_odds_weak_flag": int(home_odds_weak >= 13),
        "strong_fav_trap_flag": int(strong_fav_trap >= 2),
        "draws_t1"          : draws_history[-1] if len(draws_history) >= 1 else 5,
        "draws_t2"          : draws_history[-2] if len(draws_history) >= 2 else 5,
        "draws_t3"          : draws_history[-3] if len(draws_history) >= 3 else 5,
        "draw_heavy_streak" : fm[-1].get("draw_heavy_streak", 0),
        "decisive_streak"   : fm[-1].get("decisive_streak", 0),
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
        "Draw-Heavy": {"P10": 7, "P50": 8, "P90": 9},
        "Balanced"  : {"P10": 4, "P50": 5, "P90": 6},
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
    """
    Load Chronos pipeline.
    model_size: 'tiny' (fastest), 'small' (recommended), 'base', 'large'
    """
    model_name = f"amazon/chronos-t5-{model_size}"
    print(f"  Loading Chronos ({model_name}) ...")
    pipeline = ChronosPipeline.from_pretrained(
        model_name,
        device_map="cpu",       # CPU — no GPU required
        torch_dtype=torch.float32,
    )
    print(f"  Chronos loaded.\n")
    return pipeline


def chronos_forecast(pipeline, series_values, context_len,
                     num_samples=500):
    """
    Run Chronos forecast on a 1-D series.

    Returns genuine P10, P50, P90 from the model's sampled distribution
    — not approximated from std. This is Chronos's key advantage over
    TimesFM.

    Args:
        series_values : list of floats (historical values)
        context_len   : how many recent values to use as context
        num_samples   : number of distribution samples (higher = more
                        accurate quantiles, slower)

    Returns:
        (p10, p50, p90) as floats
    """
    arr = np.array(series_values, dtype=np.float32)

    # Use last context_len values
    if len(arr) > context_len:
        arr = arr[-context_len:]

    # Chronos expects a torch tensor
    context_tensor = torch.tensor(arr).unsqueeze(0)  # shape [1, context_len]

    # forecast() returns samples: shape [batch, num_samples, horizon]
    forecast = pipeline.predict(
        inputs           = context_tensor,
        prediction_length= 1,
        num_samples      = num_samples,
    )

    # Extract samples for horizon step 0
    samples = forecast[0, :, 0].numpy()  # shape [num_samples]

    p10 = float(np.percentile(samples, 10))
    p50 = float(np.percentile(samples, 50))
    p90 = float(np.percentile(samples, 90))

    return round(p10, 2), round(p50, 2), round(p90, 2)


def run_all_forecasts(pipeline, fm, context_lengths):
    """
    Forecast total_draws, total_homes, total_aways across all
    context lengths. Select tightest P10-P90 spread per target.
    """
    targets = ["total_draws", "total_homes", "total_aways"]
    all_fc  = {t: {} for t in targets}

    print(f"  {'Target':<15} {'CL':>4}  {'P10':>6} {'P50':>6} "
          f"{'P90':>6}  {'Spread':>7}")
    print("  " + "-" * 52)

    for target in targets:
        series = [f[target] for f in fm]
        for cl in context_lengths:
            p10, p50, p90 = chronos_forecast(pipeline, series, cl)
            all_fc[target][cl] = (p10, p50, p90)
            spread = round(p90 - p10, 2)
            print(f"  {target:<15} {cl:>4}  {p10:>6.2f} {p50:>6.2f} "
                  f"{p90:>6.2f}  {spread:>7.2f}")

    # Select tightest spread per target
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
    """
    Per-match outcome score:
      - Implied probability from odds
      - Draw aversion correction (draws undervalued in markets)
      - Favourite calibration (fav < 1.60 deflated — jackpot trap)
      - Away bias correction (+3 from backtest finding)
    """
    o1 = float(match["odds"]["1"])
    ox = float(match["odds"]["X"])
    o2 = float(match["odds"]["2"])

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

    # Away bias correction from backtest
    scores["2"] += 3.0

    return scores


def clamp_counts(nd, nh, na, total=16):
    nd = max(1, min(int(round(nd)), total - 2))
    nh = max(1, min(int(round(nh)), total - nd - 1))
    na = total - nd - nh
    if na < 1:
        na = 1
        nh = total - nd - na
    return nd, nh, na


def allocate_ticket(card, target_counts, base_rates):
    """
    Greedy allocation respecting TimesFM budget.
    Order: Draws first (hardest), Aways second (bias correction),
           Homes last.
    """
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
    if nd <= 3:   return "Decisive"
    elif nd <= 6: return "Balanced"
    else:         return "Draw-Heavy"


# ================================================================
# 8. PICK TYPE CLASSIFIER  (Step 3)
# ================================================================
def classify_match(match):
    o1  = float(match["odds"]["1"])
    ox  = float(match["odds"]["X"])
    o2  = float(match["odds"]["2"])
    spread    = max(o1, ox, o2) - min(o1, ox, o2)
    fav_odds  = min(o1, o2)
    league_key= normalise_league(match.get("league",""))
    league_draw = LEAGUE_DRAW_PCT.get(league_key, 0.28)

    # Shannon entropy of implied probs
    raw   = {"1": 1/o1, "X": 1/ox, "2": 1/o2}
    total = sum(raw.values())
    probs = [v/total for v in raw.values()]
    entropy = round(-sum(p * np.log(p) for p in probs if p > 0), 4)

    if spread < 0.30:
        pick_type = "Speculative"
        reason    = f"spread={round(spread,2)} < 0.30 — coin flip, avoid single"
    elif fav_odds < 1.60:
        pick_type = "Double Chance"
        reason    = f"fav={fav_odds} < 1.60 — jackpot trap (Section 3.2)"
    elif spread > 0.90 and 1.60 <= fav_odds <= 2.00:
        pick_type = "Banker"
        reason    = f"spread={round(spread,2)}, fav={fav_odds} — calibrated zone"
    elif spread < 0.40 and league_draw >= 0.30:
        pick_type = "Draw"
        reason    = f"tight match, {league_key} draw rate {round(league_draw*100)}%"
    else:
        pick_type = "Double Chance"
        reason    = f"spread={round(spread,2)} — moderate uncertainty"

    return {
        "pick_type"  : pick_type,
        "entropy"    : entropy,
        "spread"     : round(spread, 3),
        "reason"     : reason,
        "league_draw": round(league_draw * 100, 1),
    }


# ================================================================
# 9. PRINT HELPERS
# ================================================================
def print_signals(card_feat):
    print(f"\n  CARD SIGNALS:")
    signals = [
        ("mirror_count",    card_feat["mirror_count"],
         card_feat["mirror_ge3_flag"],    "DRAW-HEAVY",  "normal"),
        ("clear_fav_count", card_feat["clear_fav_count"],
         card_feat["clear_fav_ge3_flag"], "DECISIVE",    "normal"),
        ("away_fav_count",  card_feat["away_fav_count"],
         card_feat["away_fav_ge5_flag"],  "AWAY-HEAVY",  "normal"),
        ("strong_fav_trap", card_feat["strong_fav_trap"],
         card_feat["strong_fav_trap_flag"], "WARNING",   "ok"),
    ]
    for name, val, flag, pos, neg in signals:
        label = f"*** {pos} ***" if flag else neg
        print(f"    {name:<20} = {val}  →  {label}")

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
    for pt in ["Banker", "Draw", "Double Chance", "Speculative"]:
        n   = counts.get(pt, 0)
        bar = "█" * n
        print(f"    {pt:<14} {n:>2}  {bar}")
    specs = [c for c in classifications if c["pick_type"] == "Speculative"]
    if specs:
        print(f"\n  ⚠ SPECULATIVE MATCHES — consider double coverage:")
        for i, c in enumerate(classifications):
            if c["pick_type"] == "Speculative":
                print(f"    [{i+1}] {c['reason']}")


def print_ticket(ticket, card, classifications, label, counts):
    h = counts["1"]; d = counts["X"]; a = counts["2"]
    print(f"\n  [{label}]  H={h}  D={d}  A={a}  "
          f"Regime: {regime_label(d)}")
    print(f"  {'#':<4} {'Match':<36} {'Pred':<5} {'Type':<14} "
          f"{'Entr':>6}  League")
    print("  " + "-" * 74)
    for i, (pred, m) in enumerate(zip(ticket, card)):
        c       = classifications[i]
        matchup = f"{m['home']} vs {m['away']}"
        league  = m.get("league", "")
        print(f"  {i+1:<4} {matchup:<36} {pred:<5} "
              f"{c['pick_type']:<14} {c['entropy']:>6.3f}  {league}")
    print(f"\n  Ticket: {' - '.join(ticket)}")


# ================================================================
# 10. MAIN
# ================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", default="small",
        choices=["tiny","small","base","large"],
        help="Chronos model size (default: small)"
    )
    parser.add_argument(
        "--samples", type=int, default=500,
        help="Number of forecast samples for quantiles (default: 500)"
    )
    args = parser.parse_args()

    print("=" * 74)
    print("  MOZZART DAILY JACKPOT FORECAST — Chronos Edition")
    print(f"  Model: chronos-t5-{args.model}  |  Samples: {args.samples}")
    print("=" * 74)

    # --- Load data ---
    rounds      = load_batches()
    card, card_name = load_card()

    # --- Base rates ---
    all_results = [m["result"] for r in rounds for m in r["matches"]]
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

    # --- Feature engineering ---
    print(f"\n  [Stage 1] Feature engineering ...")
    fm = [compute_round_features(r) for r in rounds]
    fm = add_temporal_features(fm)
    print(f"  Features computed for {len(fm)} rounds")

    latest = fm[-1]
    print(f"\n  Latest round: "
          f"Draws={latest['total_draws']}  "
          f"Homes={latest['total_homes']}  "
          f"Aways={latest['total_aways']}  "
          f"mirror={latest['mirror_count']}  "
          f"clear_fav={latest['clear_fav_count']}")

    card_feat = compute_card_features(card, fm)
    print_signals(card_feat)

    # --- Pick type classification ---
    classifications = [classify_match(m) for m in card]
    print_pick_summary(classifications)

    # --- Chronos forecast ---
    print(f"\n  [Stage 2] Chronos forecast ...")
    pipeline = load_chronos(args.model)

    context_lengths = [5, 10, 31]
    forecast = run_all_forecasts(pipeline, fm, context_lengths)

    # --- Rules-based draw override (Improvement 1) ---
    rules_draw = rules_based_draw_forecast(card_feat, len(card))
    forecast["total_draws"] = rules_draw
    print(f"\n  [Draw Override] Regime: {rules_draw['regime']}  "
          f"P10={rules_draw['P10']}  P50={rules_draw['P50']}  P90={rules_draw['P90']}")

    print(f"\n  FORECAST SUMMARY:")
    print(f"  {'Target':<15} {'P10':>6} {'P50':>6} {'P90':>6}  CL")
    print("  " + "-" * 40)
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

    tickets    = {}
    counts_out = {}
    base_scored = None

    for scenario, pct in scenarios.items():
        nd = forecast["total_draws"][pct]
        nh = forecast["total_homes"][pct]
        na = forecast["total_aways"][pct]
        nd, nh, na = clamp_counts(nd, nh, na, total=16)
        counts = {"1": nh, "X": nd, "2": na}
        counts_out[scenario] = counts
        ticket, scored = allocate_ticket(card, counts, base_rates)
        tickets[scenario] = ticket
        if scenario == "base":
            base_scored = scored

    # --- Print tickets ---
    print("\n" + "=" * 74)
    print("  PREDICTION TICKETS")
    print(f"  Card: {card_name}")
    print("=" * 74)

    for scenario, pct in scenarios.items():
        print_ticket(
            tickets[scenario], card, classifications,
            f"{scenario.upper()} ({pct})",
            counts_out[scenario]
        )

    # --- Save output ---
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_name  = f"mozzart_forecast_{timestamp}.json"
    out_path  = os.path.join(OUTPUT_DIR, out_name)

    output = {
        "generated_at"  : timestamp,
        "card_file"     : card_name,
        "model"         : f"chronos-t5-{args.model}",
        "num_samples"   : args.samples,
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
                        "index"    : i+1,
                        "home"     : card[i]["home"],
                        "away"     : card[i]["away"],
                        "league"   : card[i].get("league",""),
                        "pred"     : tickets[scenario][i],
                        "label"    : LABEL_MAP[tickets[scenario][i]],
                        "pick_type": classifications[i]["pick_type"],
                        "entropy"  : classifications[i]["entropy"],
                    }
                    for i in range(len(card))
                ],
            }
            for scenario, pct in scenarios.items()
        },
        "match_analysis": [
            {
                "index"     : i+1,
                "home"      : card[i]["home"],
                "away"      : card[i]["away"],
                "league"    : card[i].get("league",""),
                "pick_type" : classifications[i]["pick_type"],
                "entropy"   : classifications[i]["entropy"],
                "spread"    : classifications[i]["spread"],
                "reason"    : classifications[i]["reason"],
                "league_draw_pct": classifications[i]["league_draw"],
            }
            for i in range(len(card))
        ],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Saved: mozzart/output/{out_name}")
    print("=" * 74)


if __name__ == "__main__":
    main()
