"""
Accès aux données climatiques de Copernicus (ECMWF) : construction des
demandes, téléchargement, lecture des fichiers NetCDF et conversion des
unités. Tous les réglages viennent de app/core/climate_config.py.

Utilisé par le script de vérification et par le service de projection
climatique.
"""

import calendar
import logging
import re
import zipfile
from pathlib import Path
from typing import Callable

from app.core import climate_config as cfg
from app.core.climate_config import ClimateVariable

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Conversions d'unités
# ----------------------------------------------------------------------


def _kelvin_to_c(v: float, _y: int, _m: int) -> float:
    return v - 273.15


def _era5_precip_mm(v: float, y: int, m: int) -> float:
    """ERA5 mensuel : mètres d'eau par jour (moyenne du mois) → mm sur le mois."""
    return v * 1000 * calendar.monthrange(y, m)[1]


def _cmip6_precip_mm(v: float, y: int, m: int) -> float:
    """CMIP6 : flux en kg m-2 s-1 → mm sur le mois."""
    return v * 86400 * calendar.monthrange(y, m)[1]


def _identity(v: float, _y: int, _m: int) -> float:
    return v


#: Conversions, désignées par leur nom dans la configuration
CONVERSIONS: dict[str, Callable[[float, int, int], float]] = {
    "kelvin_to_c": _kelvin_to_c,
    "era5_precip_mm": _era5_precip_mm,
    "cmip6_precip_mm": _cmip6_precip_mm,
    "identity": _identity,
}

ALL_MONTHS = list(range(1, 13))

# ----------------------------------------------------------------------
# Demandes
# ----------------------------------------------------------------------


def area(lat: float, lon: float) -> list[float]:
    """Zone [nord, ouest, sud, est] autour du lieu."""
    m = cfg.AREA_MARGIN
    return [round(lat + m, 2), round(lon - m, 2), round(lat - m, 2), round(lon + m, 2)]


def era5_request(var: ClimateVariable, year: int, months: list[int], lat: float, lon: float) -> tuple[str, dict]:
    """Jeu de données et demande ERA5 pour une variable, une année et des mois."""
    month_list = [f"{m:02d}" for m in months]
    if var.era5_daily_statistic:
        # Statistique quotidienne (ex. maximum du jour) : le 15 de chaque mois
        return cfg.ERA5_DAILY_STATS, {
            "product_type": "reanalysis",
            "variable": [var.era5],
            "year": str(year),
            "month": month_list,
            "day": ["15"],
            "daily_statistic": var.era5_daily_statistic,
            "time_zone": "utc+01:00",
            "frequency": "1_hourly",
            "area": area(lat, lon),
        }
    return era5_monthly_request(var, [year], months, lat, lon)


def era5_monthly_request(
    var: ClimateVariable, years: list[int], months: list[int], lat: float, lon: float
) -> tuple[str, dict]:
    """Demande ERA5 MENSUELLE sur plusieurs années d'un coup."""
    return cfg.ERA5_MONTHLY, {
        "product_type": ["monthly_averaged_reanalysis"],
        "variable": [var.era5],
        "year": [str(y) for y in years],
        "month": [f"{m:02d}" for m in months],
        "time": ["00:00"],
        "area": area(lat, lon),
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def cmip6_request(
    var: ClimateVariable, model: str, scenario: str, year: int, months: list[int], lat: float, lon: float
) -> tuple[str, dict]:
    """Jeu de données et demande CMIP6 pour une variable, un modèle et un scénario."""
    return cmip6_years_request(var, model, scenario, [year], months, lat, lon)


def cmip6_years_request(
    var: ClimateVariable, model: str, experiment: str, years: list[int], months: list[int], lat: float, lon: float
) -> tuple[str, dict]:
    """
    Demande CMIP6 sur plusieurs années d'un coup. `experiment` est un
    scénario (ex. « ssp2_4_5 ») ou « historical » (simulation du passé,
    jusqu'en 2014).
    """
    return cfg.CMIP6, {
        "temporal_resolution": "monthly",
        "experiment": experiment,
        "variable": var.cmip6,
        "model": model,
        "year": [str(y) for y in years],
        "month": [f"{m:02d}" for m in months],
        "area": area(lat, lon),
    }


#: États d'une demande chez Copernicus, en mots simples
_CDS_STATES = {
    "accepted": "demande en file d'attente…",
    "queued": "demande en file d'attente…",
    "running": "demande en cours de traitement…",
    "successful": "demande traitée, téléchargement du fichier…",
    "failed": "demande refusée",
    "rejected": "demande refusée",
}

_STATE_RE = re.compile(r"status has been updated to (\w+)", re.IGNORECASE)


def _cds_info(*args) -> None:
    """Messages d'état de Copernicus, affichés en français dans le terminal."""
    message = " ".join(str(a) for a in args)
    match = _STATE_RE.search(message)
    if match:
        state = match.group(1).lower()
        logger.info("           Copernicus : %s", _CDS_STATES.get(state, state))


def _cds_warning(*args) -> None:
    logger.warning("           Copernicus : %s", " ".join(str(a) for a in args))


def make_client():
    """
    Client Copernicus, avec la clé lue dans .env. Ses messages d'état (file
    d'attente, traitement…) sont affichés en français dans le terminal ;
    pas de barre de progression.
    """
    if not cfg.CDS_KEY:
        raise RuntimeError("CDSAPI_KEY absente du fichier .env : ajoutez CDSAPI_URL et CDSAPI_KEY.")
    import cdsapi

    return cdsapi.Client(
        url=cfg.CDS_URL, key=cfg.CDS_KEY, quiet=False, progress=False,
        info_callback=_cds_info, warning_callback=_cds_warning,
    )

# ----------------------------------------------------------------------
# Lecture des fichiers reçus
# ----------------------------------------------------------------------


def netcdf_files(path: Path, workdir: Path) -> list[Path]:
    """Le fichier lui-même, ou les NetCDF d'une archive zip (cas de CMIP6)."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.endswith(".nc")]
            archive.extractall(workdir, members=names)
        return [workdir / n for n in names]
    return [path]


def _coord(da, *candidates: str) -> str:
    for name in candidates:
        if name in da.coords or name in da.dims:
            return name
    raise KeyError(f"coordonnée introuvable parmi {candidates}")


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance à vol d'oiseau entre deux points (formule de haversine)."""
    import math

    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def values_at(
    files: list[Path], names: tuple, lat: float, lon: float, sea: bool, meta: dict | None = None
) -> list[tuple[int, int, float]]:
    """
    Valeurs brutes (année, mois, valeur) au lieu : la maille la plus proche,
    ou, pour une variable marine, la maille de MER la plus proche. Plusieurs
    fichiers (un par période, chez CMIP6) sont réunis. Si `meta` est donné,
    il reçoit la distance (km) de la maille retenue : « distance_km ».
    """
    import numpy as np
    import xarray as xr

    results: dict[tuple[int, int], float] = {}
    for path in files:
        ds = xr.open_dataset(path)
        var = next((n for n in names if n in ds.data_vars), None)
        if var is None:
            ds.close()
            continue
        da = ds[var]
        lat_name = _coord(da, "latitude", "lat")
        lon_name = _coord(da, "longitude", "lon")
        time_name = _coord(da, "valid_time", "time")
        lon_query = lon % 360 if float(da[lon_name].max()) > 180 else lon

        for t in da[time_name].values:
            field = da.sel({time_name: t}).squeeze(drop=True)
            # Grille rectangulaire : latitude et longitude à une dimension.
            # Sinon, grille courbe (fréquente pour l'océan) : coordonnées 2D.
            if da[lat_name].ndim == 1:
                grid_lat, grid_lon = np.meshgrid(field[lat_name].values, field[lon_name].values, indexing="ij")
                data = field.transpose(lat_name, lon_name).values
            else:
                grid_lat, grid_lon = ds[lat_name].values, ds[lon_name].values
                data = field.values
            dist = (grid_lat - lat) ** 2 + (grid_lon - lon_query) ** 2
            if sea or da[lat_name].ndim != 1:
                # Seulement les mailles qui ont une valeur : la mer, pour la
                # température de la mer
                dist = np.where(np.isnan(data), np.inf, dist)
            if not np.isfinite(dist).any():
                continue
            index = int(np.argmin(dist))
            raw = float(data.flat[index])
            if meta is not None and "distance_km" not in meta:
                cell_lat, cell_lon = float(grid_lat.flat[index]), float(grid_lon.flat[index])
                if cell_lon > 180:
                    cell_lon -= 360
                meta["distance_km"] = round(distance_km(lat, lon, cell_lat, cell_lon), 1)
            stamp = np.datetime64(t, "M").astype(object)
            results[(stamp.year, stamp.month)] = raw
        ds.close()
    if not results:
        raise ValueError(f"variable {names} absente des fichiers reçus")
    return [(y, m, v) for (y, m), v in sorted(results.items())]


def retrieve_values(
    client, dataset: str, request: dict, file_names: tuple, convert: str, sea: bool, lat: float, lon: float, workdir: Path,
    meta: dict | None = None,
) -> list[tuple[int, int, float]]:
    """
    Télécharge une demande et renvoie les valeurs CONVERTIES au lieu :
    (année, mois, valeur en °C ou en mm). Lève l'erreur de Copernicus
    telle quelle si la demande est refusée.
    """
    target = workdir / f"{abs(hash((dataset, repr(sorted(request.items())))))}.download"
    client.retrieve(dataset, request, str(target))
    to_unit = CONVERSIONS[convert]
    return [
        (y, m, to_unit(v, y, m))
        for y, m, v in values_at(netcdf_files(target, workdir), file_names, lat, lon, sea, meta)
    ]