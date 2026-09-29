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

⚠️  Deux pipelines cohabitent dans ce fichier :
    1. Pipeline historique (RGB + NDVI)   → generate_sentinel_composites_for_parcel()
    2. Pipeline étendu (14 indices)       → generate_all_indices_for_parcel()
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

# --- Nouveaux imports pour les 14 indices ---
from app.services.cog_writer import (
    INDICE_PALETTES,
    build_b2_keys,
    get_palette,
    write_all_cogs,
)
from app.services.indices.calculator import compute_all_indices
from app.services.indices.registry import (
    MULTIBAND_ORDER,
    SEPARATE_COGS,
    filter_available_indices,
    get_indice,
    list_indice_names,
)
from app.services.zonal_stats import (
    compute_zonal_stats_with_validation,
    fetch_parcel_geometry_wkt,
)

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


# ======================================================================
# ======================================================================
# PIPELINE HISTORIQUE — RGB + NDVI
# ======================================================================
# ======================================================================


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
# Écriture COG (pipeline historique)
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
# Point d'entrée public (pipeline historique RGB + NDVI)
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


# ======================================================================
# ======================================================================
# NOUVELLE SECTION — INGESTION DES 14 INDICES SPECTRAUX
# ======================================================================
# ======================================================================
#
# Cette section étend le pipeline existant (RGB + NDVI) pour calculer
# les 14 indices spectraux, écrire les 4 COG (3 séparés + 1 multi-bande),
# les uploader sur B2 et insérer les stats dans `indice_reading`.
#
# Architecture hybride :
#   - 3 COG séparés (visuels) : NDVI, NDMI, NDWI
#   - 1 COG multi-bande (11)  : NDRE, EVI, SAVI, MSAVI, NBR,
#                                REDEDGE, VARI, CARBONATE, SI_SOIL,
#                                PSRI, FCOVER
#
# ⚠️  La fonction `generate_sentinel_composites_for_parcel()` ci-dessus
#     reste INCHANGÉE (rétro-compatibilité RGB + NDVI seuls).
# ======================================================================


# ----------------------------------------------------------------------
# Configuration spécifique aux 14 indices
# ----------------------------------------------------------------------

# Toutes les bandes requises par au moins un indice
ALL_BANDS_FOR_INDICES = ["B02", "B03", "B04", "B05", "B06", "B08", "B11", "B12"]


# ----------------------------------------------------------------------
# Helpers : récupération des infos parcel
# ----------------------------------------------------------------------


def _get_farm_id_for_parcel(db: Session, parcel_id: int) -> int | None:
    """Récupère le farm_id d'une parcelle (ou None si introuvable)."""
    row = db.execute(
        text("SELECT farm_id FROM parcel WHERE id = :parcel_id"),
        {"parcel_id": parcel_id},
    ).fetchone()
    return row.farm_id if row else None


# ----------------------------------------------------------------------
# ÉTAPE 1 — Téléchargement de TOUTES les bandes nécessaires
# ----------------------------------------------------------------------


def _download_all_bands(
    item,
    bbox: tuple[float, float, float, float],
    target_resolution: float = TARGET_RESOLUTION_M,
) -> tuple[dict[str, np.ndarray], np.ndarray | None, "rioxarray.DataArray"]:
    """
    Télécharge les 8 bandes + SCL, les aligne sur la grille de B04.

    Returns:
        (bands_dict, cloud_mask, ref_da)
          - bands_dict : {"B02": arr, "B03": arr, ..., "B12": arr}
          - cloud_mask : masque SCL (True = nuage) aligné sur B04, ou None
          - ref_da     : DataArray de référence (B04) pour transform/crs
    """
    # Vérifier les bandes manquantes
    available = set(item.assets.keys())
    missing = [b for b in ALL_BANDS_FOR_INDICES if b not in available]
    if missing:
        logger.warning(
            "[Indices] Bandes manquantes dans la scène : %s", missing
        )

    # On télécharge uniquement les bandes présentes
    bands_to_load = [b for b in ALL_BANDS_FOR_INDICES if b in available]

    if BAND_RED not in bands_to_load:
        raise ValueError(
            "B04 (Red) obligatoire manquante — impossible d'aligner les bandes"
        )

    # Charger B04 en premier (référence de grille)
    ref_da = _clip_band_to_bbox(
        item, BAND_RED, bbox, target_resolution=target_resolution
    )

    bands_dict: dict[str, np.ndarray] = {}
    bands_dict[BAND_RED] = ref_da.values[0].astype("float32")

    # Charger les autres bandes alignées sur B04
    for band in bands_to_load:
        if band == BAND_RED:
            continue
        try:
            da = _clip_band_to_bbox(
                item, band, bbox, target_resolution=target_resolution
            )
            da = da.rio.reproject_match(ref_da, resampling=Resampling.bilinear)
            bands_dict[band] = da.values[0].astype("float32")
        except Exception as e:
            logger.warning("[Indices] Échec chargement %s : %s", band, e)

    # Charger SCL (nearest, PAS d'upsampling)
    cloud_mask: np.ndarray | None = None
    try:
        scl_da = _clip_band_to_bbox(item, BAND_SCL, bbox, target_resolution=None)
        scl_da = scl_da.rio.reproject_match(ref_da, resampling=Resampling.nearest)
        scl_arr = scl_da.values[0]
        cloud_mask = np.isin(scl_arr, list(SCL_MASK_VALUES))
        masked_count = int(cloud_mask.sum())
        total = cloud_mask.size
        logger.info(
            "[Indices] Masque SCL : %d/%d pixels masqués (%.1f%%)",
            masked_count, total, 100.0 * masked_count / max(total, 1),
        )
    except Exception as e:
        logger.warning("[Indices] SCL indisponible, pas de masque nuages : %s", e)

    return bands_dict, cloud_mask, ref_da


# ----------------------------------------------------------------------
# ÉTAPE 2 — Application du masque SCL sur toutes les bandes
# ----------------------------------------------------------------------


def _apply_cloud_mask(
    bands: dict[str, np.ndarray],
    cloud_mask: np.ndarray | None,
) -> dict[str, np.ndarray]:
    """
    Applique le masque SCL (True = nuage) sur toutes les bandes.

    Les pixels nuageux deviennent NaN.
    """
    if cloud_mask is None:
        return bands

    masked: dict[str, np.ndarray] = {}
    for band, arr in bands.items():
        arr_m = np.where(cloud_mask, np.nan, arr).astype("float32")
        masked[band] = arr_m
    return masked


# ----------------------------------------------------------------------
# ÉTAPE 3 — Cache : vérifier si une scène est déjà ingérée
# ----------------------------------------------------------------------


def _check_scene_in_cache(
    db: Session,
    parcel_id: int,
    scene_id: str,
) -> tuple[bool, float | None]:
    """
    Vérifie si une scène est déjà ingérée pour cette parcelle.

    Returns:
        (already_exists, existing_cloud_cover)
    """
    row = db.execute(
        text("""
            SELECT cloud_cover
            FROM indice_reading
            WHERE parcel_id = :parcel_id AND scene_id = :scene_id
            LIMIT 1
        """),
        {"parcel_id": parcel_id, "scene_id": scene_id},
    ).fetchone()

    if row is None:
        return False, None
    return True, row.cloud_cover


# ----------------------------------------------------------------------
# ÉTAPE 4 — Suppression des anciennes lignes (si remplacement)
# ----------------------------------------------------------------------


def _delete_scene_readings(
    db: Session,
    parcel_id: int,
    scene_id: str,
) -> int:
    """Supprime toutes les lignes d'une scène donnée. Retourne le nb supprimé."""
    result = db.execute(
        text("""
            DELETE FROM indice_reading
            WHERE parcel_id = :parcel_id AND scene_id = :scene_id
        """),
        {"parcel_id": parcel_id, "scene_id": scene_id},
    )
    return result.rowcount


# ----------------------------------------------------------------------
# ÉTAPE 5 — Insertion en base (une ligne par indice)
# ----------------------------------------------------------------------


def _insert_indice_readings(
    db: Session,
    parcel_id: int,
    scene_id: str,
    scene_date,
    cloud_cover: float | None,
    recorded_at,
    stats_by_indice: dict[str, dict],
    b2_keys: dict[str, str],
) -> int:
    """
    Insère les lignes `indice_reading` pour chaque indice calculé.

    Returns:
        Nombre de lignes insérées.
    """
    inserted = 0

    def b2_key_for(indice_name: str) -> str | None:
        lower = indice_name.lower()
        if lower in b2_keys:
            return b2_keys[lower]
        if indice_name in MULTIBAND_ORDER.values():
            return b2_keys.get("multiband")
        return None

    for indice_name, stats in stats_by_indice.items():
        spec = get_indice(indice_name)

        db.execute(
            text("""
                INSERT INTO indice_reading (
                    parcel_id, indice_name,
                    value, min_value, max_value, std_value, median_value,
                    valid_pixels, cloud_pixels, total_pixels, valid_ratio,
                    theoretical_min, theoretical_max,
                    is_valid, validation_error,
                    scene_id, scene_date, cloud_cover, b2_key,
                    recorded_at
                )
                VALUES (
                    :parcel_id, :indice_name,
                    :value, :min_value, :max_value, :std_value, :median_value,
                    :valid_pixels, :cloud_pixels, :total_pixels, :valid_ratio,
                    :theoretical_min, :theoretical_max,
                    :is_valid, :validation_error,
                    :scene_id, :scene_date, :cloud_cover, :b2_key,
                    :recorded_at
                )
            """),
            {
                "parcel_id": parcel_id,
                "indice_name": indice_name,
                "value": stats.get("mean"),
                "min_value": stats.get("min"),
                "max_value": stats.get("max"),
                "std_value": stats.get("std"),
                "median_value": stats.get("median"),
                "valid_pixels": stats.get("valid_pixels", 0),
                "cloud_pixels": stats.get("cloud_pixels", 0),
                "total_pixels": stats.get("total_pixels", 0),
                "valid_ratio": stats.get("valid_ratio", 0.0),
                "theoretical_min": spec.theoretical_min,
                "theoretical_max": spec.theoretical_max,
                "is_valid": stats.get("is_valid", True),
                "validation_error": stats.get("validation_error"),
                "scene_id": scene_id,
                "scene_date": scene_date,
                "cloud_cover": cloud_cover,
                "b2_key": b2_key_for(indice_name),
                "recorded_at": recorded_at,
            },
        )
        inserted += 1

    return inserted


# ----------------------------------------------------------------------
# FONCTION PRINCIPALE — génération des 14 indices
# ----------------------------------------------------------------------


def generate_all_indices_for_parcel(
    db: Session,
    farm_id: int,
    parcel_id: int,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    target_resolution: float = TARGET_RESOLUTION_M,
    *,
    force_refresh: bool = False,
) -> dict | None:
    """
    Génère les 14 indices spectraux pour une parcelle et insère les stats.

    Flux :
      1. Recherche STAC (meilleure scène)
      2. Vérification cache (skip ou remplace selon cloud_cover)
      3. Téléchargement des 8 bandes + SCL
      4. Masquage SCL
      5. Calcul des 14 indices
      6. Écriture des 4 COG (3 séparés + 1 multi-bande)
      7. Upload B2
      8. Stats zonales (polygone exact) + validation
      9. Insertion en base

    Args:
        force_refresh: si True, ignore le cache et remplace même si la
                       scène existante a moins de nuages.

    Returns:
        dict avec :
          - scene_id, scene_date, cloud_cover
          - inserted : nombre de lignes insérées
          - skipped  : nombre d'indices skippés
          - b2_keys  : dict des clés B2
          - indices_calculated : liste des indices calculés
          - indices_skipped    : dict {indice: raison}
        ou None si aucune scène / aucune géométrie.
    """
    # ------------------------------------------------------------------
    # 0. Vérifs préliminaires
    # ------------------------------------------------------------------
    bbox = _get_bbox_for_parcel(db, parcel_id)
    if bbox is None:
        logger.info("[Indices] Parcelle %s sans géométrie", parcel_id)
        return None

    parcel_wkt = fetch_parcel_geometry_wkt(db, parcel_id)
    if parcel_wkt is None:
        logger.info("[Indices] Parcelle %s sans WKT exploitable", parcel_id)
        return None

    logger.info("[Indices] Bbox parcelle %s : %s", parcel_id, bbox)

    # ------------------------------------------------------------------
    # 1. Recherche STAC
    # ------------------------------------------------------------------
    item = _search_best_scene(bbox, lookback_days=lookback_days)
    if item is None:
        logger.info("[Indices] Aucune scène trouvée pour parcel %s", parcel_id)
        return None

    scene_id = item.id
    scene_date_str = item.properties.get("datetime", "")[:10]
    cloud_cover = item.properties.get("eo:cloud_cover")

    logger.info(
        "[Indices] Scène retenue : %s (clouds=%.4f%%)",
        scene_id, cloud_cover if cloud_cover is not None else -1,
    )


    # ------------------------------------------------------------------
    # 2. Vérification cache
    # ------------------------------------------------------------------
    already, existing_cloud = _check_scene_in_cache(db, parcel_id, scene_id)

    if already:
        if force_refresh:
            # force_refresh=True → on supprime systématiquement
            deleted = _delete_scene_readings(db, parcel_id, scene_id)
            logger.info(
                "[Indices] force_refresh=True → REMPLACE (%d lignes supprimées)",
                deleted,
            )
        elif (
            existing_cloud is not None
            and cloud_cover is not None
            and cloud_cover >= existing_cloud
        ):
            # La nouvelle scène est moins bonne → SKIP
            logger.info(
                "[Indices] Scène déjà ingérée avec clouds=%.4f%% ≤ nouvelle (%.4f%%) → SKIP",
                existing_cloud, cloud_cover,
            )
            return {
                "scene_id": scene_id,
                "scene_date": scene_date_str,
                "cloud_cover": cloud_cover,
                "inserted": 0,
                "skipped": 0,
                "cached": True,
                "b2_keys": {},
                "indices_calculated": [],
                "indices_skipped": {},
            }
        else:
            # La nouvelle scène est meilleure → REMPLACE
            deleted = _delete_scene_readings(db, parcel_id, scene_id)
            logger.info(
                "[Indices] Scène déjà ingérée mais nouvelle moins nuageuse → REMPLACE (%d lignes supprimées)",
                deleted,
            )

    # ------------------------------------------------------------------
    # 3. Téléchargement des bandes
    # ------------------------------------------------------------------
    bands, cloud_mask, ref_da = _download_all_bands(
        item, bbox, target_resolution=target_resolution
    )

    available_bands = set(bands.keys())
    indices_calculables = filter_available_indices(available_bands)
    logger.info(
        "[Indices] %d/%d indices calculables (bandes dispo : %s)",
        len(indices_calculables), len(list_indice_names()),
        sorted(available_bands),
    )

    # ------------------------------------------------------------------
    # 4. Masquage SCL
    # ------------------------------------------------------------------
    bands_masked = _apply_cloud_mask(bands, cloud_mask)

    # ------------------------------------------------------------------
    # 5. Calcul des 14 indices
    # ------------------------------------------------------------------
    results = compute_all_indices(bands_masked)

    indices_ok: dict[str, np.ndarray] = {}
    indices_skipped: dict[str, str] = {}

    for name, (arr, err) in results.items():
        if err is not None or arr is None:
            indices_skipped[name] = err or "raison inconnue"
            logger.warning("[Indices] %s SKIP — %s", name, err)
        else:
            indices_ok[name] = arr

    logger.info(
        "[Indices] %d calculés, %d skippés",
        len(indices_ok), len(indices_skipped),
    )

    if not indices_ok:
        logger.warning("[Indices] Aucun indice calculable pour parcel %s", parcel_id)
        return {
            "scene_id": scene_id,
            "scene_date": scene_date_str,
            "cloud_cover": cloud_cover,
            "inserted": 0,
            "skipped": len(indices_skipped),
            "b2_keys": {},
            "indices_calculated": [],
            "indices_skipped": indices_skipped,
        }

    # ------------------------------------------------------------------
    # 6. Écriture des 4 COG
    # ------------------------------------------------------------------
    transform = ref_da.rio.transform()
    crs = ref_da.rio.crs

    can_write_cogs = (
        all(n in indices_ok for n in SEPARATE_COGS)
        and all(n in indices_ok for n in MULTIBAND_ORDER.values())
    )

    b2_keys: dict[str, str] = {}
    cog_paths: dict[str, str] = {}

    if can_write_cogs:
        try:
            cog_paths = write_all_cogs(
                indices_ok, transform, crs, scene_date=scene_date_str,
            )
            b2_keys = build_b2_keys(farm_id, parcel_id, scene_date_str)
            logger.info("[Indices] COG écrits : %s", sorted(cog_paths.keys()))
        except Exception as e:
            logger.exception("[Indices] Échec écriture COG : %s", e)
            cog_paths = {}
            b2_keys = {}
    else:
        missing = [
            n for n in list(SEPARATE_COGS) + list(MULTIBAND_ORDER.values())
            if n not in indices_ok
        ]
        logger.warning(
            "[Indices] COG NON écrits (indices manquants : %s)", missing
        )

    # ------------------------------------------------------------------
    # 7. Upload B2
    # ------------------------------------------------------------------
    b2_urls: dict[str, str] = {}
    if cog_paths and b2_keys:
        for key, local_path in cog_paths.items():
            try:
                url = upload_file_to_b2(local_path, b2_keys[key])
                b2_urls[key] = url
                logger.info("[Indices] Upload B2 OK : %s", b2_keys[key])
            except Exception as e:
                logger.exception("[Indices] Échec upload B2 %s : %s", key, e)

    # ------------------------------------------------------------------
    # 8. Stats zonales
    # ------------------------------------------------------------------
    stats_by_indice: dict[str, dict] = {}
    for name, arr in indices_ok.items():
        spec = get_indice(name)
        try:
            stats = compute_zonal_stats_with_validation(
                arr=arr,
                transform=transform,
                raster_crs=crs,
                parcel_geometry=parcel_wkt,
                theoretical_min=spec.theoretical_min,
                theoretical_max=spec.theoretical_max,
                cloud_mask=cloud_mask,
            )
            stats_by_indice[name] = stats
        except Exception as e:
            logger.exception("[Indices] Échec stats %s : %s", name, e)
            indices_skipped[name] = f"stats: {e}"

    # ------------------------------------------------------------------
    # 9. Insertion en base
    # ------------------------------------------------------------------
    from datetime import datetime as _datetime, timezone as _timezone, date as _date
    scene_date_obj = None
    recorded_at = None
    try:
        scene_date_obj = _date.fromisoformat(scene_date_str)
        recorded_at = item.datetime or _datetime.now(_timezone.utc)
    except Exception:
        pass

    inserted = 0
    if stats_by_indice:
        try:
            inserted = _insert_indice_readings(
                db=db,
                parcel_id=parcel_id,
                scene_id=scene_id,
                scene_date=scene_date_obj,
                cloud_cover=cloud_cover,
                recorded_at=recorded_at,
                stats_by_indice=stats_by_indice,
                b2_keys=b2_keys,
            )
            db.commit()
            logger.info("[Indices] %d lignes insérées en base", inserted)
        except Exception as e:
            db.rollback()
            logger.exception("[Indices] Échec insertion DB : %s", e)

    return {
        "scene_id": scene_id,
        "scene_date": scene_date_str,
        "cloud_cover": cloud_cover,
        "inserted": inserted,
        "skipped": len(indices_skipped),
        "cached": False,
        "b2_keys": b2_keys,
        "b2_urls": b2_urls,
        "indices_calculated": sorted(indices_ok.keys()),
        "indices_skipped": indices_skipped,
    }


# ----------------------------------------------------------------------
# Wrapper public (utilisé par les tâches Celery)
# ----------------------------------------------------------------------


def generate_all_indices_for_parcel_safe(
    parcel_id: int,
    *,
    force_refresh: bool = False,
) -> dict | None:
    """
    Version safe de `generate_all_indices_for_parcel` qui ouvre sa propre
    session DB et la ferme proprement.

    Utilisée par les tâches Celery (`ingestion_tasks.py`).
    """
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        farm_id = _get_farm_id_for_parcel(db, parcel_id)
        if farm_id is None:
            logger.warning("[Indices] Parcelle %s introuvable", parcel_id)
            return None
        return generate_all_indices_for_parcel(
            db=db,
            farm_id=farm_id,
            parcel_id=parcel_id,
            force_refresh=force_refresh,
        )
    finally:
        db.close()


# ----------------------------------------------------------------------
# Endpoint utilitaire — palettes à renvoyer au frontend
# ----------------------------------------------------------------------


def get_all_indice_palettes() -> dict[str, list[list[int | float]]]:
    """Récupère toutes les palettes des 14 indices (pour le frontend)."""
    return dict(INDICE_PALETTES)