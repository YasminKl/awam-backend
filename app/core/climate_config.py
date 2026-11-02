"""
Réglages de la projection climatique, réunis en UN SEUL endroit.

Chaque réglage a une valeur par défaut, et peut être changé dans le fichier
.env du back, sans modifier le code. Le script de vérification
(app/scripts/check_climate_sources.py) et le futur module de projection
lisent tous les deux ces réglages.

Exemples, dans .env :

    CLIMATE_START_YEAR=2020
    CLIMATE_END_YEAR=2040
    CLIMATE_BASELINE_START=1995
    CLIMATE_BASELINE_END=2025
    CLIMATE_SCENARIOS=ssp2_4_5,ssp5_8_5
    CLIMATE_MODELS=ec_earth3_cc,mpi_esm1_2_lr
    CLIMATE_AREA_MARGIN=2.0

Les noms des jeux de données et des variables Copernicus sont plus bas,
dans CLIMATE_VARIABLES : un seul endroit à corriger si Copernicus les
change un jour.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    try:
        return int(value) if value else default
    except ValueError as exc:
        raise ValueError(f"{name} doit être un nombre entier, reçu « {value} »") from exc


def _float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    try:
        return float(value) if value else default
    except ValueError as exc:
        raise ValueError(f"{name} doit être un nombre, reçu « {value} »") from exc


def _list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name, "").strip()
    items = [v.strip() for v in value.split(",") if v.strip()] if value else default
    return items


# ----------------------------------------------------------------------
# Accès à Copernicus (clé personnelle, jamais dans le code)
# ----------------------------------------------------------------------

CDS_URL = os.getenv("CDSAPI_URL", "https://cds.climate.copernicus.eu/api")
CDS_KEY = os.getenv("CDSAPI_KEY", "")

# ----------------------------------------------------------------------
# Périodes
# ----------------------------------------------------------------------

#: Période affichée : du climat réel jusqu'aux projections
START_YEAR = _int("CLIMATE_START_YEAR", 2020)
END_YEAR = _int("CLIMATE_END_YEAR", 2040)

#: Période de référence, pour corriger le biais des modèles face au climat
#: réel (ERA5) : 20 à 30 ans, comme le font les climatologues
BASELINE_START = _int("CLIMATE_BASELINE_START", 1995)
BASELINE_END = _int("CLIMATE_BASELINE_END", 2025)

if START_YEAR > END_YEAR:
    raise ValueError("CLIMATE_START_YEAR doit précéder CLIMATE_END_YEAR")
if BASELINE_START > BASELINE_END:
    raise ValueError("CLIMATE_BASELINE_START doit précéder CLIMATE_BASELINE_END")

# ----------------------------------------------------------------------
# Projections
# ----------------------------------------------------------------------

#: Scénarios d'émissions : intermédiaire et pessimiste par défaut
SCENARIOS = _list("CLIMATE_SCENARIOS", ["ssp2_4_5", "ssp5_8_5"])

#: Modèles climatiques CMIP6, à confirmer avec le script de vérification
MODELS = _list("CLIMATE_MODELS", ["ec_earth3_cc"])

#: Demi-largeur de la zone téléchargée autour d'un lieu, en degrés : assez
#: large pour contenir de la mer et quelques mailles des modèles
AREA_MARGIN = _float("CLIMATE_AREA_MARGIN", 2.0)

# ----------------------------------------------------------------------
# Jeux de données Copernicus
# ----------------------------------------------------------------------

ERA5_MONTHLY = "reanalysis-era5-single-levels-monthly-means"
ERA5_DAILY_STATS = "derived-era5-single-levels-daily-statistics"
CMIP6 = "projections-cmip6"


@dataclass(frozen=True)
class ClimateVariable:
    """Une variable du climat, avec ses noms dans chaque source."""

    #: Clé interne, utilisée en base et dans le code
    key: str
    #: Libellé affiché
    label: str
    #: Unité affichée
    unit: str
    #: Nom dans la demande ERA5 (jeu mensuel), ou None
    era5: str | None
    #: Nom de la variable dans le fichier ERA5 reçu
    era5_file: tuple
    #: Conversion de la valeur ERA5 (clé de app/services/climate_sources.CONVERSIONS)
    era5_convert: str
    #: Nom dans la demande CMIP6
    cmip6: str
    #: Nom de la variable dans le fichier CMIP6 reçu
    cmip6_file: tuple
    #: Conversion de la valeur CMIP6
    cmip6_convert: str
    #: Variable marine : lue à la mer la plus proche du lieu
    sea: bool = False
    #: Statistique quotidienne ERA5 (jeu « daily statistics »), au lieu du jeu mensuel
    era5_daily_statistic: str | None = None


#: Les variables de la projection climatique
CLIMATE_VARIABLES: list[ClimateVariable] = [
    ClimateVariable(
        "precipitation", "Précipitations", "mm/mois",
        "total_precipitation", ("tp",), "era5_precip_mm",
        "precipitation", ("pr",), "cmip6_precip_mm",
    ),
    ClimateVariable(
        "air_temperature", "Température de l'air", "°C",
        "2m_temperature", ("t2m",), "kelvin_to_c",
        "near_surface_air_temperature", ("tas",), "kelvin_to_c",
    ),
    ClimateVariable(
        "air_temperature_max", "Température maximale (pour ET₀)", "°C",
        "2m_temperature", ("t2m",), "kelvin_to_c",
        "daily_maximum_near_surface_air_temperature", ("tasmax",), "kelvin_to_c",
        era5_daily_statistic="daily_maximum",
    ),
    ClimateVariable(
        "air_temperature_min", "Température minimale (pour ET₀)", "°C",
        "2m_temperature", ("t2m",), "kelvin_to_c",
        "daily_minimum_near_surface_air_temperature", ("tasmin",), "kelvin_to_c",
        era5_daily_statistic="daily_minimum",
    ),
    ClimateVariable(
        "land_surface_temperature", "Température de surface de la terre", "°C",
        "skin_temperature", ("skt",), "kelvin_to_c",
        "surface_temperature", ("ts",), "kelvin_to_c",
    ),
    ClimateVariable(
        "sea_surface_temperature", "Température de surface de la mer", "°C",
        "sea_surface_temperature", ("sst",), "kelvin_to_c",
        "sea_surface_temperature", ("tos",), "identity",
        sea=True,
    ),
]


def middle_year() -> int:
    """Une année au milieu de la période : utile pour tester les projections."""
    return (START_YEAR + END_YEAR) // 2