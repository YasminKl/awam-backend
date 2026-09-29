"""
Statistiques zonales sur le polygone exact d'une parcelle.

Contrairement à une simple bbox, on masque le raster sur la géométrie
exacte (polygone) de la parcelle pour ne pas inclure les pixels voisins.

Utilise rasterio.mask + shapely → déjà des dépendances du pipeline existant.

Ce module est PUR (pas de DB, pas d'I/O B2). Il reçoit :
  - un np.ndarray de l'indice calculé
  - le transform + crs du raster
  - la géométrie de la parcelle (WKT ou GeoJSON)
et retourne un dict de stats.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform

from app.services.indices.helpers import NODATA_VALUE

logger = logging.getLogger(__name__)


# ======================================================================
# Helpers internes
# ======================================================================


def _parse_geometry(geometry: Any) -> dict:
    """
    Normalise une géométrie en dict GeoJSON.

    Accepte :
      - dict GeoJSON direct : {"type": "Polygon", "coordinates": [...]}
      - str WKT            : "POLYGON((...))"
      - objet shapely      : Polygon / MultiPolygon

    Returns:
        dict GeoJSON de type Polygon ou MultiPolygon

    Raises:
        ValueError si le format n'est pas reconnu.
    """
    if isinstance(geometry, dict) and "type" in geometry:
        return geometry

    if isinstance(geometry, str):
        try:
            from shapely import wkt as shapely_wkt
            geom = shapely_wkt.loads(geometry)
            return geom.__geo_interface__
        except Exception as e:
            raise ValueError(f"WKT invalide : {e}") from e

    if hasattr(geometry, "__geo_interface__"):
        return geometry.__geo_interface__

    raise ValueError(
        f"Format de géométrie non supporté : {type(geometry)}. "
        "Attendu : dict GeoJSON, str WKT, ou shapely.geometry."
    )


def _reproject_geometry_to_raster_crs(
    geometry: dict,
    raster_crs: rasterio.crs.CRS,
) -> dict:
    """
    Reprojette une géométrie (supposée en EPSG:4326) vers le CRS du raster.

    Si raster_crs est déjà EPSG:4326, aucun changement.
    """
    if raster_crs is None:
        return geometry

    # EPSG:4326 → rastar CRS
    try:
        from pyproj import Transformer
    except ImportError:
        logger.warning("[zonal_stats] pyproj non disponible, pas de reprojection")
        return geometry

    transformer = Transformer.from_crs(
        "EPSG:4326", raster_crs, always_xy=True
    )

    geom_shapely = shape(geometry)
    geom_reproj = shapely_transform(transformer.transform, geom_shapely)
    return geom_reproj.__geo_interface__


# ======================================================================
# Fonction principale
# ======================================================================


def compute_zonal_stats(
    arr: np.ndarray,
    transform: Affine,
    raster_crs: rasterio.crs.CRS,
    parcel_geometry: Any,
    *,
    cloud_mask: np.ndarray | None = None,
) -> dict:
    """
    Calcule les stats zonales sur le polygone exact d'une parcelle.

    Args:
        arr:             np.ndarray float32 de l'indice (avec NaN sur pixels
                         invalides : nuages, nodata, SCL masqué)
        transform:       Affine transform du raster
        raster_crs:      CRS du raster
        parcel_geometry: géométrie de la parcelle (GeoJSON dict, WKT, ou shapely)
                         supposée en EPSG:4326
        cloud_mask:      masque booléen optionnel (True = pixel nuageux)
                         pour distinguer cloud_pixels de valid_pixels

    Returns:
        dict avec :
          - total_pixels     : nombre total de pixels dans la parcelle
          - valid_pixels     : pixels avec valeur finie (non-NaN, non-cloud)
          - cloud_pixels     : pixels masqués par SCL (si cloud_mask fourni)
          - valid_ratio      : valid_pixels / total_pixels (0 à 1)
          - mean, min, max, std, median : stats sur les pixels valides
          (None si valid_pixels == 0)
    """
    # ------------------------------------------------------------------
    # 1. Normaliser + reprojeter la géométrie
    # ------------------------------------------------------------------
    geometry_geojson = _parse_geometry(parcel_geometry)
    geometry_geojson = _reproject_geometry_to_raster_crs(
        geometry_geojson, raster_crs
    )

    # ------------------------------------------------------------------
    # 2. Construire le masque binaire (True = DANS la parcelle)
    # ------------------------------------------------------------------
    try:
        inside_mask = geometry_mask(
            [geometry_geojson],
            out_shape=arr.shape,
            transform=transform,
            invert=True,  # True = DANS la géométrie
            all_touched=False,  # pixel inclus seulement si centre dedans
        )
    except Exception as e:
        logger.exception("[zonal_stats] Échec de geometry_mask")
        raise ValueError(f"Impossible de masquer la géométrie : {e}") from e

    total_pixels = int(inside_mask.sum())
    if total_pixels == 0:
        logger.warning(
            "[zonal_stats] Aucun pixel de la parcelle dans l'emprise du raster"
        )
        return {
            "total_pixels": 0,
            "valid_pixels": 0,
            "cloud_pixels": 0,
            "valid_ratio": 0.0,
            "mean": None,
            "min": None,
            "max": None,
            "std": None,
            "median": None,
        }

    # ------------------------------------------------------------------
    # 3. Extraire les valeurs dans la parcelle
    # ------------------------------------------------------------------
    values_inside = arr[inside_mask]

    # Pixels valides = finis (non-NaN) ET non masqués par les nuages
    finite_mask = np.isfinite(values_inside)

    if cloud_mask is not None:
        cloud_inside = cloud_mask[inside_mask]
        # Un pixel "valide" est fini ET pas nuage
        valid_mask = finite_mask & ~cloud_inside
        cloud_pixels = int(cloud_inside.sum())
    else:
        valid_mask = finite_mask
        cloud_pixels = 0

    valid_pixels = int(valid_mask.sum())
    valid_values = values_inside[valid_mask]

    # ------------------------------------------------------------------
    # 4. Calcul des stats
    # ------------------------------------------------------------------
    if valid_pixels == 0:
        return {
            "total_pixels": total_pixels,
            "valid_pixels": 0,
            "cloud_pixels": cloud_pixels,
            "valid_ratio": 0.0,
            "mean": None,
            "min": None,
            "max": None,
            "std": None,
            "median": None,
        }

    stats = {
        "total_pixels": total_pixels,
        "valid_pixels": valid_pixels,
        "cloud_pixels": cloud_pixels,
        "valid_ratio": float(valid_pixels) / float(total_pixels),
        "mean": float(np.mean(valid_values)),
        "min": float(np.min(valid_values)),
        "max": float(np.max(valid_values)),
        "std": float(np.std(valid_values)),
        "median": float(np.median(valid_values)),
    }
    return stats


# ======================================================================
# Wrapper pratique : stats + validation bornes
# ======================================================================


def compute_zonal_stats_with_validation(
    arr: np.ndarray,
    transform: Affine,
    raster_crs: rasterio.crs.CRS,
    parcel_geometry: Any,
    theoretical_min: float,
    theoretical_max: float,
    *,
    cloud_mask: np.ndarray | None = None,
) -> dict:
    """
    Comme compute_zonal_stats, mais ajoute la validation par rapport
    aux bornes théoriques de l'indice.

    Returns:
        dict avec les stats + :
          - is_valid          : True si toutes les valeurs sont dans les bornes
          - validation_error  : message si is_valid=False
    """
    stats = compute_zonal_stats(
        arr=arr,
        transform=transform,
        raster_crs=raster_crs,
        parcel_geometry=parcel_geometry,
        cloud_mask=cloud_mask,
    )

    is_valid = True
    validation_error: str | None = None

    # On ne valide que si on a des stats
    if stats["valid_pixels"] > 0:
        mn = stats["min"]
        mx = stats["max"]

        out_of_bounds = []
        if mn is not None and mn < theoretical_min - 1e-6:
            out_of_bounds.append(f"min={mn:.4f} < {theoretical_min}")
        if mx is not None and mx > theoretical_max + 1e-6:
            out_of_bounds.append(f"max={mx:.4f} > {theoretical_max}")

        if out_of_bounds:
            is_valid = False
            validation_error = " ; ".join(out_of_bounds)

    stats["is_valid"] = is_valid
    stats["validation_error"] = validation_error
    return stats


# ======================================================================
# Helper pour PostGIS → WKT
# ======================================================================


def fetch_parcel_geometry_wkt(db, parcel_id: int) -> str | None:
    """
    Récupère la géométrie d'une parcelle au format WKT (EPSG:4326).

    Args:
        db:        session SQLAlchemy
        parcel_id: id de la parcelle

    Returns:
        WKT (str) ou None si parcelle introuvable / sans géométrie.
    """
    from sqlalchemy import text

    row = db.execute(
        text("""
            SELECT ST_AsText(ST_Transform(geom, 4326)) AS wkt
            FROM parcel
            WHERE id = :parcel_id
        """),
        {"parcel_id": parcel_id},
    ).fetchone()

    if row is None or row.wkt is None:
        return None
    return row.wkt