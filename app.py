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
    get_unscored_forecasts,
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

def extract_date_from_filename(filepath: str) -> str:
    """
    Sort forecasts and round files by date in filename, not mtime.
    Fixes Streamlit Cloud where all files share the same mtime after deploy.
    Returns sortable key: "YYYY-MM-DD" or "YYYY-MM-DD_HHMMSS".
    """
    name  = os.path.basename(filepath)
    parts = name.replace(".json", "").split("_")
    date_parts = [p for p in parts if len(p) == 10 and p.count("-") == 2]
    time_parts = [p for p in parts if len(p) == 6 and p.isdigit()]
    if date_parts:
        key = date_parts[0]
        if time_parts:
            key += "_" + time_parts[0]
        return key
    return str(os.path.getmtime(filepath))


def _format_forecast_label(filename: str) -> str:
    """
    Build a human-readable label for a forecast filename.
    mozzart_daily_2026-05-09_094837.json → "2026-05-09 09:48 — mozzart_daily_2026-05-09_094837.json"
    """
    parts      = filename.replace(".json", "").split("_")
    date_parts = [p for p in parts if len(p) == 10 and p.count("-") == 2]
    time_parts = [p for p in parts if len(p) == 6 and p.isdigit()]
    if date_parts:
        label = date_parts[0]
        if time_parts:
            t = time_parts[0]
            label += f" {t[:2]}:{t[2:4]}"
        return f"{label} — {filename}"
    return filename


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
    files   = sorted(glob.glob(pattern), key=extract_date_from_filename, reverse=True)
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
def find_unscored_local_forecasts(jackpot: str) -> list:
    """
    Return all forecast files that have no actuals block yet.
    Merges local files + Supabase records so forecasts run on other
    machines (or whose local file was lost) still appear in the dropdown.
    Local files take priority when both exist for the same card_file.
    """
    cfg     = JACKPOTS[jackpot]
    pattern = os.path.join(ROOT, cfg["output_dir"], cfg["forecast_pattern"])
    files   = sorted(glob.glob(pattern), key=extract_date_from_filename, reverse=True)

    # ── local files ──────────────────────────────────────────────
    unscored   = []
    seen_cards = set()          # card_file values already covered by a local file
    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            if "actuals" not in data:
                fname = os.path.basename(fpath)
                parts      = fname.replace(".json", "").split("_")
                date_parts = [p for p in parts if len(p) == 10 and p.count("-") == 2]
                date_str   = date_parts[0] if date_parts else ""
                card_file  = data.get("card_file", "unknown")
                seen_cards.add(card_file)
                unscored.append({
                    "filename"    : fname,
                    "filepath"    : fpath,
                    "card_file"   : card_file,
                    "generated_at": data.get("generated_at", "")[:16].replace("_", " "),
                    "date_str"    : date_str,
                    "data"        : data,
                    "source"      : "local",
                })
        except Exception:
            continue

    # ── Supabase fallback: forecasts with no actuals not covered locally ──
    try:
        sb_forecasts = list_forecasts(jackpot, limit=30)
        sb_actuals_ids = set()
        # Collect forecast_ids that already have actuals in Supabase
        from db import get_client
        client = get_client()
        actuals_result = (
            client.table("actuals")
            .select("forecast_id")
            .eq("jackpot", jackpot)
            .execute()
        )
        sb_actuals_ids = {r["forecast_id"] for r in (actuals_result.data or [])}

        for sb in sb_forecasts:
            card_file = sb.get("card_file", "")
            if card_file in seen_cards:
                continue                        # already covered by local file
            if sb["id"] in sb_actuals_ids:
                continue                        # already scored in Supabase
            # Build a synthetic entry from Supabase metadata
            gen = sb.get("generated_at", "")
            # generated_at from Supabase is ISO: "2026-05-14T07:31:09"
            date_str = gen[:10] if gen else ""
            display_gen = gen[:16].replace("T", " ") if gen else ""
            # Derive a synthetic filename from card_file for display
            fname = f"[Supabase] {card_file}"
            unscored.append({
                "filename"    : fname,
                "filepath"    : None,           # no local file
                "card_file"   : card_file,
                "generated_at": display_gen,
                "date_str"    : date_str,
                "data"        : {},             # no local data; matches won't pre-fill
                "source"      : "supabase",
                "supabase_id" : sb["id"],
            })
            seen_cards.add(card_file)
    except Exception:
        pass                                    # Supabase unavailable — local only

    # Sort combined list by date_str descending
    unscored.sort(key=lambda u: u["date_str"], reverse=True)
    return unscored


def get_all_round_files(jackpot: str) -> list[dict]:
    """
    Return all round files for a jackpot sorted by filename date desc.
    Each dict: {filename, filepath, date_str, display_label}
    """
    rounds_dir = os.path.join(ROOT, "rounds")
    if jackpot == "mozzart":
        all_files = glob.glob(os.path.join(rounds_dir, "round_*.json"))
        files = [
            f for f in all_files
            if "mega" not in os.path.basename(f)
            and "midweek" not in os.path.basename(f)
        ]
    elif jackpot == "midweek":
        files = glob.glob(os.path.join(rounds_dir, "round_midweek_*.json"))
    else:  # sportpesa
        files = glob.glob(os.path.join(rounds_dir, "round_mega_*.json"))

    files.sort(key=extract_date_from_filename, reverse=True)
    result = []
    for f in files:
        date_str = extract_date_from_filename(f)
        filename = os.path.basename(f)
        result.append({
            "filename"     : filename,
            "filepath"     : f,
            "date_str"     : date_str,
            "display_label": f"{date_str} — {filename}",
        })
    return result


def tab_log_results(jackpot: str):
    cfg = JACKPOTS[jackpot]
    n   = cfg["num_games"]
    st.subheader(f"📋 Log Results — {cfg['label']}")

    fetch_key = f"fetched_results_{jackpot}"

    RESULTS_FETCH_SCRIPTS = {
        "mozzart"  : "super_jackpot_results.py",
        "midweek"  : None,
        "sportpesa": None,
    }

    # Resolve selectors early so the fetch button can use the selected round
    unscored    = get_unscored_forecasts(jackpot)
    round_files = get_all_round_files(jackpot)

    if not unscored:
        st.info(
            "No unscored forecasts found locally. "
            "All forecasts have been logged, or no forecasts exist yet."
        )
        return

    # ── Step 1 ────────────────────────────────────────────────────
    st.markdown("**Step 1 — Fetch & Convert Results**")
    st.caption(
        "Runs `round_to_results.py` with the round file selected in Step 2 "
        "to write `results.json`."
    )

    if st.button("Fetch Latest Results", key=f"fetch_results_{jackpot}"):
        fetch_script = RESULTS_FETCH_SCRIPTS[jackpot]
        if fetch_script is not None:
            with st.spinner("Fetching latest results from API..."):
                ok1, out1 = run_script(fetch_script)
            st.code(out1, language="text")
            if not ok1:
                st.error("Results fetch failed — continuing to conversion step.")
        else:
            st.info(
                "No automatic results fetcher available for this jackpot yet. "
                "Add results manually below."
            )

        # Use whichever round is currently selected in the Step 2 selector
        _rd_label = st.session_state.get(f"round_selector_{jackpot}")
        _rd_map   = {r["display_label"]: r for r in round_files}
        _sel_round = _rd_map.get(_rd_label, round_files[0] if round_files else None)

        if _sel_round:
            with st.spinner("Converting round file to results.json..."):
                ok2, out2 = run_script(
                    "round_to_results.py",
                    ["--file", _sel_round["filepath"]]
                )
            st.code(out2, language="text")
            if ok2:
                st.success("results.json updated.")
                st.session_state[fetch_key] = read_results_json()
                st.session_state[f"last_round_{jackpot}"] = _sel_round["filename"]
            else:
                st.error("Conversion failed.")
        else:
            st.warning("No round files found in rounds/ — cannot convert.")

    st.markdown("---")

    # ── Step 2 ────────────────────────────────────────────────────
    st.markdown("**Step 2 — Match Forecast to Results**")

    fc_col, rd_col = st.columns(2)

    with fc_col:
        fc_options      = {u["display"]: u for u in unscored}
        chosen_fc_label = st.selectbox(
            "Select forecast to log",
            list(fc_options.keys()),
            key=f"forecast_selector_{jackpot}",
        )
        selected_forecast = fc_options[chosen_fc_label]

    with rd_col:
        # Date from forecast date_str (e.g. "2026-05-14")
        fc_date_str = selected_forecast["date_str"]

        # Default selection: exact date match → nearest after → nearest before
        default_idx = 0
        if round_files and fc_date_str:
            try:
                fc_dt = datetime.strptime(fc_date_str, "%Y-%m-%d")
                # 1. Exact match
                exact = next(
                    (i for i, r in enumerate(round_files)
                     if r["date_str"][:10] == fc_date_str),
                    None
                )
                if exact is not None:
                    default_idx = exact
                else:
                    # 2. Nearest on or after forecast date
                    after = [
                        (i, r) for i, r in enumerate(round_files)
                        if datetime.strptime(r["date_str"][:10], "%Y-%m-%d") >= fc_dt
                    ]
                    if after:
                        # pick the one with smallest gap (closest after)
                        default_idx = min(after, key=lambda x: (
                            datetime.strptime(x[1]["date_str"][:10], "%Y-%m-%d") - fc_dt
                        ).days)[0]
                    else:
                        # 3. Fall back to nearest before
                        before = [
                            (i, r) for i, r in enumerate(round_files)
                            if datetime.strptime(r["date_str"][:10], "%Y-%m-%d") < fc_dt
                        ]
                        if before:
                            default_idx = min(before, key=lambda x: (
                                fc_dt - datetime.strptime(x[1]["date_str"][:10], "%Y-%m-%d")
                            ).days)[0]
            except Exception:
                default_idx = 0

        rd_labels       = [r["display_label"] for r in round_files]
        chosen_rd_label = st.selectbox(
            "Select results round",
            rd_labels,
            index=default_idx,
            key=f"round_selector_{jackpot}",
        )
        rd_map         = {r["display_label"]: r for r in round_files}
        selected_round = rd_map[chosen_rd_label]

    # Auto-convert whenever the selected round changes
    last_key = f"last_round_{jackpot}"
    if st.session_state.get(last_key) != selected_round["filename"]:
        st.session_state[last_key] = selected_round["filename"]
        ok, _ = run_script(
            "round_to_results.py",
            ["--file", selected_round["filepath"]]
        )
        if ok:
            fresh = read_results_json()
            if fresh:
                st.session_state[fetch_key] = fresh

    # Warn if no round file exists for the forecast date
    round_dates = {r["date_str"][:10] for r in round_files}
    if fc_date_str and fc_date_str not in round_dates:
        st.warning(
            f"No round file found for {fc_date_str}. "
            f"Click 'Fetch Latest Results' above to fetch from API, "
            f"or select a different round manually."
        )

    st.caption(
        f"Logging: **{selected_forecast['card_file']}** "
        f"\u2190 **{selected_round['filename']}**"
    )

    # Build match names from Supabase tickets data
    match_names = []
    base_matches = selected_forecast.get("tickets", {}).get("base", {}).get("matches", [])
    for m in base_matches:
        home = m.get("home", m.get("home_team", "?"))
        away = m.get("away", m.get("away_team", "?"))
        match_names.append(f"{home} vs {away}")

    # Fall back to match_analysis if base.matches is empty
    if not match_names:
        for m in selected_forecast.get("match_analysis", []):
            home = m.get("home", "?")
            away = m.get("away", "?")
            match_names.append(f"{home} vs {away}")

    forecast_data   = selected_forecast
    target_filename = selected_forecast.get("card_file", "")

    # Pre-fill from session state (populated by fetch button), fall back to empty
    session_data      = st.session_state.get(fetch_key, {})
    prefilled_results = session_data.get("results", [])
    prefilled_scores  = session_data.get("scores", [])

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
        tickets   = selected_forecast.get("tickets", {})
        forecast  = selected_forecast.get("forecast", {})

        # Score each ticket in-process
        ticket_scores = {}
        for scenario, td in tickets.items():
            predicted = td.get("ticket", [])
            if predicted:
                ticket_scores[scenario] = sum(
                    1 for p, a in zip(predicted, results_input) if p == a
                )

        best_ticket = max(ticket_scores, key=ticket_scores.get) if ticket_scores else ""
        best_score  = ticket_scores.get(best_ticket, 0) if best_ticket else 0

        # Distribution error vs Chronos P50
        dist_err = {}
        if forecast:
            actual_draws = results_input.count("X")
            actual_homes = results_input.count("1")
            actual_aways = results_input.count("2")
            p50_d = round(forecast.get("total_draws", {}).get("P50", 0))
            p50_h = round(forecast.get("total_homes", {}).get("P50", 0))
            p50_a = round(forecast.get("total_aways", {}).get("P50", 0))
            dist_err = {
                "predicted_draws": p50_d,
                "predicted_homes": p50_h,
                "predicted_aways": p50_a,
                "actual_draws"   : actual_draws,
                "actual_homes"   : actual_homes,
                "actual_aways"   : actual_aways,
                "draw_error"     : actual_draws - p50_d,
                "home_error"     : actual_homes - p50_h,
                "away_error"     : actual_aways - p50_a,
            }

        actuals_data = {
            "logged_at"         : datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "results"           : results_input,
            "scores"            : scores_input,
            "ticket_scores"     : ticket_scores,
            "best_ticket"       : best_ticket,
            "best_score"        : best_score,
            "distribution_error": dist_err,
            "signal_accuracy"   : {},
            "per_match_accuracy": {},
        }

        with st.spinner("Saving actuals to Supabase..."):
            saved = save_actuals(jackpot, selected_forecast["id"], actuals_data)

        if saved:
            st.success(
                f"Results logged! Best score: {score_label(best_score, n)} "
                f"({best_ticket})"
            )
            st.session_state.pop(fetch_key, None)
            st.session_state.pop(f"last_round_{jackpot}", None)
            st.session_state.pop(f"forecast_selector_{jackpot}", None)
            st.balloons()
            st.rerun()
        else:
            st.warning("Supabase save failed.")


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