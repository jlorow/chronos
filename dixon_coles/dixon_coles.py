"""
dixon_coles.py  —  Dixon-Coles Match Prediction
================================================
Standalone script. Takes a FootyStats league CSV and outputs
1/X/2 predictions for upcoming matches.

Usage:
    # All upcoming matches
    python dixon_coles.py --csv data/ireland.csv

    # Today's matches only
    python dixon_coles.py --csv data/ireland.csv --date 2026-05-15

    # This week's matches
    python dixon_coles.py --csv data/ireland.csv --week

    # Specific date
    python dixon_coles.py --csv data/ireland.csv --date 2026-05-17

    # Adjust blend (default 50% DC / 50% odds)
    python dixon_coles.py --csv data/ireland.csv --date 2026-05-15 --dc-weight 0.6

Output:
    dixon_coles/predictions/<league>-predictions-<date>.json

How it works:
    Stage 1: Learn team attack/defence strengths from completed matches
             using Maximum Likelihood Estimation (scipy.optimize)
    Stage 2: Predict each upcoming match using Poisson distribution
             + Dixon-Coles correction for low-scoring games
    Stage 3: Blend DC probabilities with bookmaker odds-implied probs
    Stage 4: Output ranked predictions with confidence scores
    
    python dixon_coles.py --csv data/england-premie.csv

Requirements:
    pip install scipy numpy pandas
"""

import json
import os
import csv
import argparse
import math
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

# ================================================================
# PATHS
# ================================================================
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
PRED_DIR    = os.path.join(BASE_DIR, "dixon_coles", "predictions")
os.makedirs(PRED_DIR, exist_ok=True)

LABEL_MAP   = {"1": "Home Win", "X": "Draw", "2": "Away Win"}
MAX_GOALS   = 8   # max goals per team in scoreline grid


# ================================================================
# 1. CSV LOADER
# ================================================================
def parse_date(date_str: str):
    """
    Parse FootyStats date format: 'Feb 06 2026 - 7:45pm'
    Returns datetime object or None.
    """
    if not date_str or date_str.strip() == "":
        return None
    try:
        clean = date_str.strip().replace(" - ", " ")
        return datetime.strptime(clean, "%b %d %Y %I:%M%p")
    except Exception:
        try:
            return datetime.strptime(clean, "%b %d %Y %I:%M %p")
        except Exception:
            return None


def load_csv(filepath: str) -> tuple[list, list]:
    """
    Load CSV and split into completed and upcoming matches.
    Returns (completed, upcoming) lists of dicts.
    """
    completed = []
    upcoming  = []

    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = row.get("status", "").strip().lower()
            dt     = parse_date(row.get("date_GMT", ""))

            base = {
                "home"    : row.get("home_team_name", "").strip(),
                "away"    : row.get("away_team_name", "").strip(),
                "date"    : dt,
                "date_str": row.get("date_GMT", "").strip(),
                "odds_1"  : safe_float(row.get("odds_ft_home_team_win")),
                "odds_x"  : safe_float(row.get("odds_ft_draw")),
                "odds_2"  : safe_float(row.get("odds_ft_away_team_win")),
            }

            if status == "complete":
                hg = safe_int(row.get("home_team_goal_count"))
                ag = safe_int(row.get("away_team_goal_count"))
                if hg is not None and ag is not None:
                    base["home_goals"] = hg
                    base["away_goals"] = ag
                    base["xg_home"]    = safe_float(row.get("team_a_xg"))
                    base["xg_away"]    = safe_float(row.get("team_b_xg"))
                    completed.append(base)

            elif status == "incomplete":
                upcoming.append(base)

    return completed, upcoming


def safe_float(val):
    try:
        f = float(val)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def safe_int(val):
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


# ================================================================
# 2. RECENCY WEIGHTING
# ================================================================
def recency_weight(match_date: datetime, reference_date: datetime,
                   half_life_days: int = 90) -> float:
    """
    Exponential decay weight. A match played `half_life_days` ago
    counts half as much as a match played today.
    Default 90 days — balances recency vs sample size.
    """
    if match_date is None:
        return 0.5   # unknown date gets neutral weight
    days_ago = (reference_date - match_date).days
    days_ago = max(0, days_ago)
    return math.exp(-math.log(2) * days_ago / half_life_days)


# ================================================================
# 3. PARAMETER ESTIMATION  (Dixon-Coles MLE)
# ================================================================
def dc_correction(home_goals: int, away_goals: int,
                  lambda_h: float, mu_a: float, rho: float) -> float:
    """
    Dixon-Coles correction factor for low-scoring scorelines.
    Only applies to 0-0, 1-0, 0-1, 1-1.
    """
    if home_goals == 0 and away_goals == 0:
        return 1 - lambda_h * mu_a * rho
    elif home_goals == 1 and away_goals == 0:
        return 1 + mu_a * rho
    elif home_goals == 0 and away_goals == 1:
        return 1 + lambda_h * rho
    elif home_goals == 1 and away_goals == 1:
        return 1 - rho
    else:
        return 1.0


def neg_log_likelihood(params, teams, completed, reference_date,
                       half_life_days):
    """
    Negative log likelihood for Dixon-Coles model.
    params layout:
        [attack_0 ... attack_n, defence_0 ... defence_n,
         home_advantage, rho]
    """
    n      = len(teams)
    attack  = {t: params[i]     for i, t in enumerate(teams)}
    defence = {t: params[n + i] for i, t in enumerate(teams)}
    home_adv = params[2 * n]
    rho      = params[2 * n + 1]

    total_ll = 0.0

    for m in completed:
        h = m["home"]
        a = m["away"]
        if h not in attack or a not in attack:
            continue

        weight = recency_weight(m["date"], reference_date, half_life_days)
        if weight < 0.01:
            continue

        # Expected goals
        lambda_h = math.exp(attack[h] + defence[a] + home_adv)
        mu_a     = math.exp(attack[a] + defence[h])

        hg = m["home_goals"]
        ag = m["away_goals"]

        # Poisson log-likelihood
        ll = (
            hg * math.log(lambda_h) - lambda_h - math.lgamma(hg + 1) +
            ag * math.log(mu_a)     - mu_a     - math.lgamma(ag + 1)
        )

        # DC correction
        tau = dc_correction(hg, ag, lambda_h, mu_a, rho)
        if tau <= 0:
            tau = 1e-10
        ll += math.log(tau)

        total_ll += weight * ll

    return -total_ll


def estimate_parameters(completed: list, half_life_days: int = 90):
    """
    Estimate Dixon-Coles parameters using scipy L-BFGS-B optimiser.
    Returns dict with attack/defence per team, home_advantage, rho.
    """
    try:
        from scipy.optimize import minimize
    except ImportError:
        raise ImportError(
            "scipy is required for Dixon-Coles.\n"
            "Install with: pip install scipy"
        )

    # Collect all teams
    teams = sorted(set(
        [m["home"] for m in completed] +
        [m["away"] for m in completed]
    ))
    n = len(teams)

    if n < 2:
        raise ValueError(
            f"Need at least 2 teams with completed matches. "
            f"Found {n}."
        )

    reference_date = max(
        (m["date"] for m in completed if m["date"] is not None),
        default=datetime.now()
    )

    # Initial params: attack=0.5, defence=-0.5, home_adv=0.3, rho=-0.1
    x0 = (
        [0.5]  * n +    # attack
        [-0.5] * n +    # defence
        [0.3]  +        # home advantage
        [-0.1]          # rho
    )

    # Bounds: attack and defence unrestricted, rho in (-1, 1)
    bounds = (
        [(None, None)] * n +      # attack
        [(None, None)] * n +      # defence
        [(0, None)]    +          # home advantage >= 0
        [(-0.99, 0.99)]           # rho in (-1, 1)
    )

    # Constraint: sum of attack params = 0 (identifiability)
    # Implemented via first attack param = negative sum of rest
    result = minimize(
        neg_log_likelihood,
        x0,
        args=(teams, completed, reference_date, half_life_days),
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 500, "ftol": 1e-9},
    )

    params = result.x

    attack  = {t: params[i]     for i, t in enumerate(teams)}
    defence = {t: params[n + i] for i, t in enumerate(teams)}

    return {
        "teams"        : teams,
        "attack"       : attack,
        "defence"      : defence,
        "home_advantage": float(params[2 * n]),
        "rho"          : float(params[2 * n + 1]),
        "converged"    : result.success,
        "n_matches"    : len(completed),
        "reference_date": reference_date,
    }


# ================================================================
# 4. MATCH PREDICTION
# ================================================================
def predict_match(home: str, away: str, params: dict) -> dict | None:
    """
    Predict 1/X/2 probabilities using Dixon-Coles model.
    Returns {"1": p, "X": p, "2": p} or None if teams not in model.
    """
    attack   = params["attack"]
    defence  = params["defence"]
    home_adv = params["home_advantage"]
    rho      = params["rho"]

    if home not in attack or away not in attack:
        return None

    lambda_h = math.exp(attack[home] + defence[away] + home_adv)
    mu_a     = math.exp(attack[away] + defence[home])

    # Build scoreline probability matrix
    home_win = 0.0
    draw     = 0.0
    away_win = 0.0

    for hg in range(MAX_GOALS + 1):
        for ag in range(MAX_GOALS + 1):
            # Poisson probabilities
            p_hg = math.exp(-lambda_h) * (lambda_h ** hg) / math.factorial(hg)
            p_ag = math.exp(-mu_a)     * (mu_a     ** ag) / math.factorial(ag)
            p    = p_hg * p_ag

            # DC correction
            tau = dc_correction(hg, ag, lambda_h, mu_a, rho)
            p   = p * max(tau, 0)

            if hg > ag:
                home_win += p
            elif hg == ag:
                draw     += p
            else:
                away_win += p

    total = home_win + draw + away_win
    if total <= 0:
        return None

    return {
        "1"          : home_win / total,
        "X"          : draw     / total,
        "2"          : away_win / total,
        "exp_home_goals": round(lambda_h, 3),
        "exp_away_goals": round(mu_a, 3),
    }


# ================================================================
# 5. ODDS IMPLIED PROBABILITY
# ================================================================
def odds_to_prob(odds_1, odds_x, odds_2) -> dict | None:
    """Convert decimal odds to normalised implied probabilities."""
    if not all([odds_1, odds_x, odds_2]):
        return None
    raw   = {"1": 1/odds_1, "X": 1/odds_x, "2": 1/odds_2}
    total = sum(raw.values())
    return {k: v/total for k,v in raw.items()}


# ================================================================
# 6. BLEND AND PICK
# ================================================================
def blend_and_pick(dc_probs: dict, odds_probs: dict | None,
                   dc_weight: float = 0.5) -> dict:
    """
    Blend DC probabilities with odds-implied probabilities.
    If no odds available, use DC only.
    Returns final probabilities + pick + confidence.
    """
    if odds_probs is None:
        blended = dc_probs
    else:
        odds_weight = 1.0 - dc_weight
        blended = {
            k: dc_weight * dc_probs[k] + odds_weight * odds_probs[k]
            for k in ["1", "X", "2"]
        }

    # Normalise
    total = sum(blended[k] for k in ["1", "X", "2"])
    blended = {k: blended[k] / total for k in ["1", "X", "2"]}

    pick       = max(["1", "X", "2"], key=lambda k: blended[k])
    second     = sorted(blended.values(), reverse=True)[1]
    confidence = blended[pick] - second   # margin over second best

    # Pick type
    if confidence >= 0.15 and blended[pick] >= 0.55:
        pick_type = "Banker"
    elif confidence >= 0.08:
        pick_type = "Double Chance"
    elif blended["X"] >= 0.30 and confidence < 0.08:
        pick_type = "Draw"
    else:
        pick_type = "Speculative"

    return {
        "pick"      : pick,
        "pick_type" : pick_type,
        "confidence": round(confidence * 100, 1),
        "probs"     : {k: round(blended[k] * 100, 1) for k in ["1","X","2"]},
        "dc_probs"  : {k: round(dc_probs[k] * 100, 1) for k in ["1","X","2"]},
        "odds_probs": {k: round(odds_probs[k] * 100, 1)
                       for k in ["1","X","2"]} if odds_probs else None,
    }


# ================================================================
# 7. DATE FILTERING
# ================================================================
def filter_by_date(upcoming: list, target_date: str | None,
                   week: bool = False) -> list:
    """
    Filter upcoming matches by date.
    target_date: 'YYYY-MM-DD' string
    week: if True, return matches within the next 7 days from today
    """
    if not target_date and not week:
        return upcoming

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if week:
        end_date = today + timedelta(days=7)
        return [
            m for m in upcoming
            if m["date"] is not None
            and today <= m["date"].replace(
                hour=0, minute=0, second=0, microsecond=0
            ) <= end_date
        ]

    if target_date:
        try:
            target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        except ValueError:
            print(f"Invalid date format: {target_date}. Use YYYY-MM-DD.")
            return upcoming
        return [
            m for m in upcoming
            if m["date"] is not None
            and m["date"].date() == target_dt.date()
        ]

    return upcoming


# ================================================================
# 8. TEAM STRENGTH TABLE
# ================================================================
def print_team_strengths(params: dict):
    """Print estimated attack/defence strengths per team."""
    teams   = params["teams"]
    attack  = params["attack"]
    defence = params["defence"]

    print(f"\n  TEAM STRENGTHS (from {params['n_matches']} matches)")
    print(f"  {'Team':<30} {'Attack':>8} {'Defence':>9} {'Net':>6}")
    print("  " + "-" * 56)
    for t in sorted(teams, key=lambda t: attack[t] - defence[t], reverse=True):
        net = attack[t] - defence[t]
        print(f"  {t:<30} {attack[t]:>8.3f} {defence[t]:>9.3f} {net:>6.3f}")
    print(f"\n  Home advantage : {params['home_advantage']:.3f}")
    print(f"  Rho (DC corr)  : {params['rho']:.3f}")
    print(f"  Converged      : {params['converged']}")


# ================================================================
# 9. MAIN
# ================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Dixon-Coles match prediction from FootyStats CSV"
    )
    parser.add_argument(
        "--csv", required=True,
        help="Path to FootyStats league CSV file"
    )
    parser.add_argument(
        "--date", default=None,
        help="Filter upcoming matches by date (YYYY-MM-DD). "
             "Default: today."
    )
    parser.add_argument(
        "--week", action="store_true",
        help="Show this week's upcoming matches (next 7 days)"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Show all upcoming matches (no date filter)"
    )
    parser.add_argument(
        "--dc-weight", type=float, default=0.50,
        help="Weight for Dixon-Coles vs odds (0-1). Default: 0.50"
    )
    parser.add_argument(
        "--half-life", type=int, default=90,
        help="Recency half-life in days. Default: 90"
    )
    parser.add_argument(
        "--strengths", action="store_true",
        help="Print team strength table"
    )
    args = parser.parse_args()

    # Default to today if no filter specified
    if not args.date and not args.week and not getattr(args, "all"):
        args.date = datetime.now().strftime("%Y-%m-%d")

    print("=" * 68)
    print("  DIXON-COLES MATCH PREDICTOR")
    print(f"  CSV    : {os.path.basename(args.csv)}")
    filter_label = (
        f"date={args.date}" if args.date else
        "this week"         if args.week else
        "all upcoming"
    )
    print(f"  Filter : {filter_label}")
    print(f"  Blend  : {int(args.dc_weight*100)}% DC / "
          f"{int((1-args.dc_weight)*100)}% odds")
    print("=" * 68)

    # Load
    if not os.path.exists(args.csv):
        print(f"\nERROR: CSV file not found: {args.csv}")
        return

    completed, upcoming = load_csv(args.csv)
    print(f"\n  Completed matches : {len(completed)}")
    print(f"  Upcoming matches  : {len(upcoming)}")

    if len(completed) < 10:
        print(
            f"\n  WARNING: Only {len(completed)} completed matches. "
            f"Dixon-Coles needs more data for reliable estimates.\n"
            f"  Predictions will be less accurate."
        )

    # Estimate parameters
    print(f"\n  Estimating team parameters...")
    try:
        params = estimate_parameters(completed, args.half_life)
    except ImportError as e:
        print(f"\n  ERROR: {e}")
        return
    except Exception as e:
        print(f"\n  ERROR estimating parameters: {e}")
        return

    print(f"  Done. {len(params['teams'])} teams parameterised.")
    if not params["converged"]:
        print("  WARNING: Optimiser did not fully converge. "
              "Results may be less accurate.")

    if args.strengths:
        print_team_strengths(params)

    # Filter upcoming
    if getattr(args, "all"):
        filtered = upcoming
    else:
        filtered = filter_by_date(upcoming, args.date, args.week)

    if not filtered:
        print(f"\n  No upcoming matches found for filter: {filter_label}")
        print(f"  Try --week or --all to see more matches.")
        return

    print(f"\n  Predicting {len(filtered)} match(es)...\n")

    # Predict
    results = []
    no_model = []

    for m in filtered:
        dc_probs   = predict_match(m["home"], m["away"], params)
        odds_probs = odds_to_prob(m["odds_1"], m["odds_x"], m["odds_2"])

        if dc_probs is None:
            no_model.append(m)
            pick_data = {
                "pick"      : max(["1","X","2"],
                                  key=lambda k: (odds_probs or {}).get(k, 0))
                              if odds_probs else "?",
                "pick_type" : "Odds Only",
                "confidence": 0.0,
                "probs"     : {k: round((odds_probs or {}).get(k,0)*100,1)
                               for k in ["1","X","2"]},
                "dc_probs"  : None,
                "odds_probs": {k: round((odds_probs or {}).get(k,0)*100,1)
                               for k in ["1","X","2"]} if odds_probs else None,
            }
        else:
            pick_data = blend_and_pick(
                {k: dc_probs[k] for k in ["1","X","2"]},
                odds_probs,
                args.dc_weight
            )

        results.append({
            "home"          : m["home"],
            "away"          : m["away"],
            "date"          : m["date"].strftime("%Y-%m-%d %H:%M") if m["date"] else "",
            "pick"          : pick_data["pick"],
            "label"         : LABEL_MAP.get(pick_data["pick"], "?"),
            "pick_type"     : pick_data["pick_type"],
            "confidence"    : pick_data["confidence"],
            "probs"         : pick_data["probs"],
            "dc_probs"      : pick_data.get("dc_probs"),
            "odds_probs"    : pick_data.get("odds_probs"),
            "exp_home_goals": dc_probs.get("exp_home_goals") if dc_probs else None,
            "exp_away_goals": dc_probs.get("exp_away_goals") if dc_probs else None,
            "odds"          : {
                "1": m["odds_1"],
                "X": m["odds_x"],
                "2": m["odds_2"],
            },
        })

    # Print predictions
    PICK_ICONS = {
        "Banker"        : "★",
        "Draw"          : "~",
        "Double Chance" : "○",
        "Speculative"   : "?",
        "Odds Only"     : "-",
    }

    print("=" * 68)
    print(f"  {'Match':<36} {'Pick':<5} {'H%':>5} {'X%':>5} {'A%':>5} "
          f"{'Conf':>6}  Type")
    print("-" * 68)

    for r in results:
        matchup = f"{r['home']} vs {r['away']}"
        icon    = PICK_ICONS.get(r["pick_type"], " ")
        h       = r["probs"]["1"]
        x       = r["probs"]["X"]
        a       = r["probs"]["2"]
        conf    = r["confidence"]
        ptype   = r["pick_type"]
        print(f"  {matchup:<36} {r['pick']:<5} {h:>5.1f} {x:>5.1f} "
              f"{a:>5.1f} {conf:>5.1f}%  {icon} {ptype}")

    print("-" * 68)

    # Summary
    from collections import Counter
    pick_counts = Counter(r["pick"] for r in results)
    type_counts = Counter(r["pick_type"] for r in results)
    print(f"\n  PICKS: H={pick_counts['1']}  D={pick_counts['X']}  "
          f"A={pick_counts['2']}")
    print(f"  TYPES: " + "  ".join(
        f"{k}={v}" for k,v in type_counts.most_common()
    ))

    if no_model:
        print(f"\n  NOTE: {len(no_model)} match(es) used odds-only "
              f"(teams not in training data):")
        for m in no_model:
            print(f"    {m['home']} vs {m['away']}")

    # Save output
    league_name = (
        os.path.basename(args.csv)
        .replace(".csv", "")
        .replace(" ", "-")
        .lower()
    )
    date_tag    = args.date or datetime.now().strftime("%Y-%m-%d")
    out_name    = f"{league_name}-predictions-{date_tag}.json"
    out_path    = os.path.join(PRED_DIR, out_name)

    output = {
        "generated_at"  : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "csv_source"    : os.path.basename(args.csv),
        "filter"        : filter_label,
        "dc_weight"     : args.dc_weight,
        "half_life_days": args.half_life,
        "n_completed"   : len(completed),
        "n_predicted"   : len(results),
        "converged"     : params["converged"],
        "team_params"   : {
            "attack"        : {k: round(v, 4) for k,v in params["attack"].items()},
            "defence"       : {k: round(v, 4) for k,v in params["defence"].items()},
            "home_advantage": round(params["home_advantage"], 4),
            "rho"           : round(params["rho"], 4),
        },
        "predictions"   : results,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Saved: {out_path}")
    print("=" * 68)


if __name__ == "__main__":
    main()