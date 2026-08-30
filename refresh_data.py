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
import math
import json
import time
import requests
from datetime import date, timedelta, datetime, timezone

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
SEASON = 2026
PREVIOUS_SEASON = 2025
RESULTS_SAMPLE = 6
LEAGUE_AVG_GOALS = 1.35
PREDICTIONS_LOG_FILE = "predictions_log.json"

HTML_FILES = [
    "predictor-app-prototipo.html",
    "pickstats-app/www/index.html",
]

# misma lista que ALTITUDE_TEAMS en el JS de la app -- mantenerlas iguales
# para que el contador de aciertos compare contra lo mismo que ve el usuario.
ALTITUDE_TEAMS = {
    "Millonarios": 2640, "Santa Fe": 2640, "Internacional de Bogota": 2640,
    "Deportivo Pasto": 2527,
    "LDU de Quito": 2850, "Universidad Catolica": 2850, "Independiente del Valle": 2600,
    "Cienciano": 3399, "Cusco": 3399, "Deportivo Garcilaso": 3399,
    "UTC Cajamarca": 2750, "FC Cajamarca": 2750,
}

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
    308: {"name": "Saudi Pro League", "homeAdvantage": 1.22},
    3:   {"name": "UEFA Europa League", "homeAdvantage": 1.15},
    848: {"name": "UEFA Conference League", "homeAdvantage": 1.15},
    169: {"name": "Super League (China)", "homeAdvantage": 1.22},
    98:  {"name": "J1 League (Japón)", "homeAdvantage": 1.18},
    235: {"name": "Premier League (Rusia)", "homeAdvantage": 1.20},
    241: {"name": "Copa Colombia", "homeAdvantage": 1.27},
    130: {"name": "Copa Argentina", "homeAdvantage": 1.28},
    267: {"name": "Copa Chile", "homeAdvantage": 1.27},
    503: {"name": "Copa Perú", "homeAdvantage": 1.28},
    917: {"name": "Copa Ecuador", "homeAdvantage": 1.30},
    501: {"name": "Copa Paraguay", "homeAdvantage": 1.27},
    930: {"name": "Copa Uruguay", "homeAdvantage": 1.26},
    73:  {"name": "Copa do Brasil", "homeAdvantage": 1.28},
}

DEFAULT_FORM = ["D", "D", "D", "D", "D"]

# --- caches globales (para no repetir llamadas si un equipo juega en más
#     de una competición, ej. liga local + Libertadores) ---
GOALS_CACHE = {}    # team_id -> (gf, gc, form)
EXTRA_CACHE = {}    # team_id -> {recentResults, cornersAvg, cardsAvg, lastMatchCorners, lastMatchCards}
PREDICTIONS_LOG = {}  # match_id (string) -> registro del historial de pronósticos
TOPSCORERS_CACHE = {}  # league_id -> {team_name: {name, goals, injured}}
STANDINGS_CACHE = {}  # league_id -> {team_name: {rank, points, played, ppg}}

# si el goleador principal de un equipo está lesionado, le bajamos un poco
# el ataque -- no es exacto (no sabemos qué % de los goles del equipo son
# de él), pero es una señal razonable y conservadora.
TOPSCORER_INJURY_PENALTY = 0.10

# qué tanto pesa la posición en la tabla sobre el ataque de un equipo,
# comparando sus puntos por partido contra el promedio de su liga.
# Se limita entre estos dos valores para que un líder aplastante o un
# colista no disparen el ajuste a un extremo poco realista.
STANDINGS_FACTOR_MIN = 0.80
STANDINGS_FACTOR_MAX = 1.25

# competiciones donde se enfrentan equipos de ligas muy distintas entre sí
# -- ahí nuestras estadísticas (goles, tabla) son menos comparables de un
# equipo a otro, así que le bajamos la confianza a la recomendación en vez
# de mostrar un porcentaje tan extremo como en un partido de liga local.
CUP_COMPETITIONS = {"Copa Libertadores", "Copa Sudamericana", "UEFA Europa League", "UEFA Conference League"}
CUP_UNCERTAINTY_SHRINK = 0.18  # 18% de la probabilidad se "jala" hacia un reparto parejo

# qué tan fuerte es cada liga sudamericana en competiciones continentales
# (Libertadores/Sudamericana), en relación a Colombia (1.00) como referencia.
# Son valores de criterio futbolístico general -- se pueden afinar más
# adelante con resultados reales de copa si Golyzer crece.
LEAGUE_STRENGTH = {
    "Brasileirão": 1.15,
    "Liga Profesional (Arg.)": 1.12,
    "Primera División (Uruguay)": 1.05,
    "LigaPro (Ecuador)": 1.03,
    "Liga BetPlay (Colombia)": 1.00,
    "Primera División (Chile)": 0.95,
    "Primera División (Paraguay)": 0.92,
    "Liga 1 (Perú)": 0.90,
}

TEAM_LEAGUE_CACHE = {}  # team_id -> nombre de su liga doméstica


def poisson_prob(k, lam):
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


# corrección de Dixon-Coles: el Poisson "puro" trata los goles del local y
# del visitante como independientes entre sí, y eso hace que subestime
# sistemáticamente los resultados cortos/empates (0-0, 1-0, 0-1, 1-1) --
# el fútbol real tiene algo de correlación negativa ahí (cuando un equipo
# se pone cauteloso, el otro también). rho=-0.13 es el valor típico de la
# literatura (Dixon & Coles, 1997) -- no lo hemos calibrado con datos
# propios todavía porque aún no tenemos suficiente historial evaluado.
DIXON_COLES_RHO = -0.13


def dixon_coles_tau(x, y, lam, mu, rho):
    if x == 0 and y == 0:
        adj = 1 - (lam * mu * rho)
    elif x == 0 and y == 1:
        adj = 1 + (lam * rho)
    elif x == 1 and y == 0:
        adj = 1 + (mu * rho)
    elif x == 1 and y == 1:
        adj = 1 - rho
    else:
        adj = 1.0
    return max(0.0, adj)


def altitude_bonus(home_name, away_name, is_cup=False):
    home_alt = ALTITUDE_TEAMS.get(home_name)
    if not home_alt:
        return 0
    away_alt = ALTITUDE_TEAMS.get(away_name, 0)
    if home_alt - away_alt < 1200:
        return 0
    base = 0.12 if home_alt >= 3000 else 0.07
    # en copa el visitante no tuvo tiempo de aclimatarse (es un viaje de un
    # solo partido, no una temporada completa) -- el golpe de la altura
    # suele ser más brutal ahí, así que le damos el doble de peso.
    return base * 2 if is_cup else base


def predict_pick(home_name, away_name, home_gf, home_gc, away_gf, away_gc, home_advantage,
                  home_top_scorer=None, away_top_scorer=None,
                  home_standing=None, away_standing=None, league_avg_ppg=None,
                  home_league_strength=None, away_league_strength=None,
                  is_cup=False, max_goals=8):
    """Misma lógica que matchProbabilities()/pickRecommendation() del JS,
    solo para local/empate/visita (lo que necesita el contador de aciertos)."""
    home_attack = home_gf / LEAGUE_AVG_GOALS
    home_defense = home_gc / LEAGUE_AVG_GOALS
    away_attack = away_gf / LEAGUE_AVG_GOALS
    away_defense = away_gc / LEAGUE_AVG_GOALS
    if home_top_scorer and home_top_scorer.get("injured"):
        home_attack *= (1 - TOPSCORER_INJURY_PENALTY)
    if away_top_scorer and away_top_scorer.get("injured"):
        away_attack *= (1 - TOPSCORER_INJURY_PENALTY)
    home_attack *= standings_attack_factor(home_standing, league_avg_ppg)
    away_attack *= standings_attack_factor(away_standing, league_avg_ppg)
    if is_cup:
        # en copas internacionales, corregimos la fuerza de ataque según
        # qué tan dura es la liga doméstica de cada equipo -- así un equipo
        # de una liga floja no queda sobrevalorado solo por venir en racha.
        home_attack *= (home_league_strength or 1.0)
        away_attack *= (away_league_strength or 1.0)
    effective_home_advantage = home_advantage + altitude_bonus(home_name, away_name, is_cup)

    lambda_home = home_attack * away_defense * LEAGUE_AVG_GOALS * effective_home_advantage
    lambda_away = away_attack * home_defense * LEAGUE_AVG_GOALS

    p_home = p_draw = p_away = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = poisson_prob(i, lambda_home) * poisson_prob(j, lambda_away)
            p *= dixon_coles_tau(i, j, lambda_home, lambda_away, DIXON_COLES_RHO)
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p

    total = p_home + p_draw + p_away
    probs = {"home": p_home / total, "draw": p_draw / total, "away": p_away / total}
    if is_cup:
        for k in probs:
            probs[k] = probs[k] * (1 - CUP_UNCERTAINTY_SHRINK) + (1 / 3) * CUP_UNCERTAINTY_SHRINK
    pick = max(probs, key=probs.get)
    return pick, probs[pick], lambda_home, lambda_away


# --- mercados de goles/córners/tarjetas (línea +/-), para el contador de
# aciertos separado que pidió Jaime. Réplica en Python de chooseLine() del
# JS, para poder registrar y luego evaluar estos pronósticos igual que el
# de local/empate/visitante.
def choose_line(expected_total):
    line = math.floor(expected_total - 0.5 + 0.5) + 0.5  # equivalente a Math.round en JS
    if line < 0.5:
        line = 0.5
    return line


def predict_goals_pick(lambda_home, lambda_away, max_goals=8):
    """Línea de goles +/- y el lado más probable, usando el mismo modelo
    (Poisson + Dixon-Coles) que ya usamos para local/empate/visitante --
    no cuesta llamadas extra a la API porque reutiliza lambda_home/away."""
    line = choose_line(lambda_home + lambda_away)
    threshold = math.floor(line) + 1
    p_total = p_over = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = poisson_prob(i, lambda_home) * poisson_prob(j, lambda_away)
            p *= dixon_coles_tau(i, j, lambda_home, lambda_away, DIXON_COLES_RHO)
            p_total += p
            if i + j >= threshold:
                p_over += p
    over_prob = p_over / p_total if p_total else 0.5
    pick = "over" if over_prob >= 0.5 else "under"
    prob = over_prob if pick == "over" else 1 - over_prob
    return line, pick, round(prob, 4)


def predict_line_pick(combined_avg, max_extra=20):
    """Línea +/- para córners o tarjetas, con un solo Poisson sobre el
    promedio combinado de ambos equipos (menos preciso que el de goles,
    igual que en el JS, pero sirve para medir aciertos de ese mercado)."""
    if combined_avg is None:
        return None, None, None
    line = choose_line(combined_avg)
    threshold = math.floor(line) + 1
    p_over = min(sum(poisson_prob(k, combined_avg) for k in range(threshold, threshold + max_extra)), 1.0)
    pick = "over" if p_over >= 0.5 else "under"
    prob = p_over if pick == "over" else 1 - p_over
    return line, pick, round(prob, 4)


def fetch_actual_stats(fixture_id):
    """Total de córners y tarjetas amarillas del partido (ambos equipos
    sumados), para poder evaluar si el pronóstico de esos mercados acertó.
    Ojo: esto SÍ es una llamada extra a la API, una por cada partido
    finalizado que tenía un pronóstico de córners/tarjetas pendiente --
    similar en costo a lo que ya gastamos trayendo resultados recientes."""
    try:
        data = get("/fixtures/statistics", {"fixture": fixture_id})
        total_corners = total_cards = 0.0
        any_corners = any_cards = False
        for team_block in data.get("response", []):
            for stat in team_block.get("statistics", []):
                if stat.get("type") == "Corner Kicks" and stat.get("value") is not None:
                    total_corners += float(stat["value"]); any_corners = True
                elif stat.get("type") == "Yellow Cards" and stat.get("value") is not None:
                    total_cards += float(stat["value"]); any_cards = True
        return (total_corners if any_corners else None, total_cards if any_cards else None)
    except Exception:
        return None, None


def actual_outcome(result):
    if result["home"] > result["away"]:
        return "home"
    if result["home"] < result["away"]:
        return "away"
    return "draw"


def load_predictions_log():
    global PREDICTIONS_LOG
    if os.path.exists(PREDICTIONS_LOG_FILE):
        try:
            with open(PREDICTIONS_LOG_FILE, encoding="utf-8") as f:
                records = json.load(f)
            PREDICTIONS_LOG = {str(r["id"]): r for r in records}
        except Exception as e:
            print(f"  AVISO: no se pudo leer {PREDICTIONS_LOG_FILE} ({e}), se empieza uno nuevo.")
            PREDICTIONS_LOG = {}
    else:
        PREDICTIONS_LOG = {}
    print(f"Historial de pronósticos cargado: {len(PREDICTIONS_LOG)} registros")


def save_predictions_log():
    records = list(PREDICTIONS_LOG.values())
    records.sort(key=lambda r: r["date"])
    with open(PREDICTIONS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    evaluated = [r for r in records if r["status"] in ("correct", "incorrect")]
    correct = [r for r in evaluated if r["status"] == "correct"]
    print(f"Historial de pronósticos guardado: {len(records)} registros totales, "
          f"{len(evaluated)} evaluados, {len(correct)} acertados"
          f"{f' ({round(100*len(correct)/len(evaluated))}%)' if evaluated else ''}")


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


def fetch_league_topscorers(league_id):
    """Goleador principal de cada equipo de la liga, en una sola llamada
    (mucho más barato que pedir por equipo). Cacheado por liga -- solo se
    pide una vez por corrida, sin importar cuántos partidos tenga la liga.
    Ojo: si la temporada acaba de arrancar (nadie ha anotado todavía),
    la lista viene vacía -- eso es normal, no un error."""
    if league_id in TOPSCORERS_CACHE:
        return TOPSCORERS_CACHE[league_id]

    by_team = {}
    try:
        data = get("/players/topscorers", {"league": league_id, "season": SEASON})
        for entry in data.get("response", []):
            player = entry.get("player", {})
            stats_list = entry.get("statistics") or []
            if not stats_list:
                continue
            stats = stats_list[0]
            team_name = (stats.get("team") or {}).get("name")
            goals = (stats.get("goals") or {}).get("total")
            if not team_name or goals is None:
                continue
            # la lista viene ordenada de más a menos goles -- la primera
            # vez que aparece un equipo, ese jugador es SU goleador principal
            if team_name not in by_team:
                by_team[team_name] = {
                    "name": player.get("name"),
                    "goals": goals,
                    "injured": bool(player.get("injured")),
                }
    except Exception as e:
        print(f"  AVISO: no se pudieron traer goleadores de la liga {league_id} ({e})")

    TOPSCORERS_CACHE[league_id] = by_team
    return by_team


def fetch_league_standings(league_id):
    """Tabla de posiciones de la liga (posición, puntos, partidos jugados),
    en una sola llamada. Cacheada por liga. Algunas ligas (ej. Colombia)
    tienen la tabla dividida en grupos (Apertura/Clausura) -- los juntamos
    todos en una sola lista."""
    if league_id in STANDINGS_CACHE:
        return STANDINGS_CACHE[league_id]

    by_team = {}
    try:
        data = get("/standings", {"league": league_id, "season": SEASON})
        resp = data.get("response") or []
        groups = (resp[0]["league"]["standings"]) if resp else []
        for group in groups:
            for row in group:
                team_name = (row.get("team") or {}).get("name")
                played = (row.get("all") or {}).get("played")
                points = row.get("points")
                if not team_name or not played:
                    continue

                # promedios de goles separados por local/visita -- un equipo
                # casi siempre rinde distinto según dónde juega, así que esto
                # afina el ataque/defensa más que el promedio general mezclado.
                home_split = row.get("home") or {}
                away_split = row.get("away") or {}
                home_played = home_split.get("played") or 0
                away_played = away_split.get("played") or 0
                home_goals = home_split.get("goals") or {}
                away_goals = away_split.get("goals") or {}

                by_team[team_name] = {
                    "rank": row.get("rank"),
                    "points": points,
                    "played": played,
                    "ppg": round(points / played, 2),
                    "homeGf": round(home_goals.get("for", 0) / home_played, 2) if home_played else None,
                    "homeGc": round(home_goals.get("against", 0) / home_played, 2) if home_played else None,
                    "awayGf": round(away_goals.get("for", 0) / away_played, 2) if away_played else None,
                    "awayGc": round(away_goals.get("against", 0) / away_played, 2) if away_played else None,
                }
    except Exception as e:
        print(f"  AVISO: no se pudo traer la tabla de posiciones de la liga {league_id} ({e})")

    STANDINGS_CACHE[league_id] = by_team
    return by_team


def standings_attack_factor(team_standing, league_avg_ppg):
    """Cuánto ajustar el ataque de un equipo según su posición en la tabla,
    comparado contra el promedio de puntos por partido de su liga."""
    if not team_standing or not league_avg_ppg:
        return 1.0
    factor = team_standing["ppg"] / league_avg_ppg
    return max(STANDINGS_FACTOR_MIN, min(STANDINGS_FACTOR_MAX, factor))


# qué tanto pesa el promedio específico de local/visita frente al promedio
# general del equipo (mezclado). No lo reemplazamos del todo porque al
# inicio de temporada un equipo puede tener muy pocos partidos jugados en
# ese rol específico -- una mezcla es más estable que solo el dato parcial.
HOME_AWAY_SPLIT_WEIGHT = 0.6


def apply_home_away_split(gf, gc, standing, side):
    """Ajusta el gf/gc general del equipo con su promedio específico de
    local o visita (según corresponda), si lo tenemos disponible."""
    if not standing:
        return gf, gc
    split_gf = standing.get(f"{side}Gf")
    split_gc = standing.get(f"{side}Gc")
    if split_gf is None or split_gc is None:
        return gf, gc
    w = HOME_AWAY_SPLIT_WEIGHT
    return (gf * (1 - w) + split_gf * w), (gc * (1 - w) + split_gc * w)


def team_league_strength(team_id):
    """Peso de la liga doméstica del equipo (1.0 si no la conocemos)."""
    league_name = TEAM_LEAGUE_CACHE.get(team_id)
    return LEAGUE_STRENGTH.get(league_name, 1.0)


def build_team_object(name, team_id, goals_data, avg_gf, avg_gc, topscorers_by_team=None, standings_by_team=None, side=None):
    gf, gc, form = goals_data if goals_data else (avg_gf, avg_gc, DEFAULT_FORM)
    standing = standings_by_team.get(name) if standings_by_team else None
    if side:
        gf, gc = apply_home_away_split(gf, gc, standing, side)
    extra = fetch_team_extra(team_id)
    obj = {"name": name, "gf": round(gf, 2), "gc": round(gc, 2), "form": form, "leagueStrength": team_league_strength(team_id)}
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
    if topscorers_by_team and name in topscorers_by_team:
        obj["topScorer"] = topscorers_by_team[name]
    if standings_by_team and name in standings_by_team:
        obj["standing"] = standings_by_team[name]
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

        # registramos la liga doméstica de cada equipo que aparece aquí (si
        # ya la teníamos de antes -p.ej. de su liga local- no la pisamos).
        for fx in fixtures:
            TEAM_LEAGUE_CACHE.setdefault(fx["teams"]["home"]["id"], info["name"])
            TEAM_LEAGUE_CACHE.setdefault(fx["teams"]["away"]["id"], info["name"])

        finished_fx = [fx for fx in fixtures if fx["fixture"]["status"]["short"] == "FT"]
        upcoming_fx = [fx for fx in fixtures if fx["fixture"]["status"]["short"] == "NS"]
        print(f"  {len(finished_fx)} finalizados, {len(upcoming_fx)} próximos")

        topscorers_by_team = fetch_league_topscorers(league_id) if upcoming_fx else {}
        standings_by_team = fetch_league_standings(league_id) if upcoming_fx else {}
        league_avg_ppg = (
            round(sum(s["ppg"] for s in standings_by_team.values()) / len(standings_by_team), 2)
            if standings_by_team else None
        )
        is_cup = info["name"] in CUP_COMPETITIONS

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
            result = {"home": fx["goals"]["home"], "away": fx["goals"]["away"]}
            all_matches.append({
                "id": fx["fixture"]["id"], "league": info["name"], "date": fx["fixture"]["date"],
                "finished": True, "result": result,
                "home": {"name": fx["teams"]["home"]["name"], "gf": 1.35, "gc": 1.35, "form": DEFAULT_FORM},
                "away": {"name": fx["teams"]["away"]["name"], "gf": 1.35, "gc": 1.35, "form": DEFAULT_FORM},
                "homeAdvantage": info["homeAdvantage"],
            })

            # si habíamos registrado un pronóstico para este partido antes de
            # que se jugara, lo cerramos comparando contra el resultado real
            log_id = str(fx["fixture"]["id"])
            entry = PREDICTIONS_LOG.get(log_id)
            if entry and entry.get("status") == "pending":
                real = actual_outcome(result)
                entry["actualResult"] = result
                entry["status"] = "correct" if real == entry["predictedPick"] else "incorrect"

                # evaluar el mercado de goles -- no cuesta llamadas extra,
                # ya tenemos el marcador final.
                if entry.get("goalsPick"):
                    actual_total = result["home"] + result["away"]
                    threshold = math.floor(entry["goalsLine"]) + 1
                    actual_side = "over" if actual_total >= threshold else "under"
                    entry["goalsStatus"] = "correct" if actual_side == entry["goalsPick"] else "incorrect"

                # evaluar córners/tarjetas -- esto sí implica una llamada
                # extra a la API por partido (para saber el total real de
                # cada uno), así que solo se hace si de verdad habíamos
                # registrado un pronóstico de esos mercados.
                if entry.get("cornersPick") or entry.get("cardsPick"):
                    actual_corners, actual_cards = fetch_actual_stats(fx["fixture"]["id"])
                    if entry.get("cornersPick") and actual_corners is not None:
                        threshold = math.floor(entry["cornersLine"]) + 1
                        actual_side = "over" if actual_corners >= threshold else "under"
                        entry["cornersStatus"] = "correct" if actual_side == entry["cornersPick"] else "incorrect"
                    if entry.get("cardsPick") and actual_cards is not None:
                        threshold = math.floor(entry["cardsLine"]) + 1
                        actual_side = "over" if actual_cards >= threshold else "under"
                        entry["cardsStatus"] = "correct" if actual_side == entry["cardsPick"] else "incorrect"
                    time.sleep(0.08)

        for fx in upcoming_fx:
            home_id, away_id = fx["teams"]["home"]["id"], fx["teams"]["away"]["id"]
            home_obj = build_team_object(fx["teams"]["home"]["name"], home_id, GOALS_CACHE.get(home_id), avg_gf, avg_gc, topscorers_by_team, standings_by_team, side="home")
            away_obj = build_team_object(fx["teams"]["away"]["name"], away_id, GOALS_CACHE.get(away_id), avg_gf, avg_gc, topscorers_by_team, standings_by_team, side="away")
            match_obj = {
                "id": fx["fixture"]["id"], "league": info["name"], "date": fx["fixture"]["date"],
                "home": home_obj, "away": away_obj,
                "homeAdvantage": info["homeAdvantage"],
            }
            if league_avg_ppg is not None:
                match_obj["leagueAvgPpg"] = league_avg_ppg
            all_matches.append(match_obj)
            print(f"    [próximo] {home_obj['name']} vs {away_obj['name']}")

            # registrar (o refrescar) el pronóstico de este partido mientras
            # todavía no se ha jugado -- se congela la última versión antes
            # del pitazo inicial, para comparar honestamente después.
            pick, prob, lambda_home, lambda_away = predict_pick(
                home_obj["name"], away_obj["name"],
                home_obj["gf"], home_obj["gc"], away_obj["gf"], away_obj["gc"],
                info["homeAdvantage"],
                home_top_scorer=home_obj.get("topScorer"),
                away_top_scorer=away_obj.get("topScorer"),
                home_standing=home_obj.get("standing"),
                away_standing=away_obj.get("standing"),
                league_avg_ppg=league_avg_ppg,
                home_league_strength=home_obj.get("leagueStrength"),
                away_league_strength=away_obj.get("leagueStrength"),
                is_cup=is_cup,
            )
            goals_line, goals_pick, goals_prob = predict_goals_pick(lambda_home, lambda_away)

            corners_avg = (
                home_obj["cornersAvg"] + away_obj["cornersAvg"]
                if home_obj.get("cornersAvg") is not None and away_obj.get("cornersAvg") is not None
                else None
            )
            cards_avg = (
                home_obj["cardsAvg"] + away_obj["cardsAvg"]
                if home_obj.get("cardsAvg") is not None and away_obj.get("cardsAvg") is not None
                else None
            )
            corners_line, corners_pick, corners_prob = predict_line_pick(corners_avg)
            cards_line, cards_pick, cards_prob = predict_line_pick(cards_avg)

            log_entry = {
                "id": fx["fixture"]["id"], "league": info["name"], "date": fx["fixture"]["date"],
                "home": home_obj["name"], "away": away_obj["name"],
                "predictedPick": pick, "predictedProb": round(prob, 4),
                "status": "pending", "actualResult": None,
                "goalsLine": goals_line, "goalsPick": goals_pick, "goalsProb": goals_prob,
                "cornersLine": corners_line, "cornersPick": corners_pick, "cornersProb": corners_prob,
                "cardsLine": cards_line, "cardsPick": cards_pick, "cardsProb": cards_prob,
            }
            # si ya existía un registro pendiente de este partido (de una
            # corrida anterior) y ya estaba evaluado por alguna razón, no lo
            # pisamos con uno nuevo en blanco.
            existing = PREDICTIONS_LOG.get(str(fx["fixture"]["id"]))
            if existing and existing.get("status") != "pending":
                continue
            PREDICTIONS_LOG[str(fx["fixture"]["id"])] = log_entry

        time.sleep(0.15)

    print(f"\n=== Resumen de datos de goles ===")
    print(f"  Temporada actual: {tier_counts['temporada_actual']}")
    print(f"  Temporada anterior: {tier_counts['temporada_anterior']}")
    print(f"  Promedio de liga: {tier_counts['promedio_liga']}")
    if warnings:
        print(f"\n{len(warnings)} avisos (primeros 10):")
        for w in warnings[:10]:
            print(f"  - {w}")

    mark_daily_picks()

    return all_matches


# Colombia no usa horario de verano, así que el offset fijo -5 es válido
# todo el año. La mayoría de la audiencia de Golyzer está ahí (según
# Analytics), así que "hoy" para la sección "Pronóstico del día" se define
# con esa zona horaria, igual que isToday() hace en el navegador con la
# hora local del dispositivo (que para la mayoría será, de hecho, Colombia).
COLOMBIA_TZ = timezone(timedelta(hours=-5))


def mark_daily_picks():
    """Marca, entre los partidos de HOY (pendientes, hora Colombia), cuál es
    el pick de resultado y cuál el de goles con mayor confianza -- de forma
    permanente en el historial. Así se puede medir después, honestamente,
    qué tan bien le va específicamente a los picks que destacamos en la
    sección "Pronóstico del día" de la app, no solo al modelo en general.
    Solo toca los registros de hoy; los de días anteriores ya quedaron
    fijados por la corrida de su propio día y no se tocan de nuevo."""
    today_co = datetime.now(COLOMBIA_TZ).date()
    todays_entries = [
        r for r in PREDICTIONS_LOG.values()
        if r.get("status") == "pending"
        and datetime.fromisoformat(r["date"]).astimezone(COLOMBIA_TZ).date() == today_co
    ]
    for r in todays_entries:
        r["dailyPickResult"] = False
        r["dailyPickGoals"] = False

    if not todays_entries:
        return

    best_result = max(todays_entries, key=lambda r: r["predictedProb"])
    best_result["dailyPickResult"] = True

    goals_candidates = [r for r in todays_entries if r.get("goalsProb") is not None]
    if goals_candidates:
        best_goals = max(goals_candidates, key=lambda r: r["goalsProb"])
        best_goals["dailyPickGoals"] = True

    print(f"\nPronóstico del día ({today_co}):")
    print(f"  Resultado: {best_result['home']} vs {best_result['away']} -> {best_result['predictedPick']} ({round(best_result['predictedProb']*100)}%)")
    if goals_candidates:
        print(f"  Goles: {best_goals['home']} vs {best_goals['away']} -> {best_goals['goalsPick']} {best_goals['goalsLine']} ({round(best_goals['goalsProb']*100)}%)")


def update_html_files(matches):
    matches_block = json.dumps(matches, ensure_ascii=False, indent=2)
    matches_pattern = re.compile(r"const matches = (\[.*?\n\]);\n", re.S)

    log_records = list(PREDICTIONS_LOG.values())
    log_records.sort(key=lambda r: r["date"])
    log_block = json.dumps(log_records, ensure_ascii=False, indent=2)
    log_pattern = re.compile(r"const predictionsLog = (\[.*?\]);\n", re.S)

    for path in HTML_FILES:
        if not os.path.exists(path):
            print(f"  AVISO: no se encontró {path}, se omite.")
            continue
        html = open(path, encoding="utf-8").read()

        m = matches_pattern.search(html)
        if not m:
            print(f"  ERROR: no se encontró el bloque de partidos en {path}, no se modifica.")
            continue
        html = html[:m.start(1)] + matches_block + html[m.end(1):]

        m2 = log_pattern.search(html)
        if not m2:
            print(f"  ERROR: no se encontró el bloque del historial en {path}, no se actualiza esa parte.")
        else:
            html = html[:m2.start(1)] + log_block + html[m2.end(1):]

        open(path, "w", encoding="utf-8").write(html)
        print(f"  Actualizado: {path} ({len(matches)} partidos, {len(log_records)} en el historial)")


def main():
    if not API_KEY:
        raise SystemExit("Falta la variable de entorno API_FOOTBALL_KEY.")
    print(f"Iniciando actualización — {date.today().isoformat()}")
    load_predictions_log()
    matches = fetch_all_matches()
    print(f"\nTotal de partidos: {len(matches)}")
    update_html_files(matches)
    save_predictions_log()
    print("\n=== LISTO ===")


if __name__ == "__main__":
    main()
