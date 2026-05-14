"""
db.py  —  Supabase database layer for Chronos app
==================================================
All reads and writes to Supabase go through this module.
Import this in app.py only.
"""

import os
import json
import streamlit as st
from supabase import create_client, Client
from datetime import datetime


# ================================================================
# CLIENT
# ================================================================
@st.cache_resource
def get_client() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


# ================================================================
# FORECASTS
# ================================================================
def save_forecast(jackpot: str, forecast_data: dict) -> str | None:
    """
    Save a forecast JSON to Supabase forecasts table.
    Returns the inserted row id or None on failure.
    """
    client = get_client()
    try:
        row = {
            "jackpot"      : jackpot,
            "generated_at" : forecast_data.get("generated_at", ""),
            "card_file"    : forecast_data.get("card_file", ""),
            "card_signals" : forecast_data.get("card_signals", {}),
            "forecast"     : forecast_data.get("forecast", {}),
            "tickets"      : forecast_data.get("tickets", {}),
            "num_games"    : forecast_data.get("num_games",
                             16 if jackpot == "mozzart" else
                             13 if jackpot == "midweek" else 17),
            "match_analysis": forecast_data.get("match_analysis", []),
            "base_rates"   : forecast_data.get("base_rates", {}),
        }
        result = client.table("forecasts").insert(row).execute()
        return result.data[0]["id"] if result.data else None
    except Exception as e:
        st.error(f"Failed to save forecast: {e}")
        return None


def get_latest_forecast(jackpot: str) -> dict | None:
    """Get most recent forecast for a jackpot."""
    client = get_client()
    try:
        result = (
            client.table("forecasts")
            .select("*")
            .eq("jackpot", jackpot)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        st.error(f"Failed to load forecast: {e}")
        return None


def get_forecast_by_id(forecast_id: str) -> dict | None:
    client = get_client()
    try:
        result = (
            client.table("forecasts")
            .select("*")
            .eq("id", forecast_id)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        st.error(f"Failed to load forecast: {e}")
        return None


def list_forecasts(jackpot: str, limit: int = 20) -> list:
    """List recent forecasts for a jackpot."""
    client = get_client()
    try:
        result = (
            client.table("forecasts")
            .select("id, jackpot, generated_at, card_file, num_games")
            .eq("jackpot", jackpot)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        st.error(f"Failed to list forecasts: {e}")
        return []


def get_unscored_forecasts(jackpot: str, limit: int = 30) -> list:
    """
    Return forecasts that have no actuals row yet, newest first.
    Each entry includes full tickets + match_analysis for in-process scoring.
    Queries actuals separately to avoid join window misses.
    """
    client = get_client()
    try:
        # Step 1: collect ALL forecast_ids that already have actuals
        actuals_result = (
            client.table("actuals")
            .select("forecast_id")
            .eq("jackpot", jackpot)
            .execute()
        )
        scored_ids = {r["forecast_id"] for r in (actuals_result.data or [])}

        # Step 2: fetch recent forecasts (no join needed)
        result = (
            client.table("forecasts")
            .select("id, generated_at, card_file, num_games, tickets, match_analysis, forecast, card_signals")
            .eq("jackpot", jackpot)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        unscored = []
        for row in (result.data or []):
            if row["id"] in scored_ids:
                continue
            gen      = row.get("generated_at", "")
            date_str = gen[:10] if gen else ""
            card     = row.get("card_file", "")
            parts    = card.replace(".json", "").split("_")
            dp       = [p for p in parts if len(p) == 10 and p.count("-") == 2]
            tp       = [p for p in parts if len(p) == 6 and p.isdigit()]
            if dp:
                lbl = dp[0]
                if tp:
                    t = tp[0]
                    lbl += f" {t[:2]}:{t[2:4]}"
                display = f"{lbl} \u2014 {card}"
            else:
                display = gen[:16].replace("T", " ") + f" \u2014 {card}"
            unscored.append({
                "id"            : row["id"],
                "card_file"     : card,
                "generated_at"  : gen,
                "date_str"      : date_str,
                "display"       : display,
                "num_games"     : row.get("num_games", 16),
                "tickets"       : row.get("tickets") or {},
                "match_analysis": row.get("match_analysis") or [],
                "forecast"      : row.get("forecast") or {},
                "card_signals"  : row.get("card_signals") or {},
            })
        return unscored
    except Exception as e:
        st.error(f"Failed to load unscored forecasts: {e}")
        return []


# ================================================================
# ACTUALS
# ================================================================
def save_actuals(jackpot: str, forecast_id: str, actuals_data: dict) -> bool:
    """Save logged actuals to Supabase actuals table."""
    client = get_client()
    try:
        dist_err = actuals_data.get("distribution_error", {})
        row = {
            "forecast_id"       : forecast_id,
            "jackpot"           : jackpot,
            "logged_at"         : actuals_data.get("logged_at", ""),
            "results"           : actuals_data.get("results", []),
            "scores"            : actuals_data.get("scores", []),
            "ticket_scores"     : actuals_data.get("ticket_scores", {}),
            "distribution_error": dist_err,
            "signal_accuracy"   : actuals_data.get("signal_accuracy", {}),
            "per_match_accuracy": actuals_data.get("per_match_accuracy", {}),
            "best_score"        : actuals_data.get("best_score", 0),
            "best_ticket"       : actuals_data.get("best_ticket", ""),
        }
        client.table("actuals").insert(row).execute()
        return True
    except Exception as e:
        st.error(f"Failed to save actuals: {e}")
        return False


def get_actuals_for_forecast(forecast_id: str) -> dict | None:
    client = get_client()
    try:
        result = (
            client.table("actuals")
            .select("*")
            .eq("forecast_id", forecast_id)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        return None


# ================================================================
# PERFORMANCE
# ================================================================
def get_performance(jackpot: str, limit: int = 30) -> list:
    """
    Join forecasts + actuals for performance table.
    Returns list of dicts ready for st.dataframe.
    """
    client = get_client()
    try:
        # Get forecasts with their actuals via join
        result = (
            client.table("forecasts")
            .select("id, generated_at, card_file, num_games, actuals(*)")
            .eq("jackpot", jackpot)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        rows = []
        for f in (result.data or []):
            actuals_list = f.get("actuals", [])
            actual       = actuals_list[0] if actuals_list else None

            dist_err = actual.get("distribution_error", {}) if actual else {}

            rows.append({
                "Date"          : f["generated_at"][:10] if f.get("generated_at") else "-",
                "Card"          : f.get("card_file", "-"),
                "Games"         : f.get("num_games", "-"),
                "Best Score"    : actual["best_score"] if actual else "-",
                "Best Ticket"   : actual["best_ticket"] if actual else "-",
                "Draw Predicted": dist_err.get("predicted_draws", "-"),
                "Draw Actual"   : dist_err.get("actual_draws", "-"),
                "Draw Error"    : dist_err.get("draw_error", "-"),
                "Logged"        : "✓" if actual else "✗",
                "forecast_id"   : f["id"],
            })
        return rows
    except Exception as e:
        st.error(f"Failed to load performance: {e}")
        return []
