"""
Les chiffres de la page « Climat et cultures », calculés à partir de la
météo réelle de la ferme (weather_daily, Open-Meteo). Lecture seule.

Pour un mois et une ferme :
  - les 9 paramètres : moyennes (températures, humidité, vent, rayonnement)
    et cumuls (pluie, ET₀) ;
  - les jours secs (pluie < 1 mm) et la plus longue suite de jours secs
    (indice CDD de l'OMM) ;
  - la NORMALE du mois : la moyenne du même mois sur 2020-2025 ;
  - l'ÉCART à la normale : une différence (°C, points d'humidité, km/h,
    jours) ou un pourcentage (pluie, ET₀, rayonnement).

Le mois en cours est calculé sur ses jours déjà passés, et signalé comme
partiel : son écart à la normale n'a de sens qu'en fin de mois pour les
cumuls (pluie, ET₀, jours secs).
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from statistics import mean

from sqlalchemy import text
from sqlalchemy.orm import Session

import logging
import time

from app.core import conditions_config as cfg
from app.services import weather_history_service as weather

#: Les paramètres du mois, et comment comparer chacun à sa normale :
#: « diff » = différence (même unité), « pct » = pourcentage
PARAMETERS: dict[str, str] = {
    "temperature_max_c": "diff",
    "temperature_min_c": "diff",
    "temperature_mean_c": "diff",
    "precipitation_mm": "pct",
    "humidity_mean_pct": "diff",
    "wind_speed_mean_kmh": "diff",
    "radiation_mj_m2": "pct",
    "et0_mm": "pct",
    "dry_days": "diff",
}

#: Paramètres cumulés sur le mois (les autres sont des moyennes par jour)
CUMULATIVE = {"precipitation_mm", "et0_mm", "dry_days"}


logger = logging.getLogger(__name__)

#: Au-delà de ce nombre de jours manquants, l'historique est laissé à la
#: tâche d'arrière-plan (plusieurs demandes) au lieu d'être fait à la demande
MAX_ON_DEMAND_DAYS = 400

#: Après un échec, Open-Meteo n'est pas redemandé pour cette ferme avant ce délai
RETRY_AFTER_S = 600

_last_attempt: dict[int, float] = {}


# ======================================================================
# Météo à jour, à la demande
# ======================================================================


def last_recorded_day(db: Session, farm_id: int) -> date | None:
    """Le dernier jour de météo enregistré pour la ferme."""
    return db.execute(text("SELECT MAX(day) FROM weather_daily WHERE farm_id = :f"), {"f": farm_id}).scalar()


def ensure_weather_up_to_date(db: Session, farm_id: int, today: date | None = None) -> date | None:
    """
    À l'ouverture de la page : complète TOUT DE SUITE la météo de la ferme
    jusqu'à hier, si des jours manquent, sans attendre la tâche du matin.
    Une seule tentative, sans attente : si Open-Meteo ne répond pas, la page
    s'affiche avec les jours déjà enregistrés, et Open-Meteo n'est pas
    redemandé avant RETRY_AFTER_S. Retourne le dernier jour enregistré.
    """
    yesterday = (today or date.today()) - timedelta(days=1)
    last = last_recorded_day(db, farm_id)
    if last is not None and last >= yesterday:
        return last
    if time.monotonic() - _last_attempt.get(farm_id, -RETRY_AFTER_S) < RETRY_AFTER_S:
        return last
    _last_attempt[farm_id] = time.monotonic()

    if last is None or (yesterday - last).days > MAX_ON_DEMAND_DAYS:
        # Historique complet (depuis 2020) : plusieurs demandes, en arrière-plan
        try:
            from app.tasks.weather_tasks import launch_farm_history

            launch_farm_history(farm_id, countdown=0)
        except Exception:  # noqa: BLE001
            logger.exception("🌦️ [météo] Ferme %s : historique non programmé", farm_id)
        return last

    farms = weather.farm_coordinates(db, farm_id)
    if not farms:
        return last
    try:
        written = weather.sync_farm(db, farms[0], last + timedelta(days=1), yesterday, attempts=1)
        logger.info("🌦️ [météo] Ferme %s : %d jour(s) ajoutés à l'ouverture de la page", farm_id, written)
        _last_attempt.pop(farm_id, None)
    except Exception as exc:  # noqa: BLE001 — la page s'affiche quand même
        db.rollback()
        logger.warning("🌦️ [météo] Ferme %s : météo non complétée à l'ouverture de la page : %s", farm_id, exc)
    return last_recorded_day(db, farm_id)
    
# ======================================================================
# Lecture
# ======================================================================


def daily_rows(db: Session, farm_id: int, start: date, end: date) -> list[dict]:
    """Les jours enregistrés d'une ferme entre `start` et `end` inclus, dans l'ordre."""
    rows = db.execute(
        text("""
            SELECT day, temperature_min_c, temperature_max_c, temperature_mean_c,
                   precipitation_mm, et0_mm, wind_speed_max_kmh, wind_speed_mean_kmh,
                   humidity_mean_pct, radiation_mj_m2
            FROM weather_daily
            WHERE farm_id = :f AND day BETWEEN :s AND :e
            ORDER BY day
        """),
        {"f": farm_id, "s": start, "e": end},
    ).mappings().all()
    return [dict(r) for r in rows]


def available_months(db: Session, farm_id: int) -> list[str]:
    """Les mois qui ont au moins un jour de météo, du plus récent au plus ancien (« AAAA-MM »)."""
    rows = db.execute(
        text("""
            SELECT DISTINCT to_char(day, 'YYYY-MM') AS m FROM weather_daily
            WHERE farm_id = :f ORDER BY m DESC
        """),
        {"f": farm_id},
    ).fetchall()
    return [r.m for r in rows]


# ======================================================================
# Calculs
# ======================================================================


def _avg(values) -> float | None:
    values = [v for v in values if v is not None]
    return mean(values) if values else None


def longest_dry_spell(rows: list[dict]) -> int:
    """La plus longue suite de jours secs consécutifs (indice CDD de l'OMM)."""
    longest = current = 0
    previous: date | None = None
    for r in rows:
        rain = r.get("precipitation_mm")
        consecutive = previous is not None and (r["day"] - previous).days == 1
        if rain is not None and rain < cfg.DRY_DAY_MM:
            current = current + 1 if consecutive or current == 0 else 1
            longest = max(longest, current)
        else:
            current = 0
        previous = r["day"]
    return longest


def month_values(rows: list[dict]) -> dict:
    """Les chiffres d'un mois à partir de ses jours."""
    def mean_temp(r):
        if r.get("temperature_mean_c") is not None:
            return r["temperature_mean_c"]
        if r.get("temperature_max_c") is not None and r.get("temperature_min_c") is not None:
            return (r["temperature_max_c"] + r["temperature_min_c"]) / 2
        return None

    rains = [r["precipitation_mm"] for r in rows if r.get("precipitation_mm") is not None]
    et0 = [r["et0_mm"] for r in rows if r.get("et0_mm") is not None]
    windiest = max(
        (r for r in rows if r.get("wind_speed_max_kmh") is not None),
        key=lambda r: r["wind_speed_max_kmh"],
        default=None,
    )
    return {
        "temperature_max_c": _avg(r.get("temperature_max_c") for r in rows),
        "temperature_min_c": _avg(r.get("temperature_min_c") for r in rows),
        "temperature_mean_c": _avg(mean_temp(r) for r in rows),
        "precipitation_mm": sum(rains) if rains else None,
        "humidity_mean_pct": _avg(r.get("humidity_mean_pct") for r in rows),
        "wind_speed_mean_kmh": _avg(r.get("wind_speed_mean_kmh") for r in rows),
        "radiation_mj_m2": _avg(r.get("radiation_mj_m2") for r in rows),
        "et0_mm": sum(et0) if et0 else None,
        "dry_days": sum(1 for v in rains if v < cfg.DRY_DAY_MM) if rains else None,
        "longest_dry_spell": longest_dry_spell(rows) if rains else None,
        "wind_speed_max_kmh": windiest["wind_speed_max_kmh"] if windiest else None,
        "windiest_day": windiest["day"] if windiest else None,
    }


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def normal(db: Session, farm_id: int, month: int) -> dict:
    """
    La normale d'un mois : la moyenne de ses chiffres sur la période des
    normales, en ne gardant que les années où le mois est (presque) complet.
    """
    yearly = []
    for y in range(cfg.NORMALS_START, cfg.NORMALS_END + 1):
        start, end = _month_bounds(y, month)
        rows = daily_rows(db, farm_id, start, end)
        if len(rows) >= end.day - cfg.MAX_MISSING_DAYS:
            yearly.append(month_values(rows))
    out = {key: _avg(v.get(key) for v in yearly) for key in PARAMETERS}
    out["years"] = len(yearly)
    return out


def anomaly(value: float | None, reference: float | None, kind: str) -> float | None:
    """L'écart à la normale : une différence, ou un pourcentage."""
    if value is None or reference is None:
        return None
    if kind == "pct":
        return None if reference == 0 else (value - reference) / reference * 100
    return value - reference


def _round(v, digits: int = 1):
    return None if v is None else round(v, digits)


def month_conditions(db: Session, farm_id: int, year: int, month: int, today: date | None = None) -> dict:
    """
    Tout ce qu'affiche l'onglet « Paramètres » pour un mois : chaque
    paramètre, sa normale, son écart, et si le mois est complet.
    """
    today = today or date.today()
    start, end = _month_bounds(year, month)
    last_day = min(end, today)
    rows = daily_rows(db, farm_id, start, last_day) if start <= today else []
    values = month_values(rows)
    ref = normal(db, farm_id, month)
    partial = end >= today

    parameters = {}
    for key, kind in PARAMETERS.items():
        # Pour un mois en cours, comparer un cumul à la normale du mois entier
        # n'aurait pas de sens : l'écart n'est donné qu'en fin de mois
        compare = not (partial and key in CUMULATIVE)
        parameters[key] = {
            "value": _round(values[key]),
            "normal": _round(ref[key]),
            "anomaly": _round(anomaly(values[key], ref[key], kind)) if compare else None,
            "anomaly_unit": "%" if kind == "pct" else None,
        }
    return {
        "farm_id": farm_id,
        "month": f"{year}-{month:02d}",
        "days": len(rows),
        "days_in_month": end.day,
        "partial": partial,
        "normals_period": [cfg.NORMALS_START, cfg.NORMALS_END],
        "normals_years": ref["years"],
        "parameters": parameters,
        "longest_dry_spell": values["longest_dry_spell"],
        "wind_speed_max_kmh": _round(values["wind_speed_max_kmh"]),
        "windiest_day": values["windiest_day"],
    }
    

def compare_years(db: Session, farm_id: int, month: int, today: date | None = None) -> dict:
    """
    Le même mois pour chaque année, depuis le début des normales jusqu'à
    aujourd'hui, et sa normale : pour comparer les années entre elles.
    Le mois de l'année en cours peut être partiel : il est signalé.
    """
    today = today or date.today()
    rows_out = []
    for y in range(today.year, cfg.NORMALS_START - 1, -1):
        start, end = _month_bounds(y, month)
        if start > today:
            continue
        rows = daily_rows(db, farm_id, start, min(end, today))
        if not rows:
            continue
        values = month_values(rows)
        rows_out.append({
            "year": y,
            "days": len(rows),
            "partial": end >= today or len(rows) < end.day - cfg.MAX_MISSING_DAYS,
            "values": {key: _round(values[key]) for key in PARAMETERS},
        })
    ref = normal(db, farm_id, month)
    return {
        "farm_id": farm_id,
        "month": month,
        "normals_period": [cfg.NORMALS_START, cfg.NORMALS_END],
        "years": rows_out,
        "normal": {key: _round(ref[key]) for key in PARAMETERS},
    }