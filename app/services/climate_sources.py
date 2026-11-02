"""
Accès aux données climatiques de Copernicus (ECMWF) : construction des
demandes, téléchargement, lecture des fichiers NetCDF et conversion des
unités. Tous les réglages viennent de app/core/climate_config.py.

Utilisé par le script de vérification, et par le futur module de
projection climatique.
"""

import calendar
import zipfile
from pathlib import Path
from typing import Callable

from app.core import climate_config as cfg
from app.core.climate_config import ClimateVariable

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
    return cfg.ERA5_MONTHLY, {
        "product_type": ["monthly_averaged_reanalysis"],
        "variable": [var.era5],
        "year": [str(year)],
        "month": month_list,
        "time": ["00:00"],
        "area": area(lat, lon),
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def cmip6_request(
    var: ClimateVariable, model: str, scenario: str, year: int, months: list[int], lat: float, lon: float
) -> tuple[str, dict]:
    """Jeu de données et demande CMIP6 pour une variable, un modèle et un scénario."""
    return cfg.CMIP6, {
        "temporal_resolution": "monthly",
        "experiment": scenario,
        "variable": var.cmip6,
        "model": model,
        "year": [str(year)],
        "month": [f"{m:02d}" for m in months],
        "area": area(lat, lon),
    }


def make_client():
    """Client Copernicus, avec la clé lue dans .env."""
    if not cfg.CDS_KEY:
        raise RuntimeError("CDSAPI_KEY absente du fichier .env : ajoutez CDSAPI_URL et CDSAPI_KEY.")
    import cdsapi

    return cdsapi.Client(url=cfg.CDS_URL, key=cfg.CDS_KEY, quiet=True, progress=False)


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


def values_at(files: list[Path], names: tuple, lat: float, lon: float, sea: bool) -> list[tuple[int, int, float]]:
    """
    Valeurs brutes (année, mois, valeur) au lieu : la maille la plus proche,
    ou, pour une variable marine, la maille de MER la plus proche.
    """
    import numpy as np
    import xarray as xr

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

        results = []
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
            raw = float(data.flat[int(np.argmin(dist))])
            stamp = np.datetime64(t, "M").astype(object)
            results.append((stamp.year, stamp.month, raw))
        ds.close()
        if results:
            return results
    raise ValueError(f"variable {names} absente des fichiers reçus")


def retrieve_values(
    client, dataset: str, request: dict, file_names: tuple, convert: str, sea: bool, lat: float, lon: float, workdir: Path
) -> list[tuple[int, int, float]]:
    """
    Télécharge une demande et renvoie les valeurs CONVERTIES au lieu :
    (année, mois, valeur en °C ou en mm). Lève l'erreur de Copernicus
    telle quelle si la demande est refusée.
    """
    target = workdir / f"{abs(hash((dataset, repr(sorted(request.items())))))}.download"
    client.retrieve(dataset, request, str(target))
    to_unit = CONVERSIONS[convert]
    return [(y, m, to_unit(v, y, m)) for y, m, v in values_at(netcdf_files(target, workdir), file_names, lat, lon, sea)]