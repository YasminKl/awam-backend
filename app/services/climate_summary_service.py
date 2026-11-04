"""
Lecture du climat d'un lieu, pour la page « Projection climatique » : les
séries année par année, les chiffres clés et la surveillance de l'écart
entre le climat réel et les projections. Lecture seule : aucun appel
extérieur, tout vient de la base.

  - Climat réel : weather_daily (pluie, températures), résumé par mois sans
    copie ; ERA5 (températures de surface) dans climate_monthly.
  - Projections : climate_monthly, valeurs CORRIGÉES de leurs biais.
  - ET₀ : méthode de Hargreaves (FAO-56) pour le réel COMME pour les
    projections, afin de comparer des valeurs calculées de la même façon.

Indice d'aridité : pluie ÷ ET₀, classé selon le PNUE (UNEP, 1992) :
hyper-aride < 0,05 ; aride < 0,20 ; semi-aride < 0,50 ; subhumide sec
< 0,65 ; humide au-delà. Le PNUE utilise l'évapotranspiration potentielle :
l'ET₀ de FAO-56 en est une approximation, à préciser dans un rapport.
"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, pstdev

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import climate_config as cfg
from app.models.climate import SOURCE_CMIP6, SOURCE_ERA5, ClimateSite
from app.services import climate_projection_service as projection

#: Un lieu à moins de cette distance de la mer est côtier
COASTAL_KM = 50.0

#: Nombre d'années réelles récentes pour la surveillance de l'écart
RECENT_YEARS = 5

#: Classes d'aridité du PNUE (limite supérieure, libellé)
ARIDITY_CLASSES = [
    (0.05, "hyper-aride"),
    (0.20, "aride"),
    (0.50, "semi-aride"),
    (0.65, "subhumide sec"),
    (math.inf, "humide"),
]

SUMMER_MONTHS = (6, 7, 8)


def aridity_class(index: float | None) -> str | None:
    if index is None:
        return None
    return next(label for limit, label in ARIDITY_CLASSES if index < limit)


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _avg(values) -> float | None:
    values = [v for v in values if v is not None]
    return mean(values) if values else None


# ======================================================================
# Climat réel, année par année
# ======================================================================


def real_years(db: Session, site: ClimateSite, last_year: int) -> dict[int, dict]:
    """
    Le climat réel année par année, pour les années COMPLÈTES : pluie,
    température moyenne, ET₀ (Hargreaves), sol en été et mer (ERA5).
    """
    monthly = projection.real_monthly_from_weather(db, site)
    rain, tmax, tmin, tmean = (
        monthly["precipitation_mm"], monthly["air_temperature_max_c"],
        monthly["air_temperature_min_c"], monthly["air_temperature_c"],
    )
    surfaces = db.execute(
        text("""
            SELECT year, month, land_surface_temperature_c AS soil, sea_surface_temperature_c AS sea
            FROM climate_monthly WHERE site_id = :s AND source = :src
        """),
        {"s": site.id, "src": SOURCE_ERA5},
    ).mappings().all()
    soil = {(r["year"], r["month"]): r["soil"] for r in surfaces}
    sea = {(r["year"], r["month"]): r["sea"] for r in surfaces}

    years: dict[int, dict] = {}
    for y in range(cfg.START_YEAR, last_year + 1):
        months = [(y, m) for m in range(1, 13)]
        if not all(ym in rain for ym in months):
            continue
        et0 = [
            projection.hargreaves_et0_month(tmean.get(ym), tmax.get(ym), tmin.get(ym), site.latitude, *ym)
            for ym in months
        ]
        summer = [soil.get((y, m)) for m in SUMMER_MONTHS]
        years[y] = {
            "rain": sum(rain[ym] for ym in months),
            "temperature": _avg(tmean.get(ym) for ym in months),
            "et0": sum(et0) if all(v is not None for v in et0) else None,
            "summer_soil": _avg(summer) if all(v is not None for v in summer) else None,
            "sea": _avg(sea.get(ym) for ym in months),
        }
    return years


# ======================================================================
# Projections, année par année et par modèle
# ======================================================================


def projected_years(db: Session, site: ClimateSite, scenario: str) -> dict[str, dict[int, dict]]:
    """Les projections CORRIGÉES, année par année, pour chaque modèle."""
    rows = db.execute(
        text("""
            SELECT model, year,
                   SUM(precipitation_mm) AS rain, COUNT(precipitation_mm) AS n_rain,
                   AVG(air_temperature_c) AS temperature,
                   SUM(et0_mm) AS et0, COUNT(et0_mm) AS n_et0,
                   AVG(land_surface_temperature_c) FILTER (WHERE month IN (6, 7, 8)) AS summer_soil,
                   AVG(sea_surface_temperature_c) AS sea
            FROM climate_monthly
            WHERE site_id = :s AND source = :src AND scenario = :sc AND corrected
            GROUP BY model, year
        """),
        {"s": site.id, "src": SOURCE_CMIP6, "sc": scenario},
    ).mappings().all()
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    for r in rows:
        out[r["model"]][r["year"]] = {
            "rain": r["rain"] if r["n_rain"] == 12 else None,
            "temperature": r["temperature"],
            "et0": r["et0"] if r["n_et0"] == 12 else None,
            "summer_soil": r["summer_soil"],
            "sea": r["sea"],
        }
    return dict(out)


def _ensemble(models: dict[str, dict[int, dict]], year: int, key: str) -> tuple[float | None, float | None, float | None]:
    """Moyenne, minimum et maximum des modèles pour une année et une variable."""
    values = [m[year][key] for m in models.values() if year in m and m[year].get(key) is not None]
    if not values:
        return None, None, None
    return mean(values), min(values), max(values)


# ======================================================================
# Surveillance de l'écart (niveau 1)
# ======================================================================


def divergence(real: dict[int, dict], models: dict[str, dict[int, dict]]) -> list[str]:
    """
    Compare la moyenne des dernières années RÉELLES à la fourchette des
    modèles sur les mêmes années. Une seule année ne veut rien dire : on
    raisonne sur RECENT_YEARS ans, avec une marge égale à deux fois
    l'incertitude de la moyenne réelle (2 σ / √n), pour ne pas réagir à la
    variabilité naturelle.
    """
    recent = sorted(real)[-RECENT_YEARS:]
    if len(recent) < 3 or not models:
        return []
    messages = []
    for key, label, unit, drier, wetter in (
        ("rain", "pluie", "mm", "plus sèches", "plus pluvieuses"),
        ("temperature", "température", "°C", "plus chaudes", "plus fraîches"),
    ):
        observed = [real[y][key] for y in recent if real[y].get(key) is not None]
        per_model = [
            _avg(m[y][key] for y in recent if y in m and m[y].get(key) is not None) for m in models.values()
        ]
        per_model = [v for v in per_model if v is not None]
        if len(observed) < 3 or not per_model:
            continue
        real_mean = mean(observed)
        margin = 2 * pstdev(observed) / math.sqrt(len(observed))
        low, high = min(per_model) - margin, max(per_model) + margin
        if real_mean < low or real_mean > high:
            if key == "rain":
                way = drier if real_mean < low else wetter
            else:
                way = drier if real_mean > high else wetter
            messages.append(
                f"Les {len(observed)} dernières années réelles sont {way} que les projections "
                f"({label} moyenne {real_mean:.1f} {unit}, projections entre "
                f"{min(per_model):.1f} et {max(per_model):.1f} {unit}) : la projection est à "
                f"prendre avec prudence pour ce lieu."
            )
    return messages


# ======================================================================
# Périodes et interprétation
# ======================================================================

#: Classes du plus sec au plus humide, pour dire « se rapproche de… »
_CLASS_ORDER = [label for _limit, label in ARIDITY_CLASSES]


def _fmt(value: float, digits: int = 0) -> str:
    """Nombre à la française : 1 254 ; 0,38."""
    text = f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")
    return text.replace(" ", "\u202f")


def _trend(value: float, reference: float, noun: str) -> str:
    """« la pluie est stable », « la pluie baisse de 11 % »…"""
    if not reference:
        return f"{noun} : {_fmt(value)} mm par an"
    pct = (value - reference) / reference * 100
    if abs(pct) < cfg.STABLE_PCT:
        return f"{noun} reste stable"
    verb = "baisse" if pct < 0 else "augmente"
    return f"{noun} {verb} de {_fmt(abs(pct))} %"


def _period_ranges(real_years_list: list[int]) -> list[tuple[int, int, bool]]:
    """
    Les périodes affichées : la période RÉELLE (années complètes connues),
    puis des périodes projetées de PERIOD_YEARS ans jusqu'à la fin.
    """
    periods: list[tuple[int, int, bool]] = []
    if real_years_list:
        periods.append((real_years_list[0], real_years_list[-1], True))
        start = real_years_list[-1] + 1
    else:
        start = cfg.START_YEAR
    while start <= cfg.END_YEAR:
        end = min(start + cfg.PERIOD_YEARS - 1, cfg.END_YEAR)
        periods.append((start, end, False))
        start = end + 1
    return periods


def _period_values(real, models, start: int, end: int, is_real: bool) -> dict | None:
    """Pluie, besoin en eau, manque d'eau et aridité moyens d'une période."""
    years = range(start, end + 1)
    if is_real:
        rain = _avg(real[y]["rain"] for y in years if y in real)
        demand = _avg(real[y]["et0"] for y in years if y in real)
        per_model_rain: list[float] = []
    else:
        rain = _avg(_ensemble(models, y, "rain")[0] for y in years)
        demand = _avg(_ensemble(models, y, "et0")[0] for y in years)
        per_model_rain = [
            v for v in (_avg(m[y]["rain"] for y in years if y in m) for m in models.values()) if v is not None
        ]
    if rain is None or demand is None:
        return None
    aridity = rain / demand if demand else None
    return {
        "start": start, "end": end, "real": is_real,
        "rain": rain, "demand": demand, "deficit": max(demand - rain, 0.0),
        "aridity": aridity, "class": aridity_class(aridity), "per_model_rain": per_model_rain,
    }


def interpret_period(period: dict, reference: dict | None) -> list[str]:
    """
    Les phrases qui expliquent une période, en mots simples. Construites à
    partir des chiffres : elles changent quand les données changent.
    """
    rain, demand, deficit = period["rain"], period["demand"], period["deficit"]

    if period["real"] or reference is None:
        lines = [
            f"Il tombe en moyenne {_fmt(rain)} mm de pluie par an, "
            f"pour un besoin en eau du climat de {_fmt(demand)} mm."
        ]
        if deficit > 0:
            lines.append(f"Il manque donc environ {_fmt(deficit)} mm d'eau par an, à apporter par l'irrigation.")
        else:
            lines.append("La pluie couvre le besoin en eau du climat.")
        if period["class"]:
            lines.append(f"Le climat est {period['class']}.")
        return lines

    rain_text = _trend(rain, reference["rain"], "La pluie")
    demand_text = _trend(demand, reference["demand"], "le besoin en eau")
    if rain_text.endswith("reste stable") and demand_text.endswith("reste stable"):
        lines = ["La pluie et le besoin en eau restent stables."]
    else:
        lines = [f"{rain_text}, et {demand_text}."]

    change = deficit - reference["deficit"]
    if reference["deficit"] and abs(change) / reference["deficit"] * 100 < cfg.STABLE_PCT:
        lines.append("Le manque d'eau reste proche d'aujourd'hui.")
    elif change > 0:
        lines.append(f"Il manquera {_fmt(change)} mm d'eau de plus par an qu'aujourd'hui.")
    elif change < 0:
        lines.append(f"Il manquera {_fmt(-change)} mm d'eau de moins par an qu'aujourd'hui.")

    now_class, then_class = reference["class"], period["class"]
    if then_class and now_class and then_class != now_class:
        lines.append(f"Le climat passe de {now_class} à {then_class}.")
    elif then_class:
        index = _CLASS_ORDER.index(then_class)
        lower = ARIDITY_CLASSES[index - 1][0] if index > 0 else None
        upper = ARIDITY_CLASSES[index][0]
        near_drier = lower is not None and period["aridity"] is not None and (
            period["aridity"] - lower < 0.1 * (upper - lower)
        )
        if near_drier:
            lines.append(f"Le climat reste {then_class}, mais se rapproche de {_CLASS_ORDER[index - 1]}.")
        else:
            lines.append(f"Le climat reste {then_class}.")

    # Les modèles ne sont pas d'accord sur le sens de la pluie
    if reference["rain"] and len(period["per_model_rain"]) > 1:
        changes = [(v - reference["rain"]) / reference["rain"] * 100 for v in period["per_model_rain"]]
        if min(changes) <= -cfg.STABLE_PCT and max(changes) >= cfg.STABLE_PCT:
            lines.append("Attention : pour la pluie, les modèles ne sont pas d'accord entre eux.")
    return lines


def periods_summary(real: dict[int, dict], models: dict[str, dict[int, dict]]) -> list[dict]:
    """Les périodes, avec leurs valeurs et leur interprétation."""
    out = []
    reference = None
    for start, end, is_real in _period_ranges(sorted(real)):
        period = _period_values(real, models, start, end, is_real)
        if period is None:
            continue
        if is_real:
            reference = period
        out.append({
            "start": start, "end": end, "real": is_real,
            "rain": _round(period["rain"], 0), "demand": _round(period["demand"], 0),
            "deficit": _round(period["deficit"], 0), "climate_class": period["class"],
            "interpretation": interpret_period(period, reference if not is_real else None),
        })
    return out


# ======================================================================
# Le résumé complet
# ======================================================================


def site_summary(db: Session, site: ClimateSite, scenario: str, last_complete_year: int) -> dict:
    """Tout ce qu'affiche la page pour un lieu et un scénario."""
    real = real_years(db, site, last_complete_year)
    models = projected_years(db, site, scenario)
    coastal = site.sea_distance_km is not None and site.sea_distance_km <= COASTAL_KM

    years = []
    for y in range(cfg.START_YEAR, cfg.END_YEAR + 1):
        rain_proj, rain_min, rain_max = _ensemble(models, y, "rain")
        et0_proj, _, _ = _ensemble(models, y, "et0")
        r = real.get(y, {})
        years.append({
            "year": y,
            "rain_real": _round(r.get("rain")),
            "et0_real": _round(r.get("et0")),
            "rain_projected": _round(rain_proj),
            "rain_min": _round(rain_min),
            "rain_max": _round(rain_max),
            "et0_projected": _round(et0_proj),
        })

    real_period = sorted(real)
    future_period = list(range(cfg.END_YEAR - 4, cfg.END_YEAR + 1))

    def now(key):
        return _avg(real[y].get(key) for y in real_period)

    def future(key):
        return _avg(_ensemble(models, y, key)[0] for y in future_period)

    rain_now, rain_future = now("rain"), future("rain")
    et0_now, et0_future = now("et0"), future("et0")
    aridity_now = rain_now / et0_now if rain_now is not None and et0_now else None
    aridity_future = rain_future / et0_future if rain_future is not None and et0_future else None

    kpis = {
        "rain": {"now": _round(rain_now, 0), "future": _round(rain_future, 0), "unit": "mm"},
        "et0": {"now": _round(et0_now, 0), "future": _round(et0_future, 0), "unit": "mm"},
        "aridity": {
            "now": _round(aridity_now, 2), "future": _round(aridity_future, 2),
            "class_now": aridity_class(aridity_now), "class_future": aridity_class(aridity_future),
        },
        "summer_soil": {"now": _round(now("summer_soil")), "future": _round(future("summer_soil")), "unit": "°C"},
        "sea": (
            {"now": _round(now("sea")), "future": _round(future("sea")), "unit": "°C"} if coastal else None
        ),
    }

    return {
        "site": {
            "id": site.id, "name": site.name, "farm_id": site.farm_id,
            "latitude": site.latitude, "longitude": site.longitude,
            "status": site.status, "last_error": site.last_error,
            "sea_distance_km": site.sea_distance_km, "coastal": coastal,
        },
        "scenario": scenario,
        "scenarios": cfg.SCENARIOS,
        "models": sorted(models),
        "real_period": [real_period[0], real_period[-1]] if real_period else None,
        "future_period": [future_period[0], future_period[-1]],
        "years": years,
        "kpis": kpis,
        "periods": periods_summary(real, models),
        "divergence": divergence(real, models),
    }