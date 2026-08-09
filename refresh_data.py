"""
Golyzer — script único de actualización de datos.

Este script reemplaza a paso3, paso5 y paso6: junta todo en una sola
corrida (las 20 competiciones, goles/forma con respaldo de 3 niveles,
últimos resultados, córners y tarjetas) y actualiza directamente los
archivos HTML de la app -- no genera un .json aparte para subir a mano.

Pensado para correr automáticamente (GitHub Actions), pero también
puedes correrlo tú mismo en tu Mac si alguna vez quieres una
actualización inmediata:
  python3 refresh_data.py

Variables de entorno esperadas:
  API_FOOTBALL_KEY -- tu API key de api-football.com

Archivos que actualiza (deben existir en la misma carpeta / repo):
  predictor-app-prototipo.html
  pickstats-app/www/index.html

Nota sobre tiempo: corre bastante más lento que los scripts anteriores
porque junta todo en un solo paso (puede tardar 20-40 minutos). Es
normal, está pensado para correr solo, sin que nadie lo esté mirando.
"""

import os
import re
import json
import time
import requests
from datetime import date, timedelta

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
SEASON = 2026
PREVIOUS_SEASON = 2025
RESULTS_SAMPLE = 6

HTML_FILES = [
    "predictor-app-prototipo.html",
    "pickstats-app/www/index.html",
]

LEAGUES = {
    140: {"name": "LaLiga", "homeAdvantage": 1.25},
    39:  {"name": "Premier League", "homeAdvantage": 1.20},
    135: {"name": "Serie A", "homeAdvantage": 1.20},
    78:  {"name": "Bundesliga", "homeAdvantage": 1.20},
    61:  {"name": "Ligue 1", "homeAdvantage": 1.20},
    262: {"name": "Liga MX", "homeAdvantage": 1.30},
    71:  {"name": "Brasileirão", "homeAdvantage": 1.28},
    128: {"name": "Liga Profesional (Arg.)", "homeAdvantage": 1.28},
    239: {"name": "Liga BetPlay (Colombia)", "homeAdvantage": 1.27},
    253: {"name": "MLS", "homeAdvantage": 1.22},
    88:  {"name": "Eredivisie", "homeAdvantage": 1.19},
    2:   {"name": "Champions League", "homeAdvantage": 1.15},
    265: {"name": "Primera División (Chile)", "homeAdvantage": 1.27},
    281: {"name": "Liga 1 (Perú)", "homeAdvantage": 1.28},
    242: {"name": "LigaPro (Ecuador)", "homeAdvantage": 1.30},
    252: {"name": "Primera División (Paraguay)", "homeAdvantage": 1.27},
    268: {"name": "Primera División (Uruguay)", "homeAdvantage": 1.26},
    94:  {"name": "Primeira Liga (Portugal)", "homeAdvantage": 1.20},
    13:  {"name": "Copa Libertadores", "homeAdvantage": 1.24},
    11:  {"name": "Copa Sudamericana", "homeAdvantage": 1.22},
}

DEFAULT_FORM = ["D", "D", "D", "D", "D"]

# --- caches globales (para no repetir llamadas si un equipo juega en más
#     de una competición, ej. liga local + Libertadores) ---
GOALS_CACHE = {}    # team_id -> (gf, gc, form)
EXTRA_CACHE = {}    # team_id -> {recentResults, cornersAvg, cardsAvg, lastMatchCorners, lastMatchCards}


def get(path, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(BASE + path, headers=HEADERS, params=params or {}, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1.5)


def clean_form(form_str):
    if not form_str:
        return None
    letters = [c for c in str(form_str).upper() if c in ("W", "D", "L")]
    if len(letters) < 3:
        return None
    letters = letters[-5:]
    while len(letters) < 5:
        letters.insert(0, "D")
    return letters


def safe_float(x):
    try:
        v = float(x)
        return round(v, 2) if v > 0 else None
    except (TypeError, ValueError):
        return None


def tier1_from_predictions(team_pred):
    last5 = (team_pred.get("last_5") or {})
    goals = (last5.get("goals") or {})
    gf = safe_float((goals.get("for") or {}).get("average"))
    gc = safe_float((goals.get("against") or {}).get("average"))
    form = clean_form((team_pred.get("league") or {}).get("form"))
    if gf and gc and form:
        return gf, gc, form
    return None


def tier2_from_last_season(team_id, league_id):
    try:
        data = get("/teams/statistics", {"team": team_id, "league": league_id, "season": PREVIOUS_SEASON})
        resp = data.get("response") or {}
        goals_for = ((resp.get("goals") or {}).get("for") or {}).get("average") or {}
        goals_against = ((resp.get("goals") or {}).get("against") or {}).get("average") or {}
        gf = safe_float(goals_for.get("total"))
        gc = safe_float(goals_against.get("total"))
        form = clean_form(resp.get("form"))
        if gf and gc:
            return gf, gc, (form or DEFAULT_FORM)
    except Exception:
        pass
    return None


def fetch_team_extra(team_id):
    """Últimos resultados + córners/tarjetas (promedio y último partido). Cacheado globalmente."""
    if team_id in EXTRA_CACHE:
        return EXTRA_CACHE[team_id]

    result = {
        "recentResults": [], "cornersAvg": None, "cardsAvg": None,
        "lastMatchCorners": None, "lastMatchCards": None,
    }
    try:
        data = get("/fixtures", {"team": team_id, "last": RESULTS_SAMPLE})
        fixtures = data.get("response", [])
    except Exception:
        EXTRA_CACHE[team_id] = result
        return result

    fixtures.sort(key=lambda fx: fx["fixture"]["date"], reverse=True)
    corners_values, cards_values = [], []

    for idx, fx in enumerate(fixtures):
        is_home = fx["teams"]["home"]["id"] == team_id
        opponent = fx["teams"]["away"]["name"] if is_home else fx["teams"]["home"]["name"]
        gf = fx["goals"]["home"] if is_home else fx["goals"]["away"]
        ga = fx["goals"]["away"] if is_home else fx["goals"]["home"]
        if gf is None or ga is None:
            continue
        r = "W" if gf > ga else "L" if gf < ga else "D"
        if len(result["recentResults"]) < 5:
            result["recentResults"].append({
                "opponent": opponent, "for": gf, "against": ga, "home": is_home, "result": r,
            })

        corners_val, cards_val = None, None
        try:
            stats = get("/fixtures/statistics", {"fixture": fx["fixture"]["id"]})
            for team_block in stats.get("response", []):
                if team_block["team"]["id"] != team_id:
                    continue
                for stat in team_block.get("statistics", []):
                    if stat.get("type") == "Corner Kicks":
                        corners_val = safe_float(stat.get("value"))
                    elif stat.get("type") == "Yellow Cards":
                        cards_val = safe_float(stat.get("value"))
        except Exception:
            pass
        time.sleep(0.08)

        if corners_val is not None:
            corners_values.append(corners_val)
        if cards_val is not None:
            cards_values.append(cards_val)
        if idx == 0:
            result["lastMatchCorners"] = corners_val
            result["lastMatchCards"] = cards_val

    if corners_values:
        result["cornersAvg"] = round(sum(corners_values) / len(corners_values), 2)
    if cards_values:
        result["cardsAvg"] = round(sum(cards_values) / len(cards_values), 2)

    EXTRA_CACHE[team_id] = result
    return result


def build_team_object(name, team_id, goals_data, avg_gf, avg_gc):
    gf, gc, form = goals_data if goals_data else (avg_gf, avg_gc, DEFAULT_FORM)
    extra = fetch_team_extra(team_id)
    obj = {"name": name, "gf": gf, "gc": gc, "form": form}
    if extra["recentResults"]:
        obj["recentResults"] = extra["recentResults"]
    if extra["cornersAvg"] is not None:
        obj["cornersAvg"] = extra["cornersAvg"]
    if extra["cardsAvg"] is not None:
        obj["cardsAvg"] = extra["cardsAvg"]
    if extra["lastMatchCorners"] is not None:
        obj["lastMatchCorners"] = extra["lastMatchCorners"]
    if extra["lastMatchCards"] is not None:
        obj["lastMatchCards"] = extra["lastMatchCards"]
    return obj


def fetch_all_matches():
    today = date.today()
    frm = (today - timedelta(days=5)).isoformat()
    to = (today + timedelta(days=25)).isoformat()

    all_matches = []
    tier_counts = {"temporada_actual": 0, "temporada_anterior": 0, "promedio_liga": 0}
    warnings = []

    for league_id, info in LEAGUES.items():
        print(f"\n--- {info['name']} (id={league_id}) ---")
        try:
            data = get("/fixtures", {"league": league_id, "season": SEASON, "from": frm, "to": to})
        except Exception as e:
            print(f"  ERROR trayendo el calendario: {e}")
            warnings.append(f"{info['name']}: no se pudo traer el calendario ({e})")
            continue

        fixtures = data.get("response", [])
        finished_fx = [fx for fx in fixtures if fx["fixture"]["status"]["short"] == "FT"]
        upcoming_fx = [fx for fx in fixtures if fx["fixture"]["status"]["short"] == "NS"]
        print(f"  {len(finished_fx)} finalizados, {len(upcoming_fx)} próximos")

        league_team_cache = {}  # team_id -> (gf, gc, form), solo para promedio de ESTA liga
        pending = []

        for fx in upcoming_fx:
            home_id = fx["teams"]["home"]["id"]
            away_id = fx["teams"]["away"]["id"]
            need_predictions = home_id not in GOALS_CACHE or away_id not in GOALS_CACHE

            p = None
            if need_predictions:
                try:
                    pred = get("/predictions", {"fixture": fx["fixture"]["id"]})
                    p = pred["response"][0]
                except Exception as e:
                    warnings.append(f"{info['name']}: sin predictions para fixture {fx['fixture']['id']} ({e})")
                time.sleep(0.08)

            for side in ("home", "away"):
                team_id = fx["teams"][side]["id"]
                if team_id in GOALS_CACHE:
                    league_team_cache[team_id] = GOALS_CACHE[team_id]
                    continue

                result = tier1_from_predictions(p["teams"][side]) if p else None
                if result:
                    tier_counts["temporada_actual"] += 1
                else:
                    result = tier2_from_last_season(team_id, league_id)
                    if result:
                        tier_counts["temporada_anterior"] += 1
                    time.sleep(0.08)

                if result:
                    GOALS_CACHE[team_id] = result
                    league_team_cache[team_id] = result
                else:
                    pending.append(team_id)

        if league_team_cache:
            avg_gf = round(sum(v[0] for v in league_team_cache.values()) / len(league_team_cache), 2)
            avg_gc = round(sum(v[1] for v in league_team_cache.values()) / len(league_team_cache), 2)
        else:
            avg_gf, avg_gc = 1.35, 1.35

        for team_id in pending:
            GOALS_CACHE[team_id] = (avg_gf, avg_gc, DEFAULT_FORM)
            tier_counts["promedio_liga"] += 1

        for fx in finished_fx:
            all_matches.append({
                "id": fx["fixture"]["id"], "league": info["name"], "date": fx["fixture"]["date"],
                "finished": True, "result": {"home": fx["goals"]["home"], "away": fx["goals"]["away"]},
                "home": {"name": fx["teams"]["home"]["name"], "gf": 1.35, "gc": 1.35, "form": DEFAULT_FORM},
                "away": {"name": fx["teams"]["away"]["name"], "gf": 1.35, "gc": 1.35, "form": DEFAULT_FORM},
                "homeAdvantage": info["homeAdvantage"],
            })

        for fx in upcoming_fx:
            home_id, away_id = fx["teams"]["home"]["id"], fx["teams"]["away"]["id"]
            home_obj = build_team_object(fx["teams"]["home"]["name"], home_id, GOALS_CACHE.get(home_id), avg_gf, avg_gc)
            away_obj = build_team_object(fx["teams"]["away"]["name"], away_id, GOALS_CACHE.get(away_id), avg_gf, avg_gc)
            all_matches.append({
                "id": fx["fixture"]["id"], "league": info["name"], "date": fx["fixture"]["date"],
                "home": home_obj, "away": away_obj,
                "homeAdvantage": info["homeAdvantage"],
            })
            print(f"    [próximo] {home_obj['name']} vs {away_obj['name']}")

        time.sleep(0.15)

    print(f"\n=== Resumen de datos de goles ===")
    print(f"  Temporada actual: {tier_counts['temporada_actual']}")
    print(f"  Temporada anterior: {tier_counts['temporada_anterior']}")
    print(f"  Promedio de liga: {tier_counts['promedio_liga']}")
    if warnings:
        print(f"\n{len(warnings)} avisos (primeros 10):")
        for w in warnings[:10]:
            print(f"  - {w}")

    return all_matches


def update_html_files(matches):
    block = json.dumps(matches, ensure_ascii=False, indent=2)
    pattern = re.compile(r"const matches = (\[.*?\n\]);\n", re.S)

    for path in HTML_FILES:
        if not os.path.exists(path):
            print(f"  AVISO: no se encontró {path}, se omite.")
            continue
        html = open(path, encoding="utf-8").read()
        m = pattern.search(html)
        if not m:
            print(f"  ERROR: no se encontró el bloque de partidos en {path}, no se modifica.")
            continue
        new_html = html[:m.start(1)] + block + html[m.end(1):]
        open(path, "w", encoding="utf-8").write(new_html)
        print(f"  Actualizado: {path} ({len(matches)} partidos)")


def main():
    if not API_KEY:
        raise SystemExit("Falta la variable de entorno API_FOOTBALL_KEY.")
    print(f"Iniciando actualización — {date.today().isoformat()}")
    matches = fetch_all_matches()
    print(f"\nTotal de partidos: {len(matches)}")
    update_html_files(matches)
    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()
