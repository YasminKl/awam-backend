"""
Écriture des COG (Cloud Optimized GeoTIFF) pour les 14 indices spectraux.

Architecture HYBRIDE :
  - 3 COG séparés (visuels principaux) : NDVI, NDMI, NDWI
  - 1 COG multi-bande (11 bandes)      : NDRE, EVI, SAVI, MSAVI, NBR,
                                          REDEDGE, VARI, CARBONATE,
                                          SI_SOIL, PSRI, FCOVER

Toutes les fonctions écrivent en float32 avec nodata = -9999.0.

Convention de nommage B2 (identique à l'existant) :
  rasters/farm_{farm_id}/parcel_{parcel_id}/
    ├── sentinel_ndvi/{YYYY-MM-DD}_{uuid8}.tif
    ├── sentinel_ndmi/{YYYY-MM-DD}_{uuid8}.tif
    ├── sentinel_ndwi/{YYYY-MM-DD}_{uuid8}.tif
    └── sentinel_indices/{YYYY-MM-DD}_{uuid8}.tif

⚠️  Ce module est PUR (pas de DB, pas d'upload B2). L'upload se fait
    dans `ingestion.py` (orchestrateur).
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid

import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.crs import CRS

from app.services.indices.helpers import NODATA_VALUE, nan_to_nodata
from app.services.indices.registry import (
    MULTIBAND_ORDER,
    SEPARATE_COGS,
    get_indice,
)

logger = logging.getLogger(__name__)


# ======================================================================
# Constantes COG
# ======================================================================

COG_DRIVER = "GTiff"
COG_COMPRESS = "deflate"
COG_PREDICTOR = 3       # 3 = float32 (meilleure compression que 2 pour float)
COG_LEVEL = 9
COG_BLOCKSIZE = 512
OVERVIEW_RESAMPLING = "bilinear"


# ======================================================================
# Helpers
# ======================================================================


def _compute_blocksize(height: int, width: int) -> int:
    """
    Calcule une taille de bloc adaptée à la taille du raster.

    Doit être : multiple de 16, ne pas dépasser la taille du raster.
    """
    blocksize = COG_BLOCKSIZE
    if width < blocksize or height < blocksize:
        blocksize = 256
    if width < blocksize or height < blocksize:
        blocksize = min(width, height)
    blocksize = max(16, (blocksize // 16) * 16)
    return blocksize


def _build_profile(
    height: int,
    width: int,
    count: int,
    dtype: str,
    crs: CRS,
    transform: Affine,
    *,
    nodata: float | None = NODATA_VALUE,
) -> dict:
    """Construit un profil GTiff intermédiaire (avant cog_translate)."""
    blocksize = _compute_blocksize(height, width)
    profile = {
        "driver": COG_DRIVER,
        "height": height,
        "width": width,
        "count": count,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "compress": COG_COMPRESS,
        "predictor": COG_PREDICTOR,
        "tiled": True,
        "blockxsize": blocksize,
        "blockysize": blocksize,
        "BIGTIFF": "IF_SAFER",
    }
    if nodata is not None:
        profile["nodata"] = nodata
    return profile


def _translate_to_cog(tmp_tif: str, dst_path: str) -> None:
    """Convertit un GTiff intermédiaire en COG optimisé."""
    from rio_cogeo.cogeo import cog_translate
    from rio_cogeo.profiles import cog_profiles

    blocksize = _compute_blocksize(
        *rasterio.open(tmp_tif).shape[:2]
    )

    cog_profile = cog_profiles.get("deflate")
    cog_profile.update({
        "BIGTIFF": "IF_SAFER",
        "BLOCKSIZE": blocksize,
        "OVERVIEW_RESAMPLING": OVERVIEW_RESAMPLING,
        "PREDICTOR": COG_PREDICTOR,
        "LEVEL": COG_LEVEL,
    })

    cog_translate(tmp_tif, dst_path, cog_profile, in_memory=False, quiet=True)


# ======================================================================
# Écriture COG mono-bande (3 indices séparés)
# ======================================================================


def write_single_band_cog(
    arr: np.ndarray,
    transform: Affine,
    crs: CRS,
    dst_path: str,
    *,
    dtype: str = "float32",
) -> None:
    """
    Écrit un COG mono-bande (float32) avec nodata = -9999.0.

    Args:
        arr:       np.ndarray float32 (avec NaN sur pixels invalides)
        transform: Affine transform
        crs:       CRS
        dst_path:  chemin de sortie du COG
        dtype:     type de sortie (défaut float32)
    """
    arr_clean = nan_to_nodata(arr, NODATA_VALUE).astype(dtype)
    height, width = arr_clean.shape

    profile = _build_profile(
        height=height, width=width, count=1,
        dtype=dtype, crs=crs, transform=transform,
        nodata=NODATA_VALUE,
    )

    tmp_tif = dst_path.replace(".tif", "_tmp.tif")
    with rasterio.open(tmp_tif, "w", **profile) as dst:
        dst.write(arr_clean, 1)

    _translate_to_cog(tmp_tif, dst_path)

    if os.path.exists(tmp_tif):
        os.remove(tmp_tif)


# ======================================================================
# Écriture COG multi-bande (11 indices restants)
# ======================================================================


def write_multiband_cog(
    bands_dict: dict[str, np.ndarray],
    transform: Affine,
    crs: CRS,
    dst_path: str,
    *,
    dtype: str = "float32",
) -> None:
    """
    Écrit un COG multi-bande avec les 11 indices restants.

    L'ordre des bandes est défini par MULTIBAND_ORDER :
        B1: NDRE, B2: EVI, B3: SAVI, B4: MSAVI, B5: NBR,
        B6: REDEDGE, B7: VARI, B8: CARBONATE, B9: SI_SOIL,
        B10: PSRI, B11: FCOVER

    Args:
        bands_dict: dict {indice_name: np.ndarray} — clés = noms d'indices
        transform:  Affine transform
        crs:        CRS
        dst_path:   chemin de sortie du COG
        dtype:      type de sortie (défaut float32)

    Raises:
        ValueError: si une bande attendue est manquante ou shape incohérente.
    """
    # Vérifier que toutes les 11 bandes attendues sont présentes
    expected = set(MULTIBAND_ORDER.values())
    provided = set(bands_dict.keys())
    missing = expected - provided
    if missing:
        raise ValueError(
            f"COG multi-bande : indices manquants {sorted(missing)}. "
            f"Attendu : {sorted(expected)}"
        )

    # Récupérer les tableaux dans le bon ordre
    ordered: list[np.ndarray] = []
    for i in range(1, 12):
        name = MULTIBAND_ORDER[i]
        arr = bands_dict[name]
        if not isinstance(arr, np.ndarray):
            raise ValueError(f"{name} : pas un ndarray ({type(arr)})")
        ordered.append(nan_to_nodata(arr, NODATA_VALUE).astype(dtype))

    # Vérifier que toutes les bandes ont la même shape
    shape0 = ordered[0].shape
    for i, arr in enumerate(ordered, start=1):
        if arr.shape != shape0:
            raise ValueError(
                f"Shape incohérente bande {i} ({MULTIBAND_ORDER[i]}) : "
                f"{arr.shape} != {shape0}"
            )

    height, width = shape0
    count = len(ordered)

    profile = _build_profile(
        height=height, width=width, count=count,
        dtype=dtype, crs=crs, transform=transform,
        nodata=NODATA_VALUE,
    )

    tmp_tif = dst_path.replace(".tif", "_tmp.tif")
    with rasterio.open(tmp_tif, "w", **profile) as dst:
        for i, arr in enumerate(ordered, start=1):
            dst.write(arr, i)
            # Nommer les bandes pour Titiler/lecture
            dst.set_band_description(i, MULTIBAND_ORDER[i])

    _translate_to_cog(tmp_tif, dst_path)

    if os.path.exists(tmp_tif):
        os.remove(tmp_tif)


# ======================================================================
# Orchestrateur : écrit tous les COG dans un dossier temporaire
# ======================================================================


def write_all_cogs(
    indices: dict[str, np.ndarray],
    transform: Affine,
    crs: CRS,
    *,
    tmpdir: str | None = None,
    scene_date: str | None = None,
) -> dict[str, str]:
    """
    Écrit les 4 COG (3 séparés + 1 multi-bande) dans un dossier temporaire.

    Args:
        indices:    dict {indice_name: np.ndarray} — les 14 indices calculés
        transform:  Affine transform
        crs:        CRS
        tmpdir:     dossier temporaire (créé si None)
        scene_date: date de la scène (YYYY-MM-DD) pour nommage

    Returns:
        dict {nom_logique: chemin_local} :
          - "ndvi", "ndmi", "ndwi" : chemins des 3 COG séparés
          - "multiband"             : chemin du COG multi-bande

    Raises:
        ValueError: si un des 3 indices séparés manque, ou si une bande
                    multi-bande manque.
    """
    # Vérifier que les 3 indices séparés sont présents
    missing_sep = [n for n in SEPARATE_COGS if n not in indices]
    if missing_sep:
        raise ValueError(
            f"Indices séparés manquants : {missing_sep}"
        )

    # Vérifier que les 11 indices multi-bande sont présents
    missing_multi = [
        n for n in MULTIBAND_ORDER.values() if n not in indices
    ]
    if missing_multi:
        raise ValueError(
            f"Indices multi-bande manquants : {missing_multi}"
        )

    # Créer le dossier temporaire si non fourni
    own_tmpdir = tmpdir is None
    if own_tmpdir:
        tmpdir = tempfile.mkdtemp(prefix="awam_cog_")

    date_prefix = scene_date or "unknown_date"
    paths: dict[str, str] = {}

    try:
        # 3 COG séparés
        for name in SEPARATE_COGS:
            arr = indices[name]
            lower = name.lower()
            dst = os.path.join(tmpdir, f"{lower}_{date_prefix}.tif")
            write_single_band_cog(arr, transform, crs, dst)
            paths[lower] = dst
            logger.info("[cog_writer] COG écrit : %s", dst)

        # 1 COG multi-bande
        multi_dict = {
            name: indices[name] for name in MULTIBAND_ORDER.values()
        }
        multi_dst = os.path.join(tmpdir, f"indices_{date_prefix}.tif")
        write_multiband_cog(multi_dict, transform, crs, multi_dst)
        paths["multiband"] = multi_dst
        logger.info("[cog_writer] COG multi-bande écrit : %s", multi_dst)

    except Exception:
        # Nettoyer le tmpdir en cas d'erreur
        if own_tmpdir and os.path.isdir(tmpdir):
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        raise

    return paths


# ======================================================================
# Génération des clés B2 (avec suffixe uuid8, identique à l'existant)
# ======================================================================


def build_b2_keys(
    farm_id: int,
    parcel_id: int,
    scene_date: str,
) -> dict[str, str]:
    """
    Construit les clés B2 des 4 COG (3 séparés + 1 multi-bande).

    Format (identique à l'existant) :
      rasters/farm_{farm_id}/parcel_{parcel_id}/sentinel_{indice}/{date}_{uuid8}.tif

    Args:
        farm_id:    id de la ferme
        parcel_id:  id de la parcelle
        scene_date: date de la scène (YYYY-MM-DD)

    Returns:
        dict {nom_logique: clé_B2} :
          - "ndvi", "ndmi", "ndwi" : clés des 3 COG séparés
          - "multiband"             : clé du COG multi-bande
    """
    base = f"rasters/farm_{farm_id}/parcel_{parcel_id}"

    keys: dict[str, str] = {}
    for name in SEPARATE_COGS:
        lower = name.lower()
        suffix = uuid.uuid4().hex[:8]
        keys[lower] = (
            f"{base}/sentinel_{lower}/{scene_date}_{suffix}.tif"
        )

    suffix = uuid.uuid4().hex[:8]
    keys["multiband"] = (
        f"{base}/sentinel_indices/{scene_date}_{suffix}.tif"
    )
    return keys


# ======================================================================
# Palettes Titiler (JSON à renvoyer au frontend, PAS stocké en base)
# ======================================================================


INDICE_PALETTES: dict[str, list[list[int | float]]] = {
    "NDVI":      [[0, "#a50026"], [0.5, "#ffffbf"], [1, "#006837"]],
    "NDMI":      [[-1, "#fff7fb"], [0, "#6a51a3"], [1, "#08306b"]],
    "NDWI":      [[-1, "#ffffff"], [0, "#4292c6"], [1, "#08306b"]],
    "NDRE":      [[0, "#a50026"], [0.5, "#ffffbf"], [1, "#006837"]],
    "EVI":       [[0, "#440154"], [0.5, "#21918c"], [1, "#fde725"]],
    "SAVI":      [[0, "#440154"], [0.5, "#21918c"], [1, "#fde725"]],
    "MSAVI":     [[0, "#440154"], [0.5, "#21918c"], [1, "#fde725"]],
    "NBR":       [[-1, "#006837"], [0, "#ffffbf"], [1, "#a50026"]],
    "REDEDGE":   [[-1, "#5e4fa2"], [0, "#ffffbf"], [1, "#9e0142"]],
    "VARI":      [[-1, "#440154"], [0, "#21918c"], [1, "#fde725"]],
    "CARBONATE": [[-1, "#fff7f3"], [0, "#dd3497"], [1, "#49006a"]],
    "SI_SOIL":   [[-1, "#fff7ec"], [0, "#d95f0e"], [1, "#7f0000"]],
    "PSRI":      [[-1, "#ffffcc"], [0, "#fd8d3c"], [1, "#800026"]],
    "FCOVER":    [[0, "#f7fcf5"], [0.5, "#74c476"], [1, "#00441b"]],
}


def get_palette(indice_name: str) -> list[list[int | float]] | None:
    """
    Récupère la palette Titiler d'un indice.

    Returns:
        Liste de stops [{valeur, couleur}, ...] ou None si inconnu.
    """
    return INDICE_PALETTES.get(indice_name.upper())


def get_all_palettes() -> dict[str, list[list[int | float]]]:
    """Récupère toutes les palettes (pour endpoint /palettes)."""
    return dict(INDICE_PALETTES)
