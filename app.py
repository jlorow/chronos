"""
app.py  —  Chronos Jackpot Forecast Dashboard
==============================================
Streamlit app. Run from C:/Users/User/Desktop/Chronos/:
    streamlit run app.py

Sections: Mozzart Daily | Mid-Week | Mega Jackpot
Tabs per section: Forecast | Run Forecast | Log Results | Performance
"""

import streamlit as st
import subprocess
import json
import os
import glob
import sys
from datetime import datetime
from db import (
    save_forecast, get_latest_forecast, list_forecasts,
    save_actuals, get_actuals_for_forecast, get_performance,
)

# ================================================================
# PAGE CONFIG
# ================================================================
st.set_page_config(
    page_title = "Chronos — Jackpot Forecasts",
    page_icon  = "🎯",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)

# ================================================================
# CONSTANTS
# ================================================================
ROOT = os.path.dirname(os.path.abspath(__file__))

JACKPOTS = {
    "mozzart": {
        "label"         : "Mozzart Daily",
        "num_games"     : 16,
        "icon"          : "🟢",
        "forecast_script": "mozzart/mozzart_forecast.py",
        "fetch_script"  : "mozzart/mozzart_jackpot_complete.py",
        "output_dir"    : "mozzart/output",
        "forecast_pattern": "mozzart_forecast_*.json",
        "color"         : "#16A34A",
    },
    "midweek": {
        "label"         : "Mid-Week",
        "num_games"     : 13,
        "icon"          : "🔵",
        "forecast_script": "midweek/midweek_forecast.py",
        "fetch_script"  : "midweek/fetch_sportpesa_midweek_jackpot.py",
        "output_dir"    : "midweek/output",
        "forecast_pattern": "midweek_forecast_*.json",
        "color"         : "#2563EB",
    },
    "sportpesa": {
        "label"         : "Mega Jackpot",
        "num_games"     : 17,
        "icon"          : "🔴",
        "forecast_script": "sportpesa/sportpesa_forecast.py",
        "fetch_script"  : "sportpesa/fetch_sportpesa_mega_jackpot.py",
        "output_dir"    : "sportpesa/output",
        "forecast_pattern": "sportpesa_forecast_*.json",
        "color"         : "#DC2626",
    },
}

PICK_COLORS = {
    "Banker"        : "🟢",
    "Draw"          : "🟡",
    "Double Chance" : "🟠",
    "Speculative"   : "🔴",
    "European"      : "🔵",
    "International" : "⚪",
}

OUTCOME_LABELS = {"1": "Home", "X": "Draw", "2": "Away"}


# ================================================================
# HELPERS
# ================================================================
def run_script(script_path: str, extra_args: list = None) -> tuple[bool, str]:
    """Run a Python script as subprocess. Returns (success, output)."""
    cmd  = [sys.executable, os.path.join(ROOT, script_path)]
    if extra_args:
        cmd.extend(extra_args)
    try:
        result = subprocess.run(
            cmd,
            capture_output = True,
            text           = True,
            encoding       = 'utf-8',
            errors         = 'replace',
            cwd            = ROOT,
            timeout        = 300,
            env            = {**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Script timed out after 5 minutes."
    except Exception as e:
        return False, str(e)


def find_latest_local_forecast(jackpot: str) -> dict | None:
    """Read most recent forecast JSON from local output folder."""
    cfg     = JACKPOTS[jackpot]
    pattern = os.path.join(ROOT, cfg["output_dir"], cfg["forecast_pattern"])
    files   = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        return None
    try:
        with open(files[0], encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_results_json() -> dict | None:
    """Read root-level results.json."""
    path = os.path.join(ROOT, "results.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def score_label(score: int, num_games: int) -> str:
    """Colour-coded score label."""
    pct = score / num_games
    if pct >= 0.75:
        return f"🟢 {score}/{num_games}"
    elif pct >= 0.50:
        return f"🟡 {score}/{num_games}"
    else:
        return f"🔴 {score}/{num_games}"


# ================================================================
# TICKET DISPLAY
# ================================================================
def render_ticket(ticket_data: dict, analysis: list, scenario: str, num_games: int):
    """Render a single ticket as a clean table."""
    ticket  = ticket_data.get("ticket", [])
    matches = ticket_data.get("matches", [])
    counts  = ticket_data.get("target_counts", {})
    regime  = ticket_data.get("regime", "")

    h = counts.get("1", 0)
    d = counts.get("X", 0)
    a = counts.get("2", 0)

    st.caption(
        f"H={h} · D={d} · A={a} · {regime}"
    )

    # Build rows
    rows = []
    for i in range(min(num_games, len(ticket))):
        pred = ticket[i]
        m    = matches[i] if i < len(matches) else {}
        ana  = analysis[i] if i < len(analysis) else {}

        home    = m.get("home", m.get("home_team", f"Home {i+1}"))
        away    = m.get("away", m.get("away_team", f"Away {i+1}"))
        league  = m.get("league", "")
        pt      = ana.get("pick_type", "")
        icon    = PICK_COLORS.get(pt, "⚪")

        rows.append({
            "#"      : i + 1,
            "Match"  : f"{home} vs {away}",
            "Pred"   : pred,
            "Result" : OUTCOME_LABELS.get(pred, pred),
            "Type"   : f"{icon} {pt}",
            "League" : league,
        })

    if rows:
        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            hide_index   = True,
            use_container_width = True,
            column_config = {
                "#"      : st.column_config.NumberColumn(width="small"),
                "Pred"   : st.column_config.TextColumn(width="small"),
                "Result" : st.column_config.TextColumn(width="small"),
                "Type"   : st.column_config.TextColumn(width="medium"),
            }
        )


# ================================================================
# SIGNALS PANEL
# ================================================================
def render_signals(card_signals: dict, forecast: dict):
    """Render card signals and Chronos forecast summary."""
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Mirror Count", card_signals.get("mirror_count", "-"),
                  delta="DRAW-HEAVY" if card_signals.get("mirror_ge3_flag") else None,
                  delta_color="normal")
        st.metric("Clear Fav Count", card_signals.get("clear_fav_count", "-"),
                  delta="DECISIVE" if card_signals.get("clear_fav_ge3_flag") else None,
                  delta_color="inverse")

    with col2:
        st.metric("Away Fav Count", card_signals.get("away_fav_count", "-"),
                  delta="AWAY-HEAVY" if card_signals.get("away_fav_ge5_flag") else None,
                  delta_color="normal")
        st.metric("Draw Pct Weighted",
                  f"{round(card_signals.get('draw_pct_weighted', 0) * 100, 1)}%")

    with col3:
        st.metric("Draws t-1", card_signals.get("draws_t1", "-"))
        st.metric("Draw Heavy Streak", card_signals.get("draw_heavy_streak", "-"))

    # Chronos distribution forecast
    st.markdown("**Chronos Distribution Forecast**")
    if forecast:
        fc_col1, fc_col2, fc_col3 = st.columns(3)
        for col, target, label in [
            (fc_col1, "total_draws", "Draws"),
            (fc_col2, "total_homes", "Homes"),
            (fc_col3, "total_aways", "Aways"),
        ]:
            fc = forecast.get(target, {})
            with col:
                st.markdown(f"**{label}**")
                st.markdown(
                    f"P10: `{fc.get('P10', '-')}` · "
                    f"P50: `{fc.get('P50', '-')}` · "
                    f"P90: `{fc.get('P90', '-')}`"
                )


# ================================================================
# TAB 1 — FORECAST
# ================================================================
def tab_forecast(jackpot: str):
    cfg = JACKPOTS[jackpot]
    st.subheader(f"{cfg['icon']} Latest Forecast — {cfg['label']}")

    # Load from Supabase
    with st.spinner("Loading forecast..."):
        data = get_latest_forecast(jackpot)

    if not data:
        st.info(
            "No forecast found in database. "
            "Run a forecast first using the **Run Forecast** tab."
        )
        return

    generated = data.get("generated_at", "")[:16].replace("_", " ")
    card_file = data.get("card_file", "")
    st.caption(f"Generated: {generated}  ·  Card: {card_file}")

    tickets  = data.get("tickets", {})
    analysis = data.get("match_analysis", [])
    forecast = data.get("forecast", {})
    signals  = data.get("card_signals", {})
    n        = data.get("num_games", cfg["num_games"])

    # Check if actuals already logged
    actuals = get_actuals_for_forecast(data["id"])
    if actuals:
        best  = actuals.get("best_score", 0)
        label = score_label(best, n)
        st.success(f"Results logged — Best score: {label}")

    # Signals
    with st.expander("Card Signals & Distribution Forecast", expanded=False):
        render_signals(signals, forecast)

    # Three tickets side by side
    st.markdown("---")
    st.markdown("**Prediction Tickets**")
    t_col1, t_col2, t_col3 = st.columns(3)

    for col, scenario, label in [
        (t_col1, "conservative", "Conservative (P10)"),
        (t_col2, "base",         "Base (P50)"),
        (t_col3, "draw_heavy",   "Draw-Heavy (P90)"),
    ]:
        with col:
            st.markdown(f"**{label}**")
            if scenario in tickets:
                render_ticket(tickets[scenario], analysis, scenario, n)
            else:
                st.caption("Not available")

    # Pro tickets — only for SportPesa Mega (17 games)
    pro_tickets = data.get("pro_tickets", {})
    if pro_tickets:
        st.markdown("---")
        st.markdown("**Pro Variant Tickets**")
        st.caption(
            "Top-confidence picks from the Base ticket. "
            "Each Pro ticket is a subset of the Base ticket ordered by confidence."
        )

        pro_keys = sorted(
            pro_tickets.keys(),
            key=lambda k: int(k.split("_")[1]),
            reverse=True,
        )

        pro_tabs = st.tabs([
            f"Pro-{k.split('_')[1]} ({len(pro_tickets[k])} games)"
            for k in pro_keys
        ])

        for tab, key in zip(pro_tabs, pro_keys):
            with tab:
                matches_pro = pro_tickets[key]
                rows = []
                for p in matches_pro:
                    pred    = p.get("pred", "")
                    home    = p.get("home", "")
                    away    = p.get("away", "")
                    conf    = p.get("confidence", 0)
                    label_p = OUTCOME_LABELS.get(pred, pred)
                    rows.append({
                        "#"          : p.get("order", ""),
                        "Match"      : f"{home} vs {away}",
                        "Pred"       : pred,
                        "Result"     : label_p,
                        "Confidence" : round(conf, 2),
                    })

                if rows:
                    import pandas as pd
                    df = pd.DataFrame(rows)
                    st.dataframe(
                        df,
                        hide_index          = True,
                        use_container_width = True,
                        column_config       = {
                            "#"          : st.column_config.NumberColumn(width="small"),
                            "Pred"       : st.column_config.TextColumn(width="small"),
                            "Result"     : st.column_config.TextColumn(width="small"),
                            "Confidence" : st.column_config.NumberColumn(
                                               width="small", format="%.2f"
                                           ),
                        }
                    )
                    ticket_str = " - ".join(p["pred"] for p in matches_pro)
                    st.code(ticket_str, language="text")

    # Pick type legend
    st.markdown("---")
    legend_cols = st.columns(6)
    for i, (pt, icon) in enumerate(PICK_COLORS.items()):
        with legend_cols[i % 6]:
            st.caption(f"{icon} {pt}")


# ================================================================
# TAB 2 — RUN FORECAST
# ================================================================
def tab_run_forecast(jackpot: str):
    cfg = JACKPOTS[jackpot]
    st.subheader(f"⚙️ Run Forecast — {cfg['label']}")

    st.info(
        f"This will:\n"
        f"1. Run `{cfg['forecast_script']}` (auto-detects latest card)\n"
        f"2. Save the output to `{cfg['output_dir']}/`\n"
        f"3. Upload the forecast to Supabase"
    )

    # Optional: fetch card first
    with st.expander("Step 0 — Fetch Latest Card (optional)", expanded=False):
        st.caption(
            f"Runs `{cfg['fetch_script']}` to download the latest card. "
            f"Skip if card is already up to date."
        )
        if st.button("Fetch Card", key=f"fetch_card_{jackpot}"):
            with st.spinner("Fetching card..."):
                ok, output = run_script(cfg["fetch_script"])
            if ok:
                st.success("Card fetched successfully.")
            else:
                st.error("Fetch failed.")
            st.code(output, language="text")

    st.markdown("---")

    model = st.selectbox(
        "Chronos model size",
        ["tiny", "small", "base"],
        index=1,
        key=f"model_{jackpot}",
        help="tiny=fastest, small=recommended, base=most accurate but slower"
    )

    samples = st.slider(
        "Forecast samples",
        min_value=100,
        max_value=1000,
        value=500,
        step=100,
        key=f"samples_{jackpot}",
        help="More samples = better P10/P50/P90 accuracy, slower runtime"
    )

    if st.button(
        f"🚀 Run {cfg['label']} Forecast",
        key=f"run_forecast_{jackpot}",
        type="primary"
    ):
        with st.spinner(
            f"Running forecast (model={model}, samples={samples})... "
            f"This takes 2-4 minutes."
        ):
            ok, output = run_script(
                cfg["forecast_script"],
                ["--model", model, "--samples", str(samples)]
            )

        st.code(output, language="text")

        if ok:
            st.success("Forecast complete. Saving to Supabase...")
            local_data = find_latest_local_forecast(jackpot)
            if local_data:
                saved_id = save_forecast(jackpot, local_data)
                if saved_id:
                    st.success(
                        f"Saved to Supabase. "
                        f"View in the **Forecast** tab."
                    )
                else:
                    st.warning(
                        "Forecast ran but Supabase save failed. "
                        "Check your secrets."
                    )
            else:
                st.warning(
                    "Script ran but no output file found. "
                    "Check the script output above."
                )
        else:
            st.error("Forecast script failed. See output above.")


# ================================================================
# TAB 3 — LOG RESULTS
# ================================================================
def tab_log_results(jackpot: str):
    cfg = JACKPOTS[jackpot]
    n   = cfg["num_games"]
    st.subheader(f"📋 Log Results — {cfg['label']}")

    # Step 1: fetch results
    st.markdown("**Step 1 — Fetch & Convert Results**")
    st.caption(
        "Runs `round_to_results.py` to convert the latest round file "
        "to `results.json`."
    )

    if st.button("Fetch Latest Results", key=f"fetch_results_{jackpot}"):
        with st.spinner("Converting round file to results.json..."):
            ok, output = run_script("round_to_results.py")
        if ok:
            st.success("results.json updated.")
        else:
            st.error("Conversion failed.")
        st.code(output, language="text")

    st.markdown("---")

    # Step 2: review and confirm results
    st.markdown("**Step 2 — Review & Confirm Results**")

    results_data = read_results_json()

    # Get match names from latest Supabase forecast
    forecast_data = get_latest_forecast(jackpot)
    match_names   = []
    if forecast_data:
        tickets = forecast_data.get("tickets", {})
        base    = tickets.get("base", {})
        matches = base.get("matches", [])
        for m in matches:
            home = m.get("home", m.get("home_team", "?"))
            away = m.get("away", m.get("away_team", "?"))
            match_names.append(f"{home} vs {away}")

    # Pre-fill from results.json if available
    prefilled_results = results_data.get("results", []) if results_data else []
    prefilled_scores  = results_data.get("scores", []) if results_data else []

    if not prefilled_results:
        st.info(
            "No results.json found or it is empty. "
            "Click 'Fetch Latest Results' above, or enter results manually below."
        )

    # Input grid
    results_input = []
    scores_input  = []

    with st.form(key=f"log_form_{jackpot}"):
        st.caption(
            f"Enter result (1=Home Win, X=Draw, 2=Away Win) "
            f"and optional score for each match."
        )

        for i in range(n):
            match_label = match_names[i] if i < len(match_names) else f"Match {i+1}"
            prefill_r   = prefilled_results[i] if i < len(prefilled_results) else "1"
            prefill_s   = str(prefilled_scores[i]) if (
                i < len(prefilled_scores) and prefilled_scores[i]
            ) else ""

            r_col, s_col, l_col = st.columns([1, 1, 4])

            with r_col:
                result = st.selectbox(
                    f"#{i+1}",
                    ["1", "X", "2"],
                    index=["1","X","2"].index(prefill_r)
                          if prefill_r in ["1","X","2"] else 0,
                    key=f"result_{jackpot}_{i}",
                    label_visibility="collapsed",
                )
                results_input.append(result)

            with s_col:
                score = st.text_input(
                    f"score_{i}",
                    value=prefill_s,
                    placeholder="2-1",
                    key=f"score_{jackpot}_{i}",
                    label_visibility="collapsed",
                )
                scores_input.append(score if score else None)

            with l_col:
                st.caption(f"**{i+1}.** {match_label}")

        submitted = st.form_submit_button(
            "✅ Log Results",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        # Write results.json with confirmed values
        confirmed = {
            "results": results_input,
            "scores" : scores_input,
        }
        results_path = os.path.join(ROOT, "results.json")
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(confirmed, f, indent=2)

        # Run log_actuals.py
        with st.spinner("Logging actuals..."):
            ok, output = run_script(
                "log_actuals.py",
                ["--jackpot", jackpot, "--file", "results.json"]
            )

        st.code(output, language="text")

        if ok:
            # Read the updated forecast file and push actuals to Supabase
            local_data = find_latest_local_forecast(jackpot)
            if local_data and "actuals" in local_data:
                if forecast_data:
                    saved = save_actuals(
                        jackpot,
                        forecast_data["id"],
                        local_data["actuals"]
                    )
                    if saved:
                        best  = local_data["actuals"].get("best_score", 0)
                        label = score_label(best, n)
                        st.success(
                            f"Results logged successfully! "
                            f"Best score: {label}"
                        )
                        st.balloons()
                    else:
                        st.warning("Logged locally but Supabase save failed.")
                else:
                    st.warning(
                        "Logged locally but no matching forecast in Supabase."
                    )
            else:
                st.warning("Log script ran but actuals block not found in output.")
        else:
            st.error("Logging failed. See output above.")


# ================================================================
# TAB 4 — PERFORMANCE
# ================================================================
def tab_performance(jackpot: str):
    cfg = JACKPOTS[jackpot]
    n   = cfg["num_games"]
    st.subheader(f"📊 Performance — {cfg['label']}")

    with st.spinner("Loading performance data..."):
        rows = get_performance(jackpot, limit=30)

    if not rows:
        st.info(
            "No logged rounds yet. "
            "Run forecasts and log results to see performance here."
        )
        return

    # Summary metrics
    logged_rows = [r for r in rows if r["Logged"] == "✓"]
    if logged_rows:
        scores      = [r["Best Score"] for r in logged_rows if isinstance(r["Best Score"], int)]
        avg_score   = round(sum(scores) / len(scores), 1) if scores else 0
        best_ever   = max(scores) if scores else 0
        draw_errors = [
            abs(r["Draw Error"])
            for r in logged_rows
            if isinstance(r.get("Draw Error"), int)
        ]
        avg_draw_err = round(sum(draw_errors) / len(draw_errors), 1) if draw_errors else 0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Rounds Logged", len(logged_rows))
        m2.metric("Average Score", f"{avg_score}/{n}")
        m3.metric("Best Score", f"{best_ever}/{n}")
        m4.metric("Avg Draw Error", f"±{avg_draw_err}")

        st.markdown("---")

    # Performance table
    import pandas as pd

    display_rows = []
    for r in rows:
        score = r["Best Score"]
        if isinstance(score, int):
            score_str = score_label(score, n)
        else:
            score_str = "—"

        draw_pred = r.get("Draw Predicted", "-")
        draw_act  = r.get("Draw Actual", "-")
        draw_err  = r.get("Draw Error", "-")

        if isinstance(draw_err, int):
            draw_err_str = (
                f"+{draw_err}" if draw_err > 0 else
                str(draw_err)  if draw_err < 0 else "exact"
            )
        else:
            draw_err_str = "—"

        display_rows.append({
            "Date"         : r["Date"],
            "Score"        : score_str,
            "Best Ticket"  : r.get("Best Ticket", "—"),
            "Draw Pred"    : draw_pred,
            "Draw Actual"  : draw_act,
            "Draw Error"   : draw_err_str,
            "Logged"       : r["Logged"],
        })

    df = pd.DataFrame(display_rows)
    st.dataframe(
        df,
        hide_index          = True,
        use_container_width = True,
    )

    # Draw prediction accuracy chart
    if logged_rows:
        draw_data = [
            {
                "Round" : r["Date"],
                "Predicted" : r.get("Draw Predicted"),
                "Actual"    : r.get("Draw Actual"),
            }
            for r in logged_rows
            if isinstance(r.get("Draw Predicted"), (int, float))
            and isinstance(r.get("Draw Actual"), int)
        ]

        if draw_data:
            st.markdown("**Draw Count — Predicted vs Actual**")
            chart_df = pd.DataFrame(draw_data).set_index("Round")
            st.line_chart(chart_df, use_container_width=True)


# ================================================================
# MAIN LAYOUT
# ================================================================
def main():
    # Sidebar — jackpot selector
    st.sidebar.title("🎯 Chronos")
    st.sidebar.caption("Jackpot Forecast Dashboard")
    st.sidebar.markdown("---")

    jackpot = st.sidebar.radio(
        "Select Jackpot",
        options=list(JACKPOTS.keys()),
        format_func=lambda k: f"{JACKPOTS[k]['icon']} {JACKPOTS[k]['label']}",
        key="jackpot_selector",
    )

    cfg = JACKPOTS[jackpot]

    st.sidebar.markdown("---")
    st.sidebar.caption(
        f"**{cfg['label']}**  \n"
        f"{cfg['num_games']} matches per round"
    )

    # Main tabs
    tab1, tab2, tab3, tab4 = st.tabs([
        "📈 Forecast",
        "⚙️ Run Forecast",
        "📋 Log Results",
        "📊 Performance",
    ])

    with tab1:
        tab_forecast(jackpot)

    with tab2:
        tab_run_forecast(jackpot)

    with tab3:
        tab_log_results(jackpot)

    with tab4:
        tab_performance(jackpot)


if __name__ == "__main__":
    main()
