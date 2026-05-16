#!/usr/bin/env python3
"""
combine_predictions.py

Combines all Dixon-Coles league prediction JSON files from the predictions
directory into a single CSV file.

Usage:
    python combine_predictions.py [--predictions-dir PATH] [--output FILE]

Defaults:
    --predictions-dir  ./dixon_coles/predictions
    --output           ./combined_predictions.csv
"""

import argparse
import csv
import json
import re
from pathlib import Path


COLUMNS = [
    "league",
    "date",
    "home",
    "away",
    "pick",
    "label",
    "pick_type",
    "confidence",
    "prob_home",
    "prob_draw",
    "prob_away",
    "exp_home_goals",
    "exp_away_goals",
    "odds_home",
    "odds_draw",
    "odds_away",
    "generated_at",
    "filter_date",
    "n_completed",
    "n_predicted",
    "converged",
    "dc_weight",
    "half_life_days",
]


def league_name_from_path(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"-predictions-\d{4}-\d{2}-\d{2}$", "", stem)
    stem = re.sub(r"-matches-\d{4}-to-\d{4}-stats$", "", stem)
    stem = re.sub(r"-matches-\d{4}-to-\d{4}$", "", stem)
    return stem.replace("-", " ").title()


def find_prediction_files(predictions_dir: Path) -> list[Path]:
    return sorted(predictions_dir.glob("*.json"))


def combine(predictions_dir: Path, output_path: Path):
    files = find_prediction_files(predictions_dir)
    if not files:
        print(f"No JSON files found in: {predictions_dir}")
        return

    total_rows = 0

    with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=COLUMNS)
        writer.writeheader()

        for json_path in files:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)

            league      = league_name_from_path(json_path)
            predictions = data.get("predictions", [])
            filter_date = data.get("filter", "").replace("date=", "")

            for pred in predictions:
                probs = pred.get("probs", {})
                odds  = pred.get("odds",  {})
                writer.writerow({
                    "league":         league,
                    "date":           pred.get("date", ""),
                    "home":           pred.get("home", ""),
                    "away":           pred.get("away", ""),
                    "pick":           pred.get("pick", ""),
                    "label":          pred.get("label", ""),
                    "pick_type":      pred.get("pick_type", ""),
                    "confidence":     pred.get("confidence", ""),
                    "prob_home":      probs.get("1", ""),
                    "prob_draw":      probs.get("X", ""),
                    "prob_away":      probs.get("2", ""),
                    "exp_home_goals": pred.get("exp_home_goals", ""),
                    "exp_away_goals": pred.get("exp_away_goals", ""),
                    "odds_home":      odds.get("1", ""),
                    "odds_draw":      odds.get("X", ""),
                    "odds_away":      odds.get("2", ""),
                    "generated_at":   data.get("generated_at", ""),
                    "filter_date":    filter_date,
                    "n_completed":    data.get("n_completed", ""),
                    "n_predicted":    data.get("n_predicted", ""),
                    "converged":      data.get("converged", ""),
                    "dc_weight":      data.get("dc_weight", ""),
                    "half_life_days": data.get("half_life_days", ""),
                })
                total_rows += 1

            print(f"  ✓ {league}  ({len(predictions)} predictions)")

    print(f"\nSaved → {output_path}  ({len(files)} leagues, {total_rows} rows)")


def main():
    parser = argparse.ArgumentParser(description="Combine Dixon-Coles prediction JSONs into a single CSV.")
    parser.add_argument(
        "--predictions-dir", "-d",
        type=Path,
        default=Path("./dixon_coles/predictions"),
        help="Directory containing prediction JSON files (default: ./dixon_coles/predictions)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("./combined_predictions.csv"),
        help="Output CSV file path (default: ./combined_predictions.csv)",
    )
    args = parser.parse_args()

    if not args.predictions_dir.is_dir():
        print(f"Error: predictions directory not found: {args.predictions_dir}")
        raise SystemExit(1)

    print(f"Reading from : {args.predictions_dir}")
    print(f"Writing to   : {args.output}\n")
    combine(args.predictions_dir, args.output)


if __name__ == "__main__":
    main()