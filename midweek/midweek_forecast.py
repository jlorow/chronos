"""
midweek_forecast.py  —  SportPesa Mid-Week Jackpot Forecast (Chronos)
=====================================================================
13-match jackpot running Tuesday/Wednesday/Thursday/Friday/early Saturday.
Separate product from the Mega Jackpot (17 games, weekends).

Directory structure (run from C:/Users/User/Desktop/Chronos/):
    midweek/data/batches/    <- BATCH_1_enriched.json ... BATCH_5_enriched.json
    midweek/data/cards/      <- jackpot_parsed_*.json  (weekly input card)
    midweek/data/enriched/   <- future FootyStats enrichment
    midweek/output/          <- midweek_forecast_*.json

Usage:
    cd C:/Users/User/Desktop/Chronos
    python midweek/midweek_forecast.py

    # Faster:
    python midweek/midweek_forecast.py --model tiny

League detection priority (temporary until scraper outputs league names):
    1. Card has 'league' or 'tournament' field -> use directly
    2. No league field -> check team name against known-teams dict
    3. Neither -> country -> top division mapping

Base rate policy:
    Uses raw batch rates. Recalculates automatically as more batches
    are added to the batches/ folder. No correction toward 35% —
    long-run anchor is 41-44% home as per dataset characteristics.

Tickets produced:
    Conservative (P10) / Base (P50) / Draw-Heavy (P90)
    All three are 13-match full tickets.
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
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))  # midweek/
ROOT_DIR     = os.path.dirname(BASE_DIR)                   # Chronos/
BATCHES_DIR  = os.path.join(BASE_DIR, "data", "batches")
CARDS_DIR    = os.path.join(BASE_DIR, "data", "cards")
ENRICHED_DIR = os.path.join(BASE_DIR, "data", "enriched")
OUTPUT_DIR   = os.path.join(BASE_DIR, "output")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ================================================================
# KNOWN TEAMS DICT
# Curated manually — used when card has no league field.
# Priority 2 in league detection chain.
# Covers teams known to appear in ambiguous countries (France/Italy
# especially) plus other commonly misassigned teams.
# Add new teams here as they appear on cards.
# ================================================================
KNOWN_TEAMS = {
    # France - Ligue 1
    "Paris Saint-Germain": "Ligue 1", "PSG": "Ligue 1",
    "Marseille": "Ligue 1", "Lyon": "Ligue 1",
    "Monaco": "Ligue 1", "Lille": "Ligue 1",
    "Nice": "Ligue 1", "Rennes": "Ligue 1",
    "Lens": "Ligue 1", "Strasbourg": "Ligue 1",
    "Montpellier": "Ligue 1", "Nantes": "Ligue 1",
    "Reims": "Ligue 1", "Toulouse": "Ligue 1",
    "Brest": "Ligue 1", "Le Havre": "Ligue 1",
    "Metz": "Ligue 1", "Clermont": "Ligue 1",
    "Lorient": "Ligue 1", "Auxerre": "Ligue 1",
    "AJ Auxerre": "Ligue 1", "Nimes": "Ligue 1",
    # France - Ligue 2
    "Guingamp": "Ligue 2", "Niort": "Ligue 2",
    "Sochaux": "Ligue 2", "Nancy": "Ligue 2",
    "Dunkerque": "Ligue 2", "Rodez": "Ligue 2",
    "Rodez Aveyron": "Ligue 2", "Bourg Peronnas": "Ligue 2",
    "FC Martigues": "Ligue 2", "Grenoble": "Ligue 2",
    "Caen": "Ligue 2", "Angers": "Ligue 2",
    "Laval": "Ligue 2", "Pau": "Ligue 2",
    "Amiens": "Ligue 2", "Bastia": "Ligue 2",
    "Quevilly Rouen": "Ligue 2",
    # France - National (3rd division)
    "Saint Brieuc": "National", "Villefranche Saone": "National",
    "Aubagne": "National", "Aubagne FC": "National",
    "Fleury": "National", "U.s. Fleury": "National",
    "Stade Briochin": "National", "Le Puy-en-Velay": "National",
    "Orleans": "National", "Versailles": "National",
    # Italy - Serie A
    "Juventus": "Serie A", "Inter Milan": "Serie A",
    "AC Milan": "Serie A", "Napoli": "Serie A",
    "Roma": "Serie A", "Lazio": "Serie A",
    "Atalanta": "Serie A", "Fiorentina": "Serie A",
    "Torino": "Serie A", "Bologna": "Serie A",
    "Udinese": "Serie A", "Sassuolo": "Serie A",
    "Empoli": "Serie A", "Verona": "Serie A",
    "Monza": "Serie A", "Lecce": "Serie A",
    "Frosinone": "Serie A", "Cagliari": "Serie A",
    "Genoa": "Serie A", "Salernitana": "Serie A",
    "Como": "Serie A", "Como 1907": "Serie A",
    "Venezia": "Serie A", "Parma": "Serie A",
    # Italy - Serie B
    "Pisa": "Serie B", "Ascoli": "Serie B",
    "Bari": "Serie B", "Palermo": "Serie B",
    "Cremonese": "Serie B", "Catanzaro": "Serie B",
    "Brescia": "Serie B", "Cittadella": "Serie B",
    "Sampdoria": "Serie B", "Spezia": "Serie B",
    "Modena": "Serie B", "Reggiana": "Serie B",
    "Sudtirol": "Serie B", "Cosenza": "Serie B",
    "Juve Stabia": "Serie B", "Carrarese": "Serie B",
    "Cesena": "Serie B", "Mantova": "Serie B",
    # England - Premier League
    "Arsenal": "Premier League", "Chelsea": "Premier League",
    "Liverpool": "Premier League", "Manchester City": "Premier League",
    "Manchester United": "Premier League", "Tottenham": "Premier League",
    "Newcastle": "Premier League", "Aston Villa": "Premier League",
    "Brighton": "Premier League", "West Ham": "Premier League",
    "Everton": "Premier League", "Brentford": "Premier League",
    "Fulham": "Premier League", "Wolverhampton": "Premier League",
    "Crystal Palace": "Premier League", "Nottingham Forest": "Premier League",
    "Luton Town": "Premier League", "Sheffield Utd": "Premier League",
    "Burnley": "Premier League", "Bournemouth": "Premier League",
    "Sunderland": "Premier League",
    # England - Championship
    "Leeds": "Championship", "Leicester": "Championship",
    "Ipswich": "Championship", "Southampton": "Championship",
    "Sheffield Wed": "Championship", "Middlesbrough": "Championship",
    "Blackburn": "Championship", "Bristol City": "Championship",
    "Millwall": "Championship", "Swansea": "Championship",
    "West Bromwich": "Championship", "Hull City": "Championship",
    "Preston": "Championship", "Stoke City": "Championship",
    "QPR": "Championship", "Coventry": "Championship",
    "Cardiff": "Championship", "Rotherham": "Championship",
    "Huddersfield": "Championship", "Birmingham": "Championship",
    "Barnsley": "Championship", "Wigan": "Championship",
    "Plymouth": "Championship",
    # England - League One
    "Oxford Utd": "League One", "Portsmouth": "League One",
    "Peterborough": "League One", "Derby": "League One",
    "Bristol Rovers": "League One", "Exeter": "League One",
    "Port Vale": "League One", "Walsall": "League One",
    "MK Dons": "League One", "Accrington": "League One",
    "Fleetwood Town": "League One", "Hartlepool": "League One",
    "Carlisle": "League One", "Northampton": "League One",
    "Doncaster": "League One", "Crawley Town": "League One",
    "Oldham": "League One",
    # Spain - La Liga
    "Real Madrid": "La Liga", "Barcelona": "La Liga",
    "Atletico Madrid": "La Liga", "Sevilla": "La Liga",
    "Sevilla FC": "La Liga", "Real Sociedad": "La Liga",
    "Villarreal": "La Liga", "Athletic Bilbao": "La Liga",
    "Real Betis": "La Liga", "Valencia": "La Liga",
    "Alaves": "La Liga", "Deportivo Alaves": "La Liga",
    "Getafe": "La Liga", "Osasuna": "La Liga",
    "Girona": "La Liga", "Mallorca": "La Liga",
    "Celta Vigo": "La Liga", "Cadiz": "La Liga",
    "Granada": "La Liga", "Las Palmas": "La Liga",
    # Spain - Segunda
    "Zaragoza": "Segunda Division", "Eibar": "Segunda Division",
    "Burgos": "Segunda Division", "Burgos CF": "Segunda Division",
    "Levante": "Segunda Division", "Mirandes": "Segunda Division",
    "Eldense": "Segunda Division", "Huesca": "Segunda Division",
    "Racing Santander": "Segunda Division",
    # Germany - Bundesliga
    "Bayern Munich": "Bundesliga", "Borussia Dortmund": "Bundesliga",
    "RB Leipzig": "Bundesliga", "Bayer Leverkusen": "Bundesliga",
    "Eintracht Frankfurt": "Bundesliga", "Wolfsburg": "Bundesliga",
    "VFL Wolfsburg": "Bundesliga", "Freiburg": "Bundesliga",
    "SC Freiburg": "Bundesliga", "Mainz": "Bundesliga",
    "FSV Mainz": "Bundesliga", "Hoffenheim": "Bundesliga",
    "Augsburg": "Bundesliga", "Werder Bremen": "Bundesliga",
    "Union Berlin": "Bundesliga", "Cologne": "Bundesliga",
    "Borussia Monchengladbach": "Bundesliga",
    "St. Pauli": "Bundesliga", "FC St. Pauli": "Bundesliga",
    # Portugal - Primeira Liga
    "FC Porto": "Primeira Liga", "Benfica": "Primeira Liga",
    "Sporting Lisbon": "Primeira Liga", "Sporting CP": "Primeira Liga",
    "Braga": "Primeira Liga", "Sporting Braga": "Primeira Liga",
    "Vitoria Guimaraes": "Primeira Liga", "Famalicao": "Primeira Liga",
    "GD Estoril": "Primeira Liga", "FC Arouca": "Primeira Liga",
    "Santa Clara": "Primeira Liga", "Casa Pia": "Primeira Liga",
    "Casa Pia Lisbon": "Primeira Liga",
    # Portugal - Liga 2
    "CD Tondela": "Liga Portugal 2", "Viseu": "Liga Portugal 2",
    "Sporting Lisbon B": "Liga Portugal 2",
    "Benfica Lisbon B": "Liga Portugal 2",
    # Netherlands - Eredivisie
    "Ajax": "Eredivisie", "PSV": "Eredivisie",
    "Feyenoord": "Eredivisie", "AZ Alkmaar": "Eredivisie",
    "FC Twente": "Eredivisie", "FC Twente Enschede": "Eredivisie",
    "FC Utrecht": "Eredivisie", "NEC Nijmegen": "Eredivisie",
    "FC Groningen": "Eredivisie", "Almere City": "Eredivisie",
    "Go Ahead Eagles": "Eredivisie", "NAC Breda": "Eredivisie",
    "Willem II": "Eredivisie", "Heracles": "Eredivisie",
    "RKC Waalwijk": "Eredivisie",
    # Scotland
    "Rangers": "Scottish Premiership", "Rangers FC": "Scottish Premiership",
    "Celtic": "Scottish Premiership", "Hearts": "Scottish Premiership",
    "Hibernian": "Scottish Premiership", "Aberdeen": "Scottish Premiership",
    "Motherwell": "Scottish Premiership", "Dundee": "Scottish Premiership",
    "Dundee FC": "Scottish Premiership", "Dundee Utd": "Scottish Premiership",
    "St. Mirren": "Scottish Premiership", "Ross County": "Scottish Premiership",
    "Livingston": "Scottish Premiership", "St. Johnstone": "Scottish Premiership",
    "Kilmarnock": "Scottish Premiership", "Inverness": "Scottish Premiership",
    "Raith Rovers": "Scottish Championship",
    "Morton": "Scottish Championship",
    "Airdrieonians": "Scottish Championship",
    # Ireland
    "Dundalk": "League of Ireland Premier Division",
    "Dundalk FC": "League of Ireland Premier Division",
    "Shelbourne": "League of Ireland Premier Division",
    "Bohemian": "League of Ireland Premier Division",
    "St Patricks": "League of Ireland Premier Division",
    "Athlone Town": "League of Ireland First Division",
    "Kerry FC": "League of Ireland First Division",
    # International (national teams)
    "England": "International", "France": "International",
    "Germany": "International", "Spain": "International",
    "Italy": "International", "Portugal": "International",
    "Brazil": "International", "Argentina": "International",
    "Netherlands": "International", "Belgium": "International",
    "Croatia": "International", "Denmark": "International",
    "Austria": "International", "Switzerland": "International",
    "Wales": "International", "Scotland": "International",
    "Ireland": "International", "Poland": "International",
    "Ukraine": "International", "Serbia": "International",
    "Romania": "International", "Hungary": "International",
    "Cyprus": "International", "Georgia": "International",
    "Australia": "International", "Japan": "International",
    "South Korea": "International", "Morocco": "International",
    "Senegal": "International", "Nigeria": "International",
    "Egypt": "International", "Ghana": "International",
}

# ================================================================
# COUNTRY -> LEAGUE FALLBACK (Priority 3)
# Used only when team name not in KNOWN_TEAMS dict
# ================================================================
COUNTRY_LEAGUE = {
    "cyprus"         : "Cyprus First Division",
    "scotland"       : "Scottish Premiership",
    "france"         : "Ligue 1",
    "italy"          : "Serie A",
    "portugal"       : "Primeira Liga",
    "ireland"        : "League of Ireland Premier Division",
    "denmark"        : "Danish Superliga",
    "czech republic" : "Czech Liga",
    "czech"          : "Czech Liga",
    "england"        : "Premier League",
    "spain"          : "La Liga",
    "germany"        : "Bundesliga",
    "netherlands"    : "Eredivisie",
    "belgium"        : "Belgian Pro League",
    "turkey"         : "Super Lig",
    "greece"         : "Super League Greece",
    "norway"         : "Eliteserien",
    "sweden"         : "Allsvenskan",
    "switzerland"    : "Swiss Super League",
    "austria"        : "Austrian Bundesliga",
    "poland"         : "Ekstraklasa",
    "brazil"         : "Brasileirao Serie A",
    "argentina"      : "Argentine Primera Division",
    "usa"            : "MLS",
    "mexico"         : "Liga MX",
    "australia"      : "A-League",
    "international"  : "International",
}

# ================================================================
# LEAGUE MAP -> feature key
# ================================================================
LEAGUE_MAP = {
    "premier league"                      : "premier_count",
    "serie a"                             : "serie_a_count",
    "la liga"                             : "la_liga_count",
    "bundesliga"                          : "bundesliga_count",
    "ligue 1"                             : "ligue1_count",
    "ligue 2"                             : "ligue2_count",
    "serie b"                             : "serie_b_count",
    "serie c"                             : "lega_pro_count",
    "segunda division"                    : "segunda_count",
    "championship"                        : "championship_count",
    "league one"                          : "league_one_count",
    "league two"                          : "league_two_count",
    "national league"                     : "national_league_count",
    "national"                            : "national_count",
    "eredivisie"                          : "eredivisie_count",
    "eerste divisie"                      : "eerste_divisie_count",
    "primeira liga"                       : "primeira_count",
    "liga portugal 2"                     : "liga_portugal2_count",
    "belgian pro league"                  : "belgium_count",
    "super lig"                           : "turkey_count",
    "scottish premiership"                : "scottish_count",
    "scottish championship"               : "scottish2_count",
    "swiss super league"                  : "swiss_count",
    "austrian bundesliga"                 : "austrian_count",
    "super league greece"                 : "greek_count",
    "ekstraklasa"                         : "polish_count",
    "czech liga"                          : "czech_count",
    "danish superliga"                    : "danish_count",
    "eliteserien"                         : "nordic_count",
    "allsvenskan"                         : "nordic_count",
    "superettan"                          : "nordic_count",
    "veikkausliiga"                       : "nordic_count",
    "brasileirao serie a"                 : "brasileirao_count",
    "brasileirao serie b"                 : "brasileirao_count",
    "argentine primera division"          : "arg_count",
    "mls"                                 : "mls_count",
    "liga mx"                             : "liga_mx_count",
    "a-league"                            : "aus_count",
    "a league"                            : "aus_count",
    "colombian liga betplay"              : "copa_count",
    "copa sudamericana"                   : "copa_count",
    "copa libertadores"                   : "copa_count",
    "j1 league"                           : "j_league_count",
    "j2 league"                           : "j_league_count",
    "k league 1"                          : "k_league_count",
    "k league 2"                          : "k_league_count",
    "league of ireland premier division"  : "ireland_count",
    "league of ireland first division"    : "ireland_count",
    "saudi pro league"                    : "gulf_count",
    "slovak super liga"                   : "slovak_count",
    "armenian premier league"             : "unknown_count",
    "international"                       : "international_count",
    "uefa champions league"               : "european_count",
    "uefa europa league"                  : "european_count",
    "uefa conference league"              : "european_count",
    "champions league"                    : "european_count",
    "europa league"                       : "european_count",
    "conference league"                   : "european_count",
    "cyprus first division"               : "cyprus_count",
}

# Corrected draw rates per league key
LEAGUE_DRAW_PCT = {
    "premier_count"       : 0.25,
    "serie_a_count"       : 0.32,
    "la_liga_count"       : 0.25,
    "bundesliga_count"    : 0.23,
    "ligue1_count"        : 0.27,
    "ligue2_count"        : 0.37,
    "serie_b_count"       : 0.32,
    "lega_pro_count"      : 0.31,
    "segunda_count"       : 0.35,
    "championship_count"  : 0.28,
    "league_one_count"    : 0.26,
    "league_two_count"    : 0.26,
    "national_league_count": 0.25,
    "national_count"      : 0.27,
    "eredivisie_count"    : 0.25,
    "eerste_divisie_count": 0.20,
    "primeira_count"      : 0.26,
    "liga_portugal2_count": 0.28,
    "belgium_count"       : 0.22,
    "turkey_count"        : 0.25,
    "scottish_count"      : 0.27,
    "scottish2_count"     : 0.26,
    "swiss_count"         : 0.27,
    "austrian_count"      : 0.26,
    "greek_count"         : 0.26,
    "polish_count"        : 0.27,
    "czech_count"         : 0.27,
    "danish_count"        : 0.27,
    "nordic_count"        : 0.27,
    "brasileirao_count"   : 0.29,
    "arg_count"           : 0.29,
    "mls_count"           : 0.26,
    "liga_mx_count"       : 0.28,
    "aus_count"           : 0.27,
    "copa_count"          : 0.30,
    "j_league_count"      : 0.24,
    "k_league_count"      : 0.25,
    "ireland_count"       : 0.27,
    "gulf_count"          : 0.24,
    "slovak_count"        : 0.26,
    "cyprus_count"        : 0.25,
    "international_count" : 0.25,
    "european_count"      : 0.26,
    "unknown_count"       : 0.28,
}

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
    "brasileirao_count", "championship_count",
}

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
# 2. LEAGUE DETECTION  (3-priority chain)
# ================================================================
def detect_league(match):
    """
    Priority 1: card has 'league' or 'tournament' field
    Priority 2: team name in KNOWN_TEAMS dict
    Priority 3: country -> top division fallback
    """
    # Priority 1: explicit league field
    for field in ["league", "tournament"]:
        val = match.get(field, "")
        if val and val.strip().lower() not in ("", "none", "unknown"):
            return val.strip()

    # Priority 2: team name lookup
    for team_field in ["home", "home_team"]:
        team = match.get(team_field, "")
        if team in KNOWN_TEAMS:
            return KNOWN_TEAMS[team]
    for team_field in ["away", "away_team"]:
        team = match.get(team_field, "")
        if team in KNOWN_TEAMS:
            return KNOWN_TEAMS[team]

    # Priority 3: country fallback
    country = match.get("country", "").strip().lower()
    return COUNTRY_LEAGUE.get(country, "Unknown")


def normalise_league(league_str):
    """Map league string to feature key."""
    if not league_str:
        return "unknown_count"
    key = league_str.strip().lower()
    if key in LEAGUE_MAP:
        return LEAGUE_MAP[key]
    for pattern, feat in LEAGUE_MAP.items():
        if pattern in key:
            return feat
    return "unknown_count"


def get_draw_aversion(league_key):
    return DRAW_AVERSION.get(league_key, DRAW_AVERSION["default"])


# ================================================================
# 3. BATCH LOADER  ({"rounds":[...]} format with metadata wrapper)
# ================================================================
def load_batches():
    files = sorted(glob.glob(os.path.join(BATCHES_DIR, "BATCH_*.json")))
    if not files:
        # Also try lowercase
        files = sorted(glob.glob(os.path.join(BATCHES_DIR, "batch_*.json")))
    if not files:
        raise FileNotFoundError(
            f"No BATCH_*.json files found in:\n  {BATCHES_DIR}\n"
            "Copy BATCH_1_enriched.json ... BATCH_5_enriched.json here."
        )

    rounds = []
    for fpath in files:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)

        # Handle both flat list and {"rounds":[...]} formats
        if isinstance(data, list):
            raw_rounds = data
        elif isinstance(data, dict) and "rounds" in data:
            raw_rounds = data["rounds"]
        else:
            print(f"  [SKIP] {os.path.basename(fpath)} — unrecognised format")
            continue

        valid = 0
        for rnd in raw_rounds:
            matches = rnd.get("matches", [])
            results = [
                m.get("actual_result", m.get("result", ""))
                for m in matches
            ]
            if all(r in ("1", "X", "2") for r in results) and len(results) == 13:
                rounds.append(rnd)
                valid += 1
            else:
                missing = sum(1 for r in results if r not in ("1","X","2"))
                wrong_n = len(results) != 13
                reason  = []
                if missing: reason.append(f"{missing} missing results")
                if wrong_n: reason.append(f"expected 13 matches, got {len(results)}")
                print(f"  [SKIP] {os.path.basename(fpath)} "
                      f"round {rnd.get('round_id','?')} — {', '.join(reason)}")

        print(f"  {os.path.basename(fpath)} -> {valid} valid rounds")

    print(f"  Total: {len(rounds)} rounds loaded")
    return rounds


# ================================================================
# 4. CARD LOADER  (local file with Supabase fallback)
# ================================================================
def load_card():
    local_files = glob.glob(os.path.join(CARDS_DIR, "jackpot_parsed_*.json"))
    if local_files:
        path = max(local_files, key=os.path.getmtime)
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        source = os.path.basename(path)
    else:
        print("  No local card found — trying Supabase...")
        sys.path.insert(0, ROOT_DIR)
        from db import get_latest_card
        raw, fetched_at = get_latest_card("midweek")
        if not raw:
            raise FileNotFoundError(
                "No jackpot_parsed_*.json locally and no card in Supabase.\n"
                "Run fetch_sportpesa_midweek_jackpot.py locally first."
            )
        source = f"supabase:{fetched_at}"
        print(f"  Loaded card from Supabase (fetched {fetched_at})")

    card = []
    for i, m in enumerate(raw):
        league = detect_league(m)
        card.append({
            "order"    : i + 1,
            "event_id" : m.get("event_id", ""),
            "home"     : m.get("home", m.get("home_team", "")),
            "away"     : m.get("away", m.get("away_team", "")),
            "league"   : league,
            "country"  : m.get("country", ""),
            "kickoff"  : m.get("kickoff", ""),
            "odds_1"   : float(m.get("home_odd", m.get("odds_1", 2.5))),
            "odds_x"   : float(m.get("draw_odd", m.get("odds_x", 3.3))),
            "odds_2"   : float(m.get("away_odd", m.get("odds_2", 2.5))),
        })

    print(f"  Card    : {source}  [auto-detected]")
    print(f"  Matches : {len(card)}")
    print(f"  League detection:")
    for m in card:
        print(f"    [{m['order']:>2}] {m['home']:<30} -> {m['league']}")

    return card, source


# ================================================================
# 5. FEATURE ENGINEERING
# ================================================================
def get_result(match):
    return match.get("actual_result", match.get("result", ""))


def is_upset(match):
    o1 = match["odds_1"]
    ox = match["odds_x"]
    o2 = match["odds_2"]
    min_odds = min(o1, ox, o2)
    fav = "1" if o1 == min_odds else ("X" if ox == min_odds else "2")
    return get_result(match) != fav


def compute_round_features(rnd):
    matches = rnd["matches"]
    n       = len(matches)

    # Normalise odds fields (batch uses odds_1/x/2 as floats)
    odds1   = [float(m["odds_1"]) for m in matches]
    oddsx   = [float(m["odds_x"]) for m in matches]
    odds2   = [float(m["odds_2"]) for m in matches]
    results = [get_result(m) for m in matches]

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
    upset_rate      = sum(is_upset(m) for m in matches) / n

    # League features — use batch league field directly
    league_counts = {k: 0 for k in LEAGUE_DRAW_PCT}
    for m in matches:
        raw_league = m.get("league", "")
        key = normalise_league(raw_league)
        league_counts[key] = league_counts.get(key, 0) + 1

    draw_pct_weighted = sum(
        league_counts.get(k, 0) * v for k, v in LEAGUE_DRAW_PCT.items()
    ) / n
    high_draw_ratio = sum(
        league_counts.get(k, 0) for k in HIGH_DRAW_LEAGUES
    ) / n
    same_league_cluster = int(any(v >= 3 for v in league_counts.values()))

    return {
        "round_id"            : rnd.get("round_id", ""),
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
        feat["upset_rate_t1"] = (
            fm[i-1]["upset_rate"] if i >= 1 else feat["upset_rate"]
        )

        streak = 0
        j = i - 1
        while j >= 0 and draws[j] >= 5:
            streak += 1; j -= 1
        feat["draw_heavy_streak"] = streak

        streak = 0
        j = i - 1
        while j >= 0 and draws[j] <= 2:
            streak += 1; j -= 1
        feat["decisive_streak"] = streak

        feat["mirror_ge3_flag"]     = int(feat["mirror_count"] >= 3)
        feat["clear_fav_ge3_flag"]  = int(feat["clear_fav_count"] >= 3)
        feat["away_fav_ge5_flag"]   = int(feat["away_fav_count"] >= 5)
        feat["home_odds_weak_flag"] = int(feat["home_odds_weak_count"] >= 10)
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
        "mirror_count"        : mirror_count,
        "clear_fav_count"     : clear_fav_count,
        "away_fav_count"      : away_fav_count,
        "home_odds_weak"      : home_odds_weak,
        "strong_fav_trap"     : strong_fav_trap,
        "draw_pct_weighted"   : round(draw_pct_weighted, 3),
        "high_draw_ratio"     : round(high_draw_ratio, 3),
        "same_league_cluster" : int(any(v >= 3 for v in league_counts.values())),
        "mirror_ge3_flag"     : int(mirror_count >= 3),
        "clear_fav_ge3_flag"  : int(clear_fav_count >= 3),
        "away_fav_ge5_flag"   : int(away_fav_count >= 5),
        "home_odds_weak_flag" : int(home_odds_weak >= 10),
        "strong_fav_trap_flag": int(strong_fav_trap >= 2),
        "draws_t1"            : draws_history[-1] if len(draws_history) >= 1 else 3,
        "draws_t2"            : draws_history[-2] if len(draws_history) >= 2 else 3,
        "draws_t3"            : draws_history[-3] if len(draws_history) >= 3 else 3,
        "draw_heavy_streak"   : fm[-1].get("draw_heavy_streak", 0),
        "decisive_streak"     : fm[-1].get("decisive_streak", 0),
    }


# ================================================================
# 6. CHRONOS FORECAST
# ================================================================
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
        inputs           = context_tensor,
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
    return best


# ================================================================
# 7. TICKET GENERATION
# ================================================================
def score_match(match, base_rates):
    o1 = match["odds_1"]
    ox = match["odds_x"]
    o2 = match["odds_2"]

    raw   = {"1": 1/o1, "X": 1/ox, "2": 1/o2}
    total = sum(raw.values())
    impl  = {k: v/total for k,v in raw.items()}

    # Draw aversion correction
    league_key = normalise_league(match.get("league", ""))
    draw_corr  = get_draw_aversion(league_key)
    impl["X"]  = min(impl["X"] + draw_corr, 0.60)
    impl_total = sum(impl.values())
    impl       = {k: v/impl_total for k,v in impl.items()}

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

    # Mid-Week specific: home bias is real here (43-44% base rate)
    # Do NOT apply away boost — unlike Mega Jackpot, homes dominate
    # Apply mild away correction only when away_fav signals are strong
    if o2 < o1 and o2 < 2.20:   # clear away favourite
        scores["2"] += 1.5

    return scores


def clamp_counts(nd, nh, na, total=13):
    nd = max(1, min(int(round(nd)), total - 2))
    nh = max(1, min(int(round(nh)), total - nd - 1))
    na = total - nd - nh
    if na < 1:
        na = 1
        nh = total - nd - na
    return nd, nh, na


def allocate_ticket(card, target_counts, base_rates):
    """
    Greedy allocation respecting Chronos budget.
    Mid-Week order: Draws first, then Homes (dominant here),
    then Aways.
    """
    n        = len(card)
    scored   = [score_match(m, base_rates) for m in card]
    assigns  = [None] * n
    assigned = set()

    for outcome in ["X", "1", "2"]:   # Homes before Aways for Mid-Week
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


def regime_label(nd, total=13):
    if nd <= 2:   return "Decisive"
    elif nd <= 4: return "Balanced"
    else:         return "Draw-Heavy"


# ================================================================
# 8. PICK TYPE CLASSIFIER
# ================================================================
def classify_match(match):
    o1  = match["odds_1"]
    ox  = match["odds_x"]
    o2  = match["odds_2"]
    spread     = max(o1, ox, o2) - min(o1, ox, o2)
    fav_odds   = min(o1, o2)
    league_key = normalise_league(match.get("league",""))
    league_draw= LEAGUE_DRAW_PCT.get(league_key, 0.28)

    raw   = {"1": 1/o1, "X": 1/ox, "2": 1/o2}
    total = sum(raw.values())
    probs = [v/total for v in raw.values()]
    entropy = round(-sum(p * np.log(p) for p in probs if p > 0), 4)

    league_lower = match.get("league","").lower()
    if any(x in league_lower for x in ["uefa","champions league","europa league","conference league"]):
        pick_type = "European"
        reason    = "Cross-league — odds only (DC future)"
    elif "international" in league_lower:
        pick_type = "International"
        reason    = "National teams — odds only"
    elif spread < 0.25:
        pick_type = "Speculative"
        reason    = f"spread={round(spread,2)} — very tight"
    elif fav_odds < 1.60:
        pick_type = "Double Chance"
        reason    = f"fav={fav_odds} < 1.60 — jackpot trap"
    elif spread > 0.90 and 1.60 <= fav_odds <= 2.10:
        pick_type = "Banker"
        reason    = f"spread={round(spread,2)}, fav={fav_odds}"
    elif spread < 0.35 and league_draw >= 0.27:
        pick_type = "Draw"
        reason    = f"tight match, {league_key} draw {round(league_draw*100)}%"
    else:
        pick_type = "Double Chance"
        reason    = f"spread={round(spread,2)}"

    return {
        "pick_type"  : pick_type,
        "entropy"    : entropy,
        "spread"     : round(spread, 3),
        "reason"     : reason,
        "league_draw": round(league_draw * 100, 1),
        "league_key" : league_key,
    }


# ================================================================
# 9. PRINT HELPERS
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
          f"high_draw_ratio={card_feat['high_draw_ratio']}")


def print_pick_summary(classifications):
    from collections import Counter
    counts = Counter(c["pick_type"] for c in classifications)
    print(f"\n  PICK TYPE SUMMARY:")
    for pt in ["Banker","Draw","Double Chance","Speculative","European","International"]:
        n   = counts.get(pt, 0)
        bar = "X" * n
        print(f"    {pt:<14} {n:>2}  {bar}")
    specs = [c for c in classifications if c["pick_type"] == "Speculative"]
    if specs:
        print(f"\n  SPECULATIVE — consider double coverage:")
        for i, c in enumerate(classifications):
            if c["pick_type"] == "Speculative":
                print(f"    [{i+1}] {c['reason']}")


def print_ticket(ticket, card, classifications, label, counts):
    h = counts["1"]; d = counts["X"]; a = counts["2"]
    print(f"\n  [{label}]  H={h}  D={d}  A={a}  "
          f"Regime: {regime_label(d)}")
    print(f"  {'#':<4} {'Match':<38} {'Pred':<5} {'Type':<14} "
          f"{'Entr':>6}  League")
    print("  " + "-" * 76)
    for i in range(len(card)):
        c       = classifications[i]
        matchup = f"{card[i]['home']} vs {card[i]['away']}"
        league  = card[i].get("league", "")
        print(f"  {i+1:<4} {matchup:<38} {ticket[i]:<5} "
              f"{c['pick_type']:<14} {c['entropy']:>6.3f}  {league}")
    print(f"\n  Ticket: {' - '.join(ticket)}")


# ================================================================
# 10. MAIN
# ================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="small",
                        choices=["tiny","small","base","large"])
    parser.add_argument("--samples", type=int, default=500)
    args = parser.parse_args()

    print("=" * 76)
    print("  SPORTPESA MID-WEEK JACKPOT FORECAST — Chronos Edition")
    print(f"  Model: chronos-t5-{args.model}  |  Samples: {args.samples}")
    print("=" * 76)

    # --- Load ---
    rounds          = load_batches()
    card, card_name = load_card()

    # --- Base rates (raw from batch, no correction) ---
    all_results = [
        get_result(m)
        for r in rounds for m in r["matches"]
    ]
    total_m   = len(all_results)
    base_rates = {
        "1": all_results.count("1") / total_m,
        "X": all_results.count("X") / total_m,
        "2": all_results.count("2") / total_m,
    }
    print(f"\n  Base rates ({total_m} matches): "
          f"Home {round(base_rates['1']*100,1)}%  "
          f"Draw {round(base_rates['X']*100,1)}%  "
          f"Away {round(base_rates['2']*100,1)}%")

    # --- Features ---
    print(f"\n  [Stage 1] Feature engineering ...")
    fm = [compute_round_features(r) for r in rounds]
    fm = add_temporal_features(fm)
    print(f"  Features computed for {len(fm)} rounds")

    latest = fm[-1]
    print(f"\n  Latest round ({latest['round_id']}): "
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

    # --- Chronos ---
    print(f"\n  [Stage 2] Chronos forecast ...")
    pipeline = load_chronos(args.model)

    n_rounds        = len(fm)
    context_lengths = [5, min(10, n_rounds), n_rounds]
    context_lengths = sorted(set(context_lengths))  # deduplicate
    forecast        = run_all_forecasts(
        pipeline, fm, context_lengths, args.samples
    )

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

    tickets    = {}
    counts_out = {}

    for scenario, pct in scenarios.items():
        nd = forecast["total_draws"][pct]
        nh = forecast["total_homes"][pct]
        na = forecast["total_aways"][pct]
        nd, nh, na = clamp_counts(nd, nh, na, total=13)
        counts = {"1": nh, "X": nd, "2": na}
        counts_out[scenario] = counts
        ticket, scored = allocate_ticket(card, counts, base_rates)
        tickets[scenario] = ticket

    # --- Print ---
    print("\n" + "=" * 76)
    print("  PREDICTION TICKETS")
    print(f"  Card: {card_name}")
    print("=" * 76)

    for scenario, pct in scenarios.items():
        print_ticket(
            tickets[scenario], card, classifications,
            f"{scenario.upper()} ({pct})",
            counts_out[scenario]
        )

    # --- Save ---
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_name  = f"midweek_forecast_{timestamp}.json"
    out_path  = os.path.join(OUTPUT_DIR, out_name)

    output = {
        "generated_at" : timestamp,
        "card_file"    : card_name,
        "model"        : f"chronos-t5-{args.model}",
        "num_samples"  : args.samples,
        "num_games"    : 13,
        "base_rates"   : {k: round(v,4) for k,v in base_rates.items()},
        "total_rounds" : len(fm),
        "card_signals" : card_feat,
        "forecast"     : forecast,
        "tickets"      : {
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
                        "entropy"  : classifications[i]["entropy"],
                    }
                    for i in range(13)
                ],
            }
            for scenario, pct in scenarios.items()
        },
        "match_analysis": [
            {
                "order"          : i+1,
                "home"           : card[i]["home"],
                "away"           : card[i]["away"],
                "league"         : card[i]["league"],
                "country"        : card[i]["country"],
                "pick_type"      : classifications[i]["pick_type"],
                "entropy"        : classifications[i]["entropy"],
                "spread"         : classifications[i]["spread"],
                "reason"         : classifications[i]["reason"],
                "league_draw_pct": classifications[i]["league_draw"],
            }
            for i in range(13)
        ],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Saved: midweek/output/{out_name}")
    print("=" * 76)


if __name__ == "__main__":
    main()
