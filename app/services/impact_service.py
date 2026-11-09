"""
Onglet « Impacts » de la page Climat et cultures : pour chaque culture en
cours d'une parcelle et pour un mois.

1. LE STADE ET SON CLIMAT
   Les stades du mois viennent du calendrier phénologique AWAM
   (agronomy/calendar.py) : c'est une période HABITUELLE, au mois près,
   pas une date observée. Sur cette période, le climat réel de la ferme
   (weather_daily) est comparé à la NORMALE des MÊMES dates, de 2020 à
   2025. Pour un stade en cours, la période et la normale s'arrêtent au
   même jour : la comparaison reste juste.
   Aucun seuil n'est inventé pour dire « plus chaud » ou « plus sec » : la
   valeur est comparée à la FOURCHETTE réelle des années de la normale
   (au-dessus de toutes, en dessous de toutes, ou dans la fourchette).

2. L'EAU
   Calcul propre à cet onglet, uniquement avec des données de la
   plateforme, sans coefficient estimé :
     besoin (mm)        = ET₀ réelle (Open-Meteo) × Kc du stade (référentiel AWAM)
     eau reçue (mm)     = pluie mesurée (comptée en entier) + arrosages enregistrés
     besoin couvert (%) = eau reçue ÷ besoin × 100
   La seule limite utilisée est 100 % : le besoin lui-même. Pour le mois,
   ses semaines, et la saison depuis le début de la campagne agricole
   (cumulée mois par mois : le Kc change avec le stade).

3. LES AUTRES STRESS (chaleur, gel, excès d'humidité) : à activer quand
   leurs seuils par culture et par stade seront validés.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from statistics import mean

from sqlalchemy.orm import Session

from app.core import conditions_config as cfg
from app.models.crop import Crop
from app.models.parcel import Parcel
from app.services import conditions_service as cs
from app.services import water_balance_service as wb
from app.services.agronomy.calendar import StagePeriod, get_periods, get_stages_at
from app.services.agronomy.water import get_kc_for_stages
from app.services.agronomy.species import SPECIES, stage_label
from app.services.irrigation_service import active_crops, crop_area_m2, crop_labels

#: Paramètres comparés pendant un stade, et comment exprimer leur écart
STAGE_PARAMETERS: dict[str, str] = {
    "temperature_max_c": "diff",
    "temperature_min_c": "diff",
    "precipitation_mm": "pct",
    "et0_mm": "pct",
    "humidity_mean_pct": "diff",
}

#: Une année compte pour la normale si elle a au moins cette part des jours
MIN_COVERAGE = 0.9


# ======================================================================
# 1. Le stade et son climat
# ======================================================================


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def stage_dates(period: StagePeriod, year: int, month: int) -> tuple[date, date]:
    """
    Les dates de la période d'un stade qui contient le mois `month` de
    l'année `year`, y compris pour un stade à cheval sur deux années
    (novembre → février).
    """
    if period.start_month <= period.end_month:
        return date(year, period.start_month, 1), _month_end(year, period.end_month)
    if month >= period.start_month:
        return date(year, period.start_month, 1), _month_end(year + 1, period.end_month)
    return date(year - 1, period.start_month, 1), _month_end(year, period.end_month)


def _shift(day: date, years: int) -> date:
    """La même date, `years` ans plus tôt ou plus tard (29 février → 28)."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def _compare(value: float | None, yearly: list[float], kind: str) -> dict:
    """Valeur, normale, écart, et place dans la fourchette des années de la normale."""
    normal = mean(yearly) if yearly else None
    position = None
    if value is not None and len(yearly) >= 2:
        if value > max(yearly):
            position = "above"
        elif value < min(yearly):
            position = "below"
        else:
            position = "within"
    return {
        "value": cs._round(value),
        "normal": cs._round(normal),
        "anomaly": cs._round(cs.anomaly(value, normal, kind)),
        "anomaly_unit": "%" if kind == "pct" else None,
        "range": [cs._round(min(yearly)), cs._round(max(yearly))] if yearly else None,
        "position": position,
    }


#: Mots de la phrase de résumé, selon le paramètre et sa place dans la fourchette
_WORDS = {
    ("temperature_max_c", "above"): "des journées plus chaudes",
    ("temperature_max_c", "below"): "des journées plus fraîches",
    ("temperature_min_c", "above"): "des nuits plus chaudes",
    ("temperature_min_c", "below"): "des nuits plus fraîches",
    ("precipitation_mm", "above"): "plus de pluie",
    ("precipitation_mm", "below"): "moins de pluie",
    ("et0_mm", "above"): "une évaporation plus forte",
    ("et0_mm", "below"): "une évaporation plus faible",
    ("humidity_mean_pct", "above"): "un air plus humide",
    ("humidity_mean_pct", "below"): "un air plus sec",
}


def stage_summary(parameters: dict, first: int, last: int) -> str:
    """La phrase qui résume le climat du stade, sans seuil inventé."""
    unusual = [_WORDS[(k, p["position"])] for k, p in parameters.items() if p["position"] in ("above", "below")]
    if not unusual:
        if all(p["position"] is None for p in parameters.values()):
            return "Pas assez d'années complètes pour comparer ce stade à la normale."
        return f"Un climat dans la fourchette habituelle des années {first} à {last}, pour tous les paramètres."
    listed = unusual[0] if len(unusual) == 1 else ", ".join(unusual[:-1]) + " et " + unusual[-1]
    return f"Hors de la fourchette des années {first} à {last} : {listed} que toutes ces années."


def stage_climate(db: Session, farm_id: int, start: date, end: date, today: date) -> dict:
    """Le climat réel d'une période de stade, et sa normale sur les mêmes dates."""
    yesterday = today - timedelta(days=1)
    last = min(end, yesterday)
    current = cs.month_values(cs.daily_rows(db, farm_id, start, last)) if start <= last else {}
    span = (last - start).days + 1 if start <= last else 0

    yearly: dict[str, list[float]] = {k: [] for k in STAGE_PARAMETERS}
    years_used = 0
    for y in range(cfg.NORMALS_START, cfg.NORMALS_END + 1):
        shift = y - start.year
        rows = cs.daily_rows(db, farm_id, _shift(start, shift), _shift(last, shift))
        if span == 0 or len(rows) < span * MIN_COVERAGE:
            continue
        values = cs.month_values(rows)
        years_used += 1
        for k in STAGE_PARAMETERS:
            if values.get(k) is not None:
                yearly[k].append(values[k])

    parameters = {k: _compare(current.get(k), yearly[k], kind) for k, kind in STAGE_PARAMETERS.items()}
    return {
        "start": start,
        "end": end,
        "counted_until": last if start <= last else None,
        "in_progress": end > yesterday,
        "not_started": start > yesterday,
        "normals_years": years_used,
        "parameters": parameters,
        "summary": stage_summary(parameters, cfg.NORMALS_START, cfg.NORMALS_END)
        if start <= last else "Ce stade n'a pas encore commencé.",
    }


def crop_stages(crop: Crop, year: int, month: int) -> list[dict]:
    """Les stades de la culture pendant le mois, avec les dates de leur période habituelle."""
    out = []
    for period in get_periods(crop.species, crop.planting_date):
        if period.covers(month):
            start, end = stage_dates(period, year, month)
            out.append({"stage": period.stage, "label": stage_label(period.stage), "months": period.label, "start": start, "end": end})
    return out


# ======================================================================
# 2. L'eau
# ======================================================================


def water_period(db: Session, parcel: Parcel, crop: Crop, start: date, end: date, single: bool) -> dict:
    """
    Le bilan d'eau d'une culture sur une période, uniquement avec des
    données de la plateforme : ET₀ et pluie réelles, Kc du référentiel,
    arrosages enregistrés. Quand une donnée manque, aucun chiffre n'est
    donné, et la raison est dite.
    """
    days = (end - start).days + 1
    known_days, et0, rain = wb._weather(db, parcel.farm_id, start, end)
    middle = start + timedelta(days=days // 2)
    stages = get_stages_at(crop.species, middle, crop.planting_date)
    kc = get_kc_for_stages(crop.species, stages.codes) if stages else None
    irrigation_l = wb._irrigation_liters(db, parcel, crop, single, start, end)

    problems = []
    if known_days < days:
        problems.append(f"la météo réelle manque pour {days - known_days} jour(s) de la période")
    if kc is None:
        problems.append("le référentiel AWAM n'a pas de Kc pour cette espèce à ce stade")
    area = None
    if irrigation_l > 0:
        # Litres → mm : il faut la surface qui a reçu l'eau
        area = crop_area_m2(db, parcel, crop)
        if area is None:
            problems.append("des arrosages sont enregistrés, mais la surface de chaque culture de la parcelle est inconnue")

    out = {
        "start": start, "end": end, "days": days,
        "et0_mm": round(et0, 1), "kc": kc, "rain_mm": round(rain, 1),
        "irrigation_l": round(irrigation_l, 0),
        "need_mm": None, "irrigation_mm": None, "received_mm": None,
        "coverage_pct": None, "missing_mm": None, "covered": None, "problems": problems,
    }
    if problems:
        return out
    need_mm = et0 * kc
    irrigation_mm = irrigation_l / area if area else 0.0
    received_mm = rain + irrigation_mm
    coverage = received_mm / need_mm * 100 if need_mm > 0 else None
    out.update({
        "need_mm": round(need_mm, 1),
        "irrigation_mm": round(irrigation_mm, 1),
        "received_mm": round(received_mm, 1),
        "coverage_pct": round(coverage, 0) if coverage is not None else None,
        "missing_mm": round(max(need_mm - received_mm, 0), 1),
        # La seule limite : le besoin lui-même
        "covered": received_mm >= need_mm,
    })
    return out


def _weeks_not_covered(db: Session, parcel: Parcel, crop: Crop, start: date, end: date, single: bool) -> list[dict]:
    """Les semaines (du lundi au dimanche, limitées au mois) où l'eau reçue est inférieure au besoin."""
    out = []
    monday = start - timedelta(days=start.weekday())
    while monday <= end:
        first, last = max(monday, start), min(monday + timedelta(days=6), end)
        b = water_period(db, parcel, crop, first, last, single)
        if b["covered"] is False:
            out.append({"start": first, "end": last, "days": b["days"], "need_mm": b["need_mm"],
                        "received_mm": b["received_mm"], "coverage_pct": b["coverage_pct"]})
        monday += timedelta(days=7)
    return out


def _season(db: Session, parcel: Parcel, crop: Crop, start: date, end: date, single: bool) -> dict:
    """
    Le bilan d'eau depuis le début de la saison, cumulé MOIS PAR MOIS : le
    Kc change avec le stade, un seul Kc pour toute la saison serait faux.
    """
    totals = {"need_mm": 0.0, "rain_mm": 0.0, "irrigation_mm": 0.0, "irrigation_l": 0.0}
    cursor = start
    while cursor <= end:
        last = min(_month_end(cursor.year, cursor.month), end)
        b = water_period(db, parcel, crop, cursor, last, single)
        if b["problems"]:
            return {"start": start, "end": end, "problems": b["problems"]}
        for k in totals:
            totals[k] += b[k] or 0.0
        cursor = last + timedelta(days=1)
    received = totals["rain_mm"] + totals["irrigation_mm"]
    coverage = received / totals["need_mm"] * 100 if totals["need_mm"] > 0 else None
    return {
        "start": start, "end": end, "problems": [],
        "need_mm": round(totals["need_mm"], 1),
        "rain_mm": round(totals["rain_mm"], 1),
        "irrigation_mm": round(totals["irrigation_mm"], 1),
        "irrigation_l": round(totals["irrigation_l"], 0),
        "received_mm": round(received, 1),
        "coverage_pct": round(coverage, 0) if coverage is not None else None,
        "missing_mm": round(max(totals["need_mm"] - received, 0), 1),
        "covered": received >= totals["need_mm"],
    }


def crop_water(db: Session, parcel: Parcel, crop: Crop, year: int, month: int, single: bool, today: date) -> dict:
    """L'eau du mois (jusqu'à hier), ses semaines non couvertes, et la saison."""
    yesterday = today - timedelta(days=1)
    start = date(year, month, 1)
    end = min(_month_end(year, month), yesterday)
    if start > end:
        return {"month": None, "weeks": [], "season": None}
    season_year = year if month >= cfg.SEASON_START_MONTH else year - 1
    season_start = date(season_year, cfg.SEASON_START_MONTH, 1)
    balance = water_period(db, parcel, crop, start, end, single)
    return {
        "month": balance,
        "weeks": [] if balance["problems"] else _weeks_not_covered(db, parcel, crop, start, end, single),
        "season": _season(db, parcel, crop, season_start, end, single),
    }


# ======================================================================
# La parcelle
# ======================================================================


def parcel_impacts(db: Session, parcel: Parcel, year: int, month: int, today: date | None = None) -> dict:
    """Tout ce qu'affiche l'onglet Impacts, pour chaque culture en cours de la parcelle."""
    today = today or date.today()
    crops = active_crops(db, parcel.id)
    single = len(crops) == 1
    out = []
    for crop in sorted(crops, key=lambda c: c.id):
        spec = SPECIES.get(crop.species)
        stages = []
        for s in crop_stages(crop, year, month):
            stages.append({**s, **stage_climate(db, parcel.farm_id, s["start"], s["end"], today)})
        out.append({
            "crop_id": crop.id,
            "label": crop_labels(db, [crop.id])[0]["label"],
            "species_label": spec.label if spec else crop.species,
            "planting_date": crop.planting_date,
            "stages": stages,
            "water": crop_water(db, parcel, crop, year, month, single, today),
        })
    return {
        "parcel_id": parcel.id,
        "parcel_name": parcel.name,
        "month": f"{year}-{month:02d}",
        "normals_period": [cfg.NORMALS_START, cfg.NORMALS_END],
        "season_start_month": cfg.SEASON_START_MONTH,
        "crops": out,
    }