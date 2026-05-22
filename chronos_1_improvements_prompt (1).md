# Chronos 1.0 — Improvement Implementation Prompt
*For Freebuff CLI. Run from `C:/Users/User/Desktop/Chronos/`.*

---

## Context

You are modifying three forecast scripts in the Chronos jackpot prediction system:

- `mozzart/mozzart_forecast.py` — 16-match Mozzart Daily jackpot
- `midweek/midweek_forecast.py` — 13-match SportPesa Mid-Week jackpot
- `sportpesa/sportpesa_forecast.py` — 17-match SportPesa Mega jackpot

All three scripts follow the same pipeline:
1. Load historical batch data → compute round features (Sets A–D)
2. Run Chronos forecast → get P10/P50/P90 for total_draws, total_homes, total_aways
3. Build 3 tickets (Conservative/Base/Draw-Heavy) using greedy allocation
4. Save output JSON

The key problem: Chronos outputs the rolling mean (~6 draws) regardless of card signals,
causing systematic overprediction in low-draw rounds.

You will implement **3 improvements** across all three scripts. Do NOT change any other
logic, function signatures, file paths, batch loading, card loading, or output JSON
structure — only the specific additions described below.

---

## Improvement 1 — Rules-Based Draw Classifier

### What it does
Replaces the Chronos draw count with a rules-based classifier that reads Feature Set D
flags directly from `card_feat`. Homes and aways still come from Chronos unchanged.

### Where to add it
Add a new function `rules_based_draw_forecast(card_feat, total_matches)` in **Section 6**
(Chronos Forecast) of each script, immediately before `load_chronos()`.

### Function logic (identical in all three scripts except the threshold table)

```python
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
    mirror_count = card_feat["mirror_count"]   # read raw count, not the flag
    clear_fav    = card_feat["clear_fav_ge3_flag"]
    draws_t1     = card_feat["draws_t1"]

    mirror = int(mirror_count >= 4)   # local threshold — overrides mirror_ge3_flag

    # Tiebreaker threshold: draws_t1 >= median of regime thresholds
    # Mozzart: 5 | Midweek: 4 | Mega: 5
    TIEBREAK_THRESHOLD = <see per-script values below>

    if mirror == 1 and clear_fav == 0:
        regime = "Draw-Heavy"
    elif clear_fav == 1 and mirror == 0:
        regime = "Decisive"
    elif mirror == 0 and clear_fav == 0:
        regime = "Balanced"
    else:
        # both flags = 1: use draws_t1 as tiebreaker
        if draws_t1 >= TIEBREAK_THRESHOLD:
            regime = "Draw-Heavy"
        else:
            regime = "Decisive"

    thresholds = DRAW_THRESHOLDS[regime]
    return {
        "P10"        : thresholds["P10"],
        "P50"        : thresholds["P50"],
        "P90"        : thresholds["P90"],
        "context_len": "rules",   # not a Chronos context — label it clearly
        "regime"     : regime,
        "source"     : "rules_classifier",
    }
```

### Per-script threshold tables and tiebreak values

**mozzart_forecast.py** (16 games, ~34.6% draw base rate):
```python
DRAW_THRESHOLDS = {
    "Draw-Heavy": {"P10": 7, "P50": 8, "P90": 9},
    "Balanced"  : {"P10": 4, "P50": 5, "P90": 6},
    "Decisive"  : {"P10": 3, "P50": 4, "P90": 5},
}
TIEBREAK_THRESHOLD = 5
```

**midweek_forecast.py** (13 games, ~26% draw base rate):
```python
DRAW_THRESHOLDS = {
    "Draw-Heavy": {"P10": 5, "P50": 6, "P90": 7},
    "Balanced"  : {"P10": 3, "P50": 4, "P90": 5},
    "Decisive"  : {"P10": 2, "P50": 3, "P90": 4},
}
TIEBREAK_THRESHOLD = 4
```

**sportpesa_forecast.py** (17 games, ~33% draw base rate):
```python
DRAW_THRESHOLDS = {
    "Draw-Heavy": {"P10": 7, "P50": 8, "P90": 10},
    "Balanced"  : {"P10": 4, "P50": 6, "P90": 7},
    "Decisive"  : {"P10": 3, "P50": 4, "P90": 5},
}
TIEBREAK_THRESHOLD = 5
```

Place `DRAW_THRESHOLDS` and `TIEBREAK_THRESHOLD` as local constants inside the
`rules_based_draw_forecast` function body, not at module level.

### Where to call it (in `main()`, after Chronos forecast runs)

In the `main()` function of each script, find the block that calls `run_all_forecasts()`
and add the override **immediately after** it:

```python
    # --- Rules-based draw override (Improvement 1) ---
    rules_draw = rules_based_draw_forecast(card_feat, len(card))
    forecast["total_draws"] = rules_draw
    print(f"\n  [Draw Override] Regime: {rules_draw['regime']}  "
          f"P10={rules_draw['P10']}  P50={rules_draw['P50']}  P90={rules_draw['P90']}")
```

### Output JSON change
In the saved output dict, add a `"draw_classifier"` key at the top level alongside
`"forecast"`:

```python
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
```

---

## Improvement 3 — Dixon-Coles Blend Hook in `score_match()`

### What it does
Adds an optional `dc_probs` parameter to `score_match()`. When `None` (default), the
function behaves exactly as today — zero behaviour change. When a dict of
`{"1": float, "X": float, "2": float}` is passed in future DC integration, it blends
25% DC + 75% odds-implied before applying draw aversion and calibration corrections.

### Change to make in `score_match()` in all three scripts

Replace the current function signature:
```python
def score_match(match, base_rates):
```
with:
```python
def score_match(match, base_rates, dc_probs=None):
```

After the existing `impl` normalisation block (after draw aversion correction and
re-normalisation), add the following blend block:

```python
    # Dixon-Coles blend hook (Improvement 3)
    # dc_probs: {"1": float, "X": float, "2": float} normalised to sum=1
    # Currently inactive (dc_probs=None). Wire is here for future DC integration.
    if dc_probs is not None:
        dc_total = sum(dc_probs.values())
        dc_norm  = {k: v / dc_total for k, v in dc_probs.items()}
        impl     = {k: 0.25 * dc_norm[k] + 0.75 * impl[k] for k in impl}
        impl_total = sum(impl.values())
        impl     = {k: v / impl_total for k, v in impl.items()}
```

Place this block immediately before the `# Favourite calibration` comment.
Do NOT change any call sites — `dc_probs` defaults to `None` everywhere.

---

## Improvement 4 — Recency Bias Correction in `run_all_forecasts()`

### What it does
Before returning the Chronos forecast results, corrects the raw Chronos P50 draw
prediction toward a recency-weighted average (last 5 rounds weighted 60%, full
history 40%). The correction shifts P10/P50/P90 together by the same delta,
preserving the spread shape. This feeds into the tiebreaker logic of Improvement 1
via `draws_t1` but does NOT override the rules classifier output.

### Where to add it
In `run_all_forecasts()` in all three scripts, add the recency correction block
immediately after the `best` dict is assembled and before the `return best` statement:

```python
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
```

The `fm` variable (list of feature dicts) is already in scope inside `run_all_forecasts()`
because it is passed as the `fm` parameter in all three scripts.

---

## What NOT to change

- Do not modify `load_batches()`, `load_card()`, `load_batch()`, or any file path logic
- Do not modify `allocate_ticket()`, `clamp_counts()`, or `regime_label()`
- Do not modify `classify_match()` or pick type logic
- Do not modify `print_ticket()`, `print_signals()`, or `print_pick_summary()`
- Do not modify `make_pro_ticket()` (sportpesa only)
- Do not change the output JSON structure except for adding the `"draw_classifier"` key
  at the top level and the three new keys inside `forecast["total_draws"]`
- Do not add any new imports
- Do not change function call signatures at call sites (all new parameters default to None)

---

## Verification checklist after implementation

For each of the three scripts, confirm:

1. `rules_based_draw_forecast()` exists in Section 6, before `load_chronos()`, and
   uses `mirror_count >= 4` (NOT `mirror_ge3_flag`) for the Draw-Heavy decision
2. `forecast["total_draws"]` is overridden after `run_all_forecasts()` returns in `main()`
3. `score_match()` signature is `(match, base_rates, dc_probs=None)` and all existing
   call sites still pass only `match` and `base_rates` (no breakage)
4. `run_all_forecasts()` prints the recency correction line and the corrected values
   appear in `best["total_draws"]` before returning
5. Output JSON has a top-level `"draw_classifier"` key with regime, source, P10/P50/P90,
   and flags sub-dict
6. Running `python mozzart/mozzart_forecast.py --model tiny --samples 50` completes
   without error (quick smoke test, tiny model for speed)
