"""
Calcul des besoins en irrigation (méthode FAO-56).

    ETc        = ET0 × Kc(stade)        besoin de la culture
    besoin net = ETc − pluie efficace   ce qu'il reste à apporter

ET0 est l'évapotranspiration de référence, fournie directement par
Open-Meteo (variable `et0_fao_evapotranspiration`), ce qui évite de
recalculer Penman-Monteith : la même formule, mais déjà appliquée aux
données météo locales.

La pluie et l'ET0 des derniers jours sont LUES dans weather_daily, déjà
alimentée chaque matin par la tâche météo : aucune donnée en double, et
aucun appel à Internet. Open-Meteo n'est interrogé qu'en secours, si la
base n'a pas encore ces jours (ex. ferme toute nouvelle), avec de nouvelles
tentatives en cas d'erreur passagère.

Le résultat est donné en millimètres (1 mm = 1 litre/m²) puis converti
en mètres cubes pour la parcelle, seule unité directement utilisable par
l'agriculteur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.agronomy.water import get_kc_for_stages
from app.services.open_meteo_client import get_json

logger = logging.getLogger(__name__)

OPEN_METEO_ARCHIVE = "https://api.open-meteo.com/v1/forecast"

#: Fraction de la pluie réellement utilisable par la plante : le reste
#: ruisselle ou percole. Valeur prudente pour un sol méditerranéen.
RAIN_EFFICIENCY = 0.80

#: Fenêtre de calcul : une semaine, horizon de décision de l'irrigation
DEFAULT_WINDOW_DAYS = 7


@dataclass
class WaterNeed:
    """Bilan hydrique d'une parcelle sur la fenêtre de calcul."""

    days: int
    #: Évapotranspiration de référence cumulée (mm)
    et0_mm: float
    kc: float | None
    #: Besoin de la culture = ET0 × Kc (mm)
    etc_mm: float
    #: Pluie cumulée (mm)
    rain_mm: float
    #: Pluie réellement utilisable (mm)
    effective_rain_mm: float
    #: Ce qu'il reste à apporter (mm), jamais négatif
    net_need_mm: float
    #: Converti pour la parcelle (m³)
    net_need_m3: float | None
    #: Explication affichable
    message: str


async def fetch_water_balance(
    latitude: float,
    longitude: float,
    days: int = DEFAULT_WINDOW_DAYS,
) -> tuple[float, float]:
    """
    Récupère l'ET0 et la pluie cumulées des N derniers jours.

    Returns:
        (et0_mm, rain_mm)
    """
    end = date.today()
    start = end - timedelta(days=days - 1)

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": "et0_fao_evapotranspiration,precipitation_sum",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "auto",
    }

    daily = (await get_json(OPEN_METEO_ARCHIVE, params, timeout=15.0)).get("daily", {})
    et0_values = [v for v in (daily.get("et0_fao_evapotranspiration") or []) if v is not None]
    rain_values = [v for v in (daily.get("precipitation_sum") or []) if v is not None]

    return sum(et0_values), sum(rain_values)


def balance_from_weather_daily(
    db: Session, farm_id: int, days: int = DEFAULT_WINDOW_DAYS, today: date | None = None
) -> tuple[float, float] | None:
    """
    ET0 et pluie cumulées des N derniers jours, lues dans weather_daily.
    Renvoie None s'il manque plus d'un jour : le bilan serait faussé.
    """
    end = today or date.today()
    start = end - timedelta(days=days - 1)
    row = db.execute(
        text("""
            SELECT COUNT(et0_mm) AS n, COALESCE(SUM(et0_mm), 0) AS et0, COALESCE(SUM(precipitation_mm), 0) AS rain
            FROM weather_daily
            WHERE farm_id = :farm_id AND day BETWEEN :start AND :end
        """),
        {"farm_id": farm_id, "start": start, "end": end},
    ).fetchone()
    if row is None or row.n < days - 1:
        return None
    return float(row.et0), float(row.rain)


def compute_water_need(
    species: str,
    stage,
    et0_mm: float,
    rain_mm: float,
    area_ha: float | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
) -> WaterNeed:
    """
    Bilan hydrique à partir de l'ET0 et de la pluie mesurées.

    `stage` : un stade, ou TOUS les stades actifs à cette date
    (calendar.get_stages_at().codes). Avec plusieurs stades, le Kc le plus
    élevé est retenu (voir water.get_kc_for_stages).

    Sans Kc connu pour l'espèce, le besoin n'est pas calculé : mieux vaut
    ne rien afficher qu'une quantité d'eau fausse.
    """
    kc = get_kc_for_stages(species, stage)

    if kc is None:
        return WaterNeed(
            days=days, et0_mm=round(et0_mm, 1), kc=None, etc_mm=0.0,
            rain_mm=round(rain_mm, 1), effective_rain_mm=0.0,
            net_need_mm=0.0, net_need_m3=None,
            message="Coefficient cultural non disponible pour cette espèce.",
        )

    etc = et0_mm * kc
    effective_rain = rain_mm * RAIN_EFFICIENCY
    net = max(etc - effective_rain, 0.0)

    # 1 mm sur 1 ha = 10 m³
    net_m3 = net * 10 * area_ha if area_ha else None

    if net <= 0:
        message = "Les pluies couvrent le besoin de la culture : pas d'apport nécessaire."
    elif net < 5:
        message = "Besoin faible : un apport peut être différé selon l'état du sol."
    else:
        message = f"Apport conseillé d'environ {net:.0f} mm sur les prochains jours."

    return WaterNeed(
        days=days,
        et0_mm=round(et0_mm, 1),
        kc=kc,
        etc_mm=round(etc, 1),
        rain_mm=round(rain_mm, 1),
        effective_rain_mm=round(effective_rain, 1),
        net_need_mm=round(net, 1),
        net_need_m3=round(net_m3, 1) if net_m3 is not None else None,
        message=message,
    )


async def get_water_need(
    species: str,
    stage,
    latitude: float,
    longitude: float,
    area_ha: float | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
    db: Session | None = None,
    farm_id: int | None = None,
) -> WaterNeed | None:
    """
    Bilan hydrique complet : la météo des derniers jours (weather_daily,
    sinon Open-Meteo en secours), puis le Kc. Renvoie None si la météo est
    indisponible partout.
    """
    balance = balance_from_weather_daily(db, farm_id, days) if db is not None and farm_id else None
    if balance is not None:
        et0_mm, rain_mm = balance
    else:
        try:
            et0_mm, rain_mm = await fetch_water_balance(latitude, longitude, days)
        except Exception as e:
            logger.warning("[eau] Météo indisponible pour le bilan hydrique : %s", e)
            return None

    return compute_water_need(species, stage, et0_mm, rain_mm, area_ha, days)