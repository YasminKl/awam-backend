"""
Service d'ingestion Sentinel-2 L2A (réflectance de surface), par parcelle.

Source : Microsoft Planetary Computer (STAC + COG), gratuit et sans
authentification pour la lecture.

Bandes Sentinel-2 utilisées (résolution 10 m) :
- B02 = Blue, B03 = Green, B04 = Red, B08 = NIR
- SCL = Scene Classification Layer (masque nuages/ombres/neige)

Formule NDVI = (NIR - Red) / (NIR + Red)

Améliorations de qualité :
- Étirement de contraste 1-99% + gamma correction (RGB plus lumineux)
- Masque nuages/ombres via la bande SCL
- Resampling bilinear pour les overviews et reprojections
- UPSAMPLING à 5 m (depuis 10 m natif) pour un rendu plus doux au zoom
- Compression DEFLATE optimisée (PREDICTOR=2, LEVEL=9)
"""

# ----------------------------------------------------------------------
# Imports standards
# ----------------------------------------------------------------------
import os
import uuid
import logging
import tempfile
import warnings
from datetime import datetime, timedelta

# ----------------------------------------------------------------------
# Imports tiers
# ----------------------------------------------------------------------
import numpy as np
import rasterio
import rioxarray
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from pystac_client import Client
import planetary_computer
from sqlalchemy import text
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Imports internes
# ----------------------------------------------------------------------
from app.services.b2_storage import upload_file_to_b2

# ----------------------------------------------------------------------
# Configuration logging & warnings
# ----------------------------------------------------------------------
# ✅ Réduire le bruit des warnings GDAL (rio-cogeo émet des CPLE_NotSupported
# pour les options COG passées au driver GTiff — c'est inoffensif).
logging.getLogger("rasterio._env").setLevel(logging.ERROR)

# ✅ Masquer les warnings STAC non bloquants (Planetary Computer ne supporte
# pas certains paramètres de `query` mais le filtre fonctionne côté serveur).
warnings.filterwarnings("ignore", message=".*DoesNotConformTo.*")
warnings.filterwarnings("ignore", message=".*CPLE_NotSupported.*")
warnings.filterwarnings("ignore", message=".*PREDICTOR option is ignored.*")

# ✅ Désactiver TOUS les loggers pystac
logging.getLogger("pystac").setLevel(logging.ERROR)
logging.getLogger("pystac_client").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# ✅ Désactiver TOUS les warnings (dernier recours)
warnings.simplefilter("ignore")

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
STAC_API_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"

BAND_RED = "B04"
BAND_GREEN = "B03"
BAND_BLUE = "B02"
BAND_NIR = "B08"
BAND_SCL = "SCL"

S2_REFLECTANCE_SCALE = 10000.0
DEFAULT_LOOKBACK_DAYS = 90
MAX_CLOUD_COVER = 20  # %

# ✅ Résolution cible après upsampling (en mètres, dans le CRS natif de la bande)
# 5.0 = upsampling 2x par rapport au 10m natif de Sentinel-2
# 2.5 = upsampling 4x (rendu encore plus lisse)
# None = pas d'upsampling (reste en 10m natif, fichiers plus petits)
TARGET_RESOLUTION_M = 5.0

# Valeurs SCL considérées comme "à masquer" (nuages, ombres, neige, saturés)
SCL_MASK_VALUES = {
    0,   # No data
    1,   # Saturated / defective
    3,   # Cloud shadows
    8,   # Cloud medium probability
    9,   # Cloud high probability
    10,  # Thin cirrus
    11,  # Snow / ice
}


# ----------------------------------------------------------------------
# Helpers PostGIS
# ----------------------------------------------------------------------


def _get_bbox_for_parcel(
    db: Session, parcel_id: int
) -> tuple[float, float, float, float] | None:
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
# Recherche STAC
# ----------------------------------------------------------------------


def _search_best_scene(
    bbox: tuple[float, float, float, float],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_cloud: int = MAX_CLOUD_COVER,
):
    """Cherche la scène Sentinel-2 L2A la moins nuageuse et la plus récente."""
    end = datetime.utcnow()
    start = end - timedelta(days=lookback_days)

    client = Client.open(STAC_API_URL)
    search = client.search(
        collections=[COLLECTION],
        bbox=bbox,
        datetime=f"{start.date()}/{end.date()}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
        limit=50,
    )

    items = list(search.items())
    if not items:
        logger.warning(
            "[Sentinel] Aucune scène < %d%% nuages, recherche élargie", max_cloud
        )
        search = client.search(
            collections=[COLLECTION],
            bbox=bbox,
            datetime=f"{start.date()}/{end.date()}",
            limit=20,
        )
        items = list(search.items())

    if not items:
        return None

    def sort_key(it):
        cloud = it.properties.get("eo:cloud_cover", 100)
        dt_str = it.properties.get("datetime", "")
        try:
            dt_ts = -datetime.fromisoformat(
                dt_str.replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            dt_ts = 0
        return (cloud, dt_ts)

    items.sort(key=sort_key)
    best = items[0]

    # Signature SAS obligatoire, sinon GDAL reçoit 409 sur les COG Sentinel-2
    best = planetary_computer.sign(best)
    return best


# ----------------------------------------------------------------------
# Lecture des bandes (avec upsampling)
# ----------------------------------------------------------------------


def _clip_band_to_bbox(
    item,
    band: str,
    bbox: tuple[float, float, float, float],
    target_resolution: float | None = TARGET_RESOLUTION_M,
):
    """
    Ouvre une bande (COG distant), la découpe sur la bbox,
    et applique un upsampling si target_resolution est défini.

    Args:
        item: STAC Item signé
        band: nom de la bande (ex: 'B04')
        bbox: (west, south, east, north) en EPSG:4326
        target_resolution: résolution cible en mètres (None = natif 10m)

    Returns:
        DataArray avec la bande découpée et potentiellement upsamplée
    """
    asset = item.assets[band]
    da = rioxarray.open_rasterio(asset.href, masked=True)

    left, bottom, right, top = transform_bounds(
        "EPSG:4326", da.rio.crs, *bbox, densify_pts=21
    )

    clipped = da.rio.clip_box(
        minx=left, miny=bottom, maxx=right, maxy=top, crs=da.rio.crs
    )

    # ✅ Upsampling si demandé
    if target_resolution is not None:
        try:
            upsampled = clipped.rio.reproject(
                clipped.rio.crs,
                resolution=target_resolution,
                resampling=Resampling.bilinear,
            )
            logger.info(
                "[Sentinel] %s : upsampling %s → %s (%.1fm)",
                band,
                clipped.shape[1:],
                upsampled.shape[1:],
                target_resolution,
            )
            return upsampled
        except Exception as e:
            logger.warning(
                "[Sentinel] Échec upsampling %s, utilisation native : %s",
                band,
                e,
            )
            return clipped

    return clipped


# ----------------------------------------------------------------------
# Traitement
# ----------------------------------------------------------------------


def _mask_clouds_with_scl(
    red: np.ndarray,
    green: np.ndarray,
    blue: np.ndarray,
    nir: np.ndarray,
    scl: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Masque les pixels nuageux/ombres/neige via la bande SCL."""
    mask = np.isin(scl, list(SCL_MASK_VALUES))
    masked_count = int(mask.sum())
    total = mask.size

    if masked_count > 0:
        logger.info(
            "[Sentinel] Masque SCL : %d/%d pixels masqués (%.1f%%)",
            masked_count,
            total,
            100.0 * masked_count / total,
        )
    else:
        logger.info("[Sentinel] Masque SCL : aucun pixel à masquer")

    red_m = np.where(mask, np.nan, red).astype("float32")
    green_m = np.where(mask, np.nan, green).astype("float32")
    blue_m = np.where(mask, np.nan, blue).astype("float32")
    nir_m = np.where(mask, np.nan, nir).astype("float32")
    return red_m, green_m, blue_m, nir_m


def _reflectance_to_uint8(arr: np.ndarray, gamma: float = 0.9) -> np.ndarray:
    """
    Convertit une réflectance Sentinel-2 (0..10000) en uint8 (0..255).

    - Étirement 1-99% (plus agressif que 2-98%)
    - Gamma correction (gamma < 1 éclaircit)
    - Gestion des NaN
    """
    valid = arr[np.isfinite(arr) & (arr > 0)]
    if valid.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)

    p1, p99 = np.percentile(valid, [1, 99])
    if p99 <= p1:
        p99 = p1 + 1

    scaled = (arr - p1) / (p99 - p1)
    scaled = np.clip(scaled, 0, 1)

    if gamma != 1.0:
        scaled = np.power(scaled, gamma)

    scaled = np.nan_to_num(scaled, nan=0.0, posinf=1.0, neginf=0.0)
    return (scaled * 255).astype(np.uint8)


def _compute_blocksize(height: int, width: int) -> int:
    """
    Calcule une taille de bloc adaptée à la taille du raster.

    Doit être :
      - Multiple de 16
      - Ne pas dépasser la taille du raster
      - Idéalement 512 pour les grands rasters, 256 pour les moyens
    """
    blocksize = 512
    if width < blocksize or height < blocksize:
        blocksize = 256
    if width < blocksize or height < blocksize:
        blocksize = min(width, height)
    blocksize = max(16, (blocksize // 16) * 16)
    return blocksize


# ----------------------------------------------------------------------
# Écriture COG
# ----------------------------------------------------------------------


def _write_rgb_cog(red, green, blue, transform, crs, dst_path: str) -> None:
    """
    Écrit un COG RGB (3 bandes uint8) avec compression optimisée.

    IMPORTANT : les options COG (BLOCKSIZE, LEVEL, PREDICTOR, OVERVIEW_RESAMPLING)
    ne doivent être passées QU'À cog_translate, PAS à rasterio.open().
    Sinon GDAL affiche des warnings "CPLE_NotSupported".
    """
    from rio_cogeo.cogeo import cog_translate
    from rio_cogeo.profiles import cog_profiles

    tmp_tif = dst_path.replace(".tif", "_tmp.tif")
    height, width = red.shape
    blocksize = _compute_blocksize(height, width)

    # Profil GTiff intermédiaire : SEULEMENT les options GTiff natives
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 3,
        "dtype": "uint8",
        "crs": crs,
        "transform": transform,
        "compress": "deflate",
        "predictor": 2,
        "tiled": True,
        "blockxsize": blocksize,
        "blockysize": blocksize,
    }

    with rasterio.open(tmp_tif, "w", **profile) as dst:
        dst.write(red, 1)
        dst.write(green, 2)
        dst.write(blue, 3)

    # Profil COG : options valides pour cog_translate
    cog_profile = cog_profiles.get("deflate")
    cog_profile.update({
        "BIGTIFF": "IF_SAFER",
        "BLOCKSIZE": blocksize,
        "OVERVIEW_RESAMPLING": "bilinear",
        "PREDICTOR": 2,
        "LEVEL": 9,
    })
    cog_translate(tmp_tif, dst_path, cog_profile, in_memory=False, quiet=True)

    if os.path.exists(tmp_tif):
        os.remove(tmp_tif)


def _write_single_band_cog(
    arr, transform, crs, dst_path: str, dtype: str = "float32"
) -> None:
    """
    Écrit un COG mono-bande avec compression optimisée.

    IMPORTANT : les options COG ne doivent être passées QU'À cog_translate.
    """
    from rio_cogeo.cogeo import cog_translate
    from rio_cogeo.profiles import cog_profiles

    tmp_tif = dst_path.replace(".tif", "_tmp.tif")
    height, width = arr.shape
    blocksize = _compute_blocksize(height, width)

    nodata_value = -9999.0
    arr_clean = np.where(np.isfinite(arr), arr, nodata_value).astype(dtype)

    # Profil GTiff intermédiaire
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": blocksize,
        "blockysize": blocksize,
        "nodata": nodata_value,
    }

    with rasterio.open(tmp_tif, "w", **profile) as dst:
        dst.write(arr_clean, 1)

    cog_profile = cog_profiles.get("deflate")
    cog_profile.update({
        "BIGTIFF": "IF_SAFER",
        "BLOCKSIZE": blocksize,
        "OVERVIEW_RESAMPLING": "bilinear",
    })
    cog_translate(tmp_tif, dst_path, cog_profile, in_memory=False, quiet=True)

    if os.path.exists(tmp_tif):
        os.remove(tmp_tif)


# ----------------------------------------------------------------------
# Point d'entrée public
# ----------------------------------------------------------------------


def generate_sentinel_composites_for_parcel(
    db: Session,
    farm_id: int,
    parcel_id: int,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    target_resolution: float | None = TARGET_RESOLUTION_M,
) -> dict | None:
    """
    Cherche la meilleure scène Sentinel-2 L2A récente pour l'emprise
    d'UNE parcelle, génère un composite RGB (COG) et un NDVI (COG),
    et les uploade sur B2.

    Args:
        target_resolution: Résolution cible en mètres (5.0 par défaut).
                          None = résolution native (10 m).

    Retourne :
      {
        "rgb_url": ..., "rgb_key": ...,
        "ndvi_url": ..., "ndvi_key": ...,
        "scene_date": "2024-07-15",
        "cloud_cover": 3.2,
        "scene_id": "S2B_MSIL2A_...",
        "bbox": [w, s, e, n],
      }
    ou None si aucune scène / aucune géométrie.
    """
    bbox = _get_bbox_for_parcel(db, parcel_id)
    if bbox is None:
        logger.info("[Sentinel] Parcelle %s sans géométrie", parcel_id)
        return None

    logger.info("[Sentinel] Bbox parcelle %s : %s", parcel_id, bbox)

    item = _search_best_scene(bbox, lookback_days=lookback_days)
    if item is None:
        logger.info("[Sentinel] Aucune scène trouvée pour parcelle %s", parcel_id)
        return None

    logger.info(
        "[Sentinel] Scène retenue : %s (clouds=%.1f%%)",
        item.id,
        item.properties.get("eo:cloud_cover", -1),
    )

    # ------------------------------------------------------------------
    # 1. Charger les 4 bandes + SCL (avec upsampling)
    # ------------------------------------------------------------------
    red_da = _clip_band_to_bbox(item, BAND_RED, bbox, target_resolution=TARGET_RESOLUTION_M)
    green_da = _clip_band_to_bbox(item, BAND_GREEN, bbox, target_resolution=TARGET_RESOLUTION_M)
    blue_da = _clip_band_to_bbox(item, BAND_BLUE, bbox, target_resolution=TARGET_RESOLUTION_M)
    nir_da = _clip_band_to_bbox(item, BAND_NIR, bbox, target_resolution=TARGET_RESOLUTION_M)

    # SCL : PAS d'upsampling (nearest pour les classes)
    scl = None
    try:
        scl_da = _clip_band_to_bbox(item, BAND_SCL, bbox, target_resolution=None)
        scl_da = scl_da.rio.reproject_match(red_da, resampling=Resampling.nearest)
        scl = scl_da.values[0]
    except Exception as e:
        logger.warning("[Sentinel] SCL indisponible, pas de masque nuages : %s", e)

    # Réaligner les bandes sur la même grille que Red
    green_da = green_da.rio.reproject_match(red_da, resampling=Resampling.bilinear)
    blue_da = blue_da.rio.reproject_match(red_da, resampling=Resampling.bilinear)
    nir_da = nir_da.rio.reproject_match(red_da, resampling=Resampling.bilinear)

    red = red_da.values[0].astype("float32")
    green = green_da.values[0].astype("float32")
    blue = blue_da.values[0].astype("float32")
    nir = nir_da.values[0].astype("float32")

    logger.info("[Sentinel] Dimensions finales : %s", red.shape)

    transform = red_da.rio.transform()
    crs = red_da.rio.crs

    # ------------------------------------------------------------------
    # 2. Masquer les nuages/ombres via SCL
    # ------------------------------------------------------------------
    if scl is not None:
        red, green, blue, nir = _mask_clouds_with_scl(red, green, blue, nir, scl)
    else:
        logger.info("[Sentinel] Pas de masque SCL appliqué")

    # ------------------------------------------------------------------
    # 3. RGB (étirement 1-99% + gamma correction)
    # ------------------------------------------------------------------
    rgb = np.stack(
        [
            _reflectance_to_uint8(red, gamma=0.9),
            _reflectance_to_uint8(green, gamma=0.9),
            _reflectance_to_uint8(blue, gamma=0.9),
        ],
        axis=0,
    )

    # ------------------------------------------------------------------
    # 4. NDVI = (NIR - Red) / (NIR + Red)
    # ------------------------------------------------------------------
    denom = nir + red
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = np.where(denom == 0, np.nan, (nir - red) / denom).astype("float32")

    # ------------------------------------------------------------------
    # 5. Écrire les COG + upload B2
    # ------------------------------------------------------------------
    scene_date = item.properties.get("datetime", "")[:10]
    scene_id = item.id
    cloud_cover = item.properties.get("eo:cloud_cover")

    with tempfile.TemporaryDirectory() as tmpdir:
        rgb_path = os.path.join(tmpdir, "sentinel_rgb.tif")
        ndvi_path = os.path.join(tmpdir, "sentinel_ndvi.tif")

        _write_rgb_cog(rgb[0], rgb[1], rgb[2], transform, crs, rgb_path)
        _write_single_band_cog(ndvi, transform, crs, ndvi_path, dtype="float32")

        suffix = uuid.uuid4().hex[:8]
        rgb_key = (
            f"rasters/farm_{farm_id}/parcel_{parcel_id}/sentinel_rgb/"
            f"{scene_date}_{suffix}.tif"
        )
        ndvi_key = (
            f"rasters/farm_{farm_id}/parcel_{parcel_id}/sentinel_ndvi/"
            f"{scene_date}_{suffix}.tif"
        )

        rgb_url = upload_file_to_b2(rgb_path, rgb_key)
        ndvi_url = upload_file_to_b2(ndvi_path, ndvi_key)

    return {
        "rgb_url": rgb_url,
        "rgb_key": rgb_key,
        "ndvi_url": ndvi_url,
        "ndvi_key": ndvi_key,
        "scene_date": scene_date,
        "scene_id": scene_id,
        "cloud_cover": cloud_cover,
        "bbox": bbox,
    }