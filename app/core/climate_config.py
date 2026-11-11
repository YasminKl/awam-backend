"""
Réglages de la projection climatique, réunis en UN SEUL endroit.

Chaque réglage a une valeur par défaut, et peut être changé dans le fichier
.env du back, sans modifier le code. Le script de vérification, le service
et les tâches Celery du climat lisent tous ces réglages.

Exemples, dans .env :

    CLIMATE_START_YEAR=2020
    CLIMATE_END_YEAR=2040
    CLIMATE_BASELINE_START=2015
    CLIMATE_BASELINE_END=2025
    CLIMATE_SLIDING_YEARS=11
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
    return [v.strip() for v in value.split(",") if v.strip()] if value else default


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

#: Période de référence de la correction des biais : 2015-2025, 11 ans.
#: Compromis retenu pour réduire les demandes à Copernicus : ces années sont
#: couvertes par les simulations des scénarios (qui commencent en 2015), il
#: n'y a donc rien à demander à la simulation « historical » (5 demandes de
#: moins). Le GIEC (6e rapport) utilise 1995-2014, 20 ans : 11 ans suffisent
#: à lisser les années extrêmes, mais la correction de la pluie est moins
#: robuste qu'avec 20 ans.
BASELINE_START = _int("CLIMATE_BASELINE_START", 2015)
BASELINE_END = _int("CLIMATE_BASELINE_END", 2025)

#: Recalcul annuel de la correction : sur les N dernières années complètes
#: (fenêtre glissante), pour tenir compte du climat réel le plus récent.
#: Même durée que la référence : les projections sont gardées depuis 2015.
SLIDING_YEARS = _int("CLIMATE_SLIDING_YEARS", 11)

#: Dernière année des simulations « historical » de CMIP6 ; ensuite, les
#: modèles suivent les scénarios
CMIP6_HISTORICAL_END = 2014

if START_YEAR > END_YEAR:
    raise ValueError("CLIMATE_START_YEAR doit précéder CLIMATE_END_YEAR")
if BASELINE_START > BASELINE_END:
    raise ValueError("CLIMATE_BASELINE_START doit précéder CLIMATE_BASELINE_END")
if SLIDING_YEARS < 5:
    raise ValueError("CLIMATE_SLIDING_YEARS doit valoir au moins 5")

# ----------------------------------------------------------------------
# Projections
# ----------------------------------------------------------------------

#: Scénarios d'émissions : intermédiaire et pessimiste par défaut
SCENARIOS = _list("CLIMATE_SCENARIOS", ["ssp2_4_5", "ssp5_8_5"])

#: Modèles climatiques CMIP6, validés avec le script de vérification
MODELS = _list("CLIMATE_MODELS", ["ec_earth3_cc"])

#: Interprétation des périodes : en dessous de cet écart (en %) avec la
#: période réelle, une valeur est dite « stable ». Choix de présentation,
#: pour ne pas annoncer une baisse pour quelques millimètres
STABLE_PCT = _float("CLIMATE_STABLE_PCT", 5.0)

#: Durée (années) des périodes comparées sur la page
PERIOD_YEARS = _int("CLIMATE_PERIOD_YEARS", 5)

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

    #: Clé interne : le nom de la colonne de climate_monthly
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
    #: Correction des biais : « add » (écart, températures) ou « mul » (rapport, pluie)
    correction: str = "add"
    #: Le climat réel de cette variable vient de weather_daily (Open-Meteo),
    #: et non d'ERA5 : il n'est pas recopié dans climate_monthly
    from_weather_daily: bool = False
    #: Projection CMIP6 non demandée : calculée à partir des températures
    #: maximale et minimale, (max + min) / 2, une demande de moins par période
    cmip6_from_minmax: bool = False


#: Les variables de la projection climatique
CLIMATE_VARIABLES: list[ClimateVariable] = [
    ClimateVariable(
        "precipitation_mm", "Précipitations", "mm/mois",
        "total_precipitation", ("tp",), "era5_precip_mm",
        "precipitation", ("pr",), "cmip6_precip_mm",
        correction="mul", from_weather_daily=True,
    ),
    ClimateVariable(
        "air_temperature_c", "Température de l'air", "°C",
        "2m_temperature", ("t2m",), "kelvin_to_c",
        "near_surface_air_temperature", ("tas",), "kelvin_to_c",
        from_weather_daily=True, cmip6_from_minmax=True,
    ),
    ClimateVariable(
        "air_temperature_max_c", "Température maximale (pour ET₀)", "°C",
        "2m_temperature", ("t2m",), "kelvin_to_c",
        "daily_maximum_near_surface_air_temperature", ("tasmax",), "kelvin_to_c",
        era5_daily_statistic="daily_maximum", from_weather_daily=True,
    ),
    ClimateVariable(
        "air_temperature_min_c", "Température minimale (pour ET₀)", "°C",
        "2m_temperature", ("t2m",), "kelvin_to_c",
        "daily_minimum_near_surface_air_temperature", ("tasmin",), "kelvin_to_c",
        era5_daily_statistic="daily_minimum", from_weather_daily=True,
    ),
    ClimateVariable(
        "land_surface_temperature_c", "Température de surface de la terre", "°C",
        "skin_temperature", ("skt",), "kelvin_to_c",
        "surface_temperature", ("ts",), "kelvin_to_c",
    ),
    ClimateVariable(
        "sea_surface_temperature_c", "Température de surface de la mer", "°C",
        "sea_surface_temperature", ("sst",), "kelvin_to_c",
        "sea_surface_temperature", ("tos",), "identity",
        sea=True,
    ),
]

#: Variables dont le climat réel vient d'ERA5 (et non de weather_daily)
ERA5_STORED_VARIABLES = [v for v in CLIMATE_VARIABLES if not v.from_weather_daily]

#: Variables demandées à CMIP6 (la température moyenne est calculée)
CMIP6_DOWNLOADED_VARIABLES = [v for v in CLIMATE_VARIABLES if not v.cmip6_from_minmax]


def middle_year() -> int:
    """Une année au milieu de la période : utile pour tester les projections."""
    return (START_YEAR + END_YEAR) // 2