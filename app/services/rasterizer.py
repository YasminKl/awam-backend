"""
Rasterisation d'une parcelle en COG colorisé (masque de contour).

Le masque sert d'aperçu visuel de la forme de la parcelle sur la carte.
Une seule parcelle = une seule couleur.

100 % Python via rasterio + rio-cogeo (pas de binaires GDAL).
"""

import os
import tempfile
import logging

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.b2_storage import upload_file_to_b2
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles

logger = logging.getLogger(__name__)

# Couleur unique du masque (vert forêt)
MASK_COLOR = (34, 139, 34)

# Résolution cible en degrés (≈ 11 m à l'équateur)
TARGET_RES = 0.0001

# Plafond de sécurité pour éviter les OOM sur de très grandes parcelles
MAX_DIMENSION = 4000

COG_PROFILE = cog_profiles.get("deflate")
COG_PROFILE.update({"BIGTIFF": "IF_SAFER"})


# ----------------------------------------------------------------------
# Helpers PostGIS
# ----------------------------------------------------------------------

def _get_parcel_geometry(db: Session, parcel_id: int) -> dict | None:
    """Géométrie GeoJSON de la parcelle, reprojetée en EPSG:4326."""
    row = db.execute(
        text("""
            SELECT ST_AsGeoJSON(ST_Transform(geom, 4326))::json AS geometry
            FROM parcel
            WHERE id = :parcel_id
        """),
        {"parcel_id": parcel_id},
    ).fetchone()
    return row.geometry if row else None


def _get_bbox_for_parcel(db: Session, parcel_id: int) -> tuple[float, float, float, float] | None:
    """Bbox (west, south, east, north) en EPSG:4326 de la parcelle."""
    row = db.execute(
        text("""
            SELECT
                ST_XMin(geom) AS west, ST_YMin(geom) AS south,
                ST_XMax(geom) AS east, ST_YMax(geom) AS north
            FROM parcel
            WHERE id = :parcel_id
        """),
        {"parcel_id": parcel_id},
    ).fetchone()
    if not row or row.west is None:
        return None
    return (row.west, row.south, row.east, row.north)


# ----------------------------------------------------------------------
# Calcul de la grille (dimensions + transform)
# ----------------------------------------------------------------------

def _compute_grid(bbox: tuple[float, float, float, float]) -> tuple[int, int, rasterio.Affine]:
    """
    Calcule la grille (width, height, transform) pour la bbox.

    Si la grille dépasse MAX_DIMENSION, augmente la résolution
    pour rester sous le plafond.
    """
    west, south, east, north = bbox
    res = TARGET_RES

    width = max(1, int(round((east - west) / res)))
    height = max(1, int(round((north - south) / res)))

    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        scale = max(width / MAX_DIMENSION, height / MAX_DIMENSION)
        res = res * scale
        width = max(1, int(round((east - west) / res)))
        height = max(1, int(round((north - south) / res)))
        logger.warning(
            "[rasterizer] Emprise trop grande : résolution ajustée à %.6f° (%dx%d)",
            res, width, height,
        )

    transform = from_bounds(west, south, east, north, width, height)
    return width, height, transform


# ----------------------------------------------------------------------
# Rasterisation
# ----------------------------------------------------------------------

def _rasterize_geometry(
    geometry: dict,
    bbox: tuple[float, float, float, float],
) -> tuple[np.ndarray, rasterio.Affine]:
    """
    Rasterise une géométrie (GeoJSON) en un tableau d'indices uint8.

    Équivalent de gdal_rasterize avec un seul feature.
    """
    width, height, transform = _compute_grid(bbox)

    # Valeur 1 = parcelle, 0 = nodata
    shapes = [(geometry, 1)]

    arr = rasterize(
        shapes=shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    )
    return arr, transform


# ----------------------------------------------------------------------
# Colorisation (RGBA)
# ----------------------------------------------------------------------

def _colorize(arr: np.ndarray) -> np.ndarray:
    """
    Colorise un tableau d'indices (0/1) en RGBA.
    Index 0 = nodata transparent, index 1 = couleur du masque.
    """
    height, width = arr.shape
    rgba = np.zeros((4, height, width), dtype=np.uint8)

    mask = arr == 1
    rgba[0][mask] = MASK_COLOR[0]
    rgba[1][mask] = MASK_COLOR[1]
    rgba[2][mask] = MASK_COLOR[2]
    rgba[3][mask] = 255

    return rgba


# ----------------------------------------------------------------------
# Écriture COG
# ----------------------------------------------------------------------

def _write_rgba_cog(
    arr: np.ndarray,
    transform: rasterio.Affine,
    dst_path: str,
) -> None:
    """Écrit un tableau RGBA (4, H, W) en COG."""
    tmp_tif = dst_path.replace(".tif", "_tmp.tif")
    height, width = arr.shape[1], arr.shape[2]

    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 4,
        "dtype": "uint8",
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }

    with rasterio.open(tmp_tif, "w", **profile) as dst:
        dst.write(arr)

    cog_translate(tmp_tif, dst_path, COG_PROFILE, in_memory=False, quiet=True)

    if os.path.exists(tmp_tif):
        os.remove(tmp_tif)


# ----------------------------------------------------------------------
# Point d'entrée public
# ----------------------------------------------------------------------

def rasterize_parcel(
    db: Session,
    farm_id: int,
    parcel_id: int,
) -> dict | None:
    """
    Génère un masque COG colorisé pour UNE parcelle et l'upload sur B2.

    Retourne :
        {
            "url": "https://...",
            "key": "rasters/farm_X/parcel_Y/mask/mask.tif",
            "bbox": (w, s, e, n),
        }
    ou None si la parcelle n'a pas de géométrie.
    """
    # 1. Récupérer la géométrie
    geometry = _get_parcel_geometry(db, parcel_id)
    if not geometry:
        logger.warning("[rasterizer] Pas de géométrie pour parcel_id=%s", parcel_id)
        return None

    bbox = _get_bbox_for_parcel(db, parcel_id)
    if bbox is None:
        logger.warning("[rasterizer] Pas de bbox pour parcel_id=%s", parcel_id)
        return None

    logger.info(
        "[rasterizer] parcel_id=%s : bbox=%s, geom_type=%s",
        parcel_id, bbox, geometry.get("type"),
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        cog_path = os.path.join(tmpdir, "mask.tif")

        # 2. Rasteriser
        arr_idx, transform = _rasterize_geometry(geometry, bbox)
        logger.info(
            "[rasterizer] Rasterisé : shape=%s, unique=%s",
            arr_idx.shape, np.unique(arr_idx).tolist(),
        )

        # 3. Coloriser (RGBA)
        arr_rgba = _colorize(arr_idx)

        # 4. Écrire le COG
        _write_rgba_cog(arr_rgba, transform, cog_path)

        # 5. Upload B2 (clé fixe : une seule version par parcelle)
        key = f"rasters/farm_{farm_id}/parcel_{parcel_id}/mask/mask.tif"
        url = upload_file_to_b2(cog_path, key)

    logger.info("[rasterizer] ✅ COG uploadé : %s", key)
    return {"url": url, "key": key, "bbox": bbox}
