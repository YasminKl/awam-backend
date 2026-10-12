"""
Calcul des besoins en irrigation (méthode FAO-56).

    ETc        = ET0 × Kc(stade)        besoin de la culture
    besoin net = ETc − pluie efficace   ce qu'il reste à apporter

ET0 est l'évapotranspiration de référence, fournie directement par
Open-Meteo (variable `et0_fao_evapotranspiration`), ce qui évite de
recalculer Penman-Monteith : la même formule, mais déjà appliquée aux
données météo locales.

Le résultat est donné en millimètres (1 mm = 1 litre/m²) puis converti
en mètres cubes pour la parcelle, seule unité directement utilisable par
l'agriculteur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

from app.services.agronomy.water import get_kc

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

    async with httpx.AsyncClient() as client:
        response = await client.get(OPEN_METEO_ARCHIVE, params=params, timeout=15.0)
        response.raise_for_status()

    daily = response.json().get("daily", {})
    et0_values = [v for v in (daily.get("et0_fao_evapotranspiration") or []) if v is not None]
    rain_values = [v for v in (daily.get("precipitation_sum") or []) if v is not None]

    return sum(et0_values), sum(rain_values)


def compute_water_need(
    species: str,
    stage: str,
    et0_mm: float,
    rain_mm: float,
    area_ha: float | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
) -> WaterNeed:
    """
    Bilan hydrique à partir de l'ET0 et de la pluie mesurées.

    Sans Kc connu pour l'espèce, le besoin n'est pas calculé : mieux vaut
    ne rien afficher qu'une quantité d'eau fausse.
    """
    kc = get_kc(species, stage)

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
    stage: str,
    latitude: float,
    longitude: float,
    area_ha: float | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
) -> WaterNeed | None:
    """
    Bilan hydrique complet : interroge la météo puis applique le Kc.
    Renvoie None si la météo est indisponible.
    """
    try:
        et0_mm, rain_mm = await fetch_water_balance(latitude, longitude, days)
    except Exception as e:
        logger.warning("[eau] Météo indisponible pour le bilan hydrique : %s", e)
        return None

    return compute_water_need(species, stage, et0_mm, rain_mm, area_ha, days)