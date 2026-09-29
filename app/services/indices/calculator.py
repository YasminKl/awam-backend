"""
Calcul effectif des 14 indices spectraux Sentinel-2.

Chaque fonction :
  - reçoit un dict de bandes {band_name: np.ndarray float32}
  - retourne un np.ndarray float32 de l'indice (NaN sur pixels invalides)
  - utilise les helpers de `helpers.py` pour la robustesse

Toutes les fonctions partagent la même signature :
    calc_xxx(bands: dict[str, np.ndarray]) -> np.ndarray

Aucune I/O. Aucune DB. Testable en isolation.

⚠️  Les noms des fonctions DOIVENT correspondre au champ
    `calc_func_name` du registry (pour la résolution dynamique).
"""

from __future__ import annotations

import logging

import numpy as np

from app.services.indices.helpers import (
    clamp,
    normalized_difference,
    safe_divide,
    safe_sqrt,
)

logger = logging.getLogger(__name__)


# ======================================================================
# Constantes
# ======================================================================

SAVI_L: float = 0.5          # Facteur d'ajustement du sol (Huete 1988)
EVI_G: float = 2.5           # Gain EVI
EVI_C1: float = 6.0          # Coefficient aérosol (Red)
EVI_C2: float = 7.5          # Coefficient aérosol (Blue)
EVI_L: float = 1.0           # Facteur d'ajustement du sol (canopée)

# FCOVER (formule simple basée sur NDVI)
FCOVER_NDVI_MIN: float = 0.1
FCOVER_NDVI_MAX: float = 0.9


# ======================================================================
# 1. NDVI — Normalized Difference Vegetation Index
# ======================================================================

def calc_ndvi(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    NDVI = (NIR - RED) / (NIR + RED)

    Plage : [-1, 1]
    - < 0   : eau, neige, nuages
    - 0-0.2 : sol nu, roche, sable
    - 0.2-0.5 : végétation clairsemée
    - > 0.5 : végétation dense et saine
    """
    nir = bands["B08"]
    red = bands["B04"]
    return normalized_difference(nir, red)


# ======================================================================
# 2. NDMI — Normalized Difference Moisture Index
# ======================================================================

def calc_ndmi(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    NDMI = (NIR - SWIR1) / (NIR + SWIR1)

    Plage : [-1, 1]
    - > 0.4 : végétation très humide
    - 0.2-0.4 : humidité modérée
    - < 0.2 : stress hydrique
    """
    nir = bands["B08"]
    swir1 = bands["B11"]
    return normalized_difference(nir, swir1)


# ======================================================================
# 3. NDWI — Normalized Difference Water Index (McFeeters 1996)
# ======================================================================

def calc_ndwi(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    NDWI = (GREEN - NIR) / (GREEN + NIR)

    Plage : [-1, 1]
    - > 0 : surfaces en eau
    - < 0 : végétation, sol
    """
    green = bands["B03"]
    nir = bands["B08"]
    return normalized_difference(green, nir)


# ======================================================================
# 4. NDRE — Normalized Difference Red Edge
# ======================================================================

def calc_ndre(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    NDRE = (NIR - REDEDGE1) / (NIR + REDEDGE1)

    Plage : [-1, 1]
    - Plus sensible que NDVI pour les canopées denses (chlorophylle).
    """
    nir = bands["B08"]
    rededge1 = bands["B05"]
    return normalized_difference(nir, rededge1)


# ======================================================================
# 5. EVI — Enhanced Vegetation Index (Huete et al. 2002)
# ======================================================================

def calc_evi(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    EVI = 2.5 * (NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1)

    Plage : [-1, 1] (souvent [-0.5, 1])
    - Corrige les effets atmosphériques et de sol.
    - Utile en zones à forte biomasse (NDVI saturé).
    """
    nir = bands["B08"]
    red = bands["B04"]
    blue = bands["B02"]

    numerator = EVI_G * (nir - red)
    denominator = nir + EVI_C1 * red - EVI_C2 * blue + EVI_L

    return safe_divide(numerator, denominator)


# ======================================================================
# 6. SAVI — Soil Adjusted Vegetation Index (Huete 1988)
# ======================================================================

def calc_savi(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    SAVI = ((NIR - RED) / (NIR + RED + L)) * (1 + L)   avec L = 0.5

    Plage : [-1, 1]
    - Correction de l'effet du sol (utile en zones peu végétalisées).
    """
    nir = bands["B08"]
    red = bands["B04"]

    numerator = nir - red
    denominator = nir + red + SAVI_L

    return safe_divide(numerator, denominator) * (1.0 + SAVI_L)


# ======================================================================
# 7. MSAVI — Modified Soil Adjusted Vegetation Index (Qi 1994)
# ======================================================================

def calc_msavi(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    MSAVI = (2*NIR + 1 - sqrt((2*NIR + 1)^2 - 8*(NIR - RED))) / 2

    Plage : [-1, 1]
    - Version auto-adaptative de SAVI (L calculé automatiquement).
    - L'argument de sqrt peut être négatif → safe_sqrt renvoie NaN.
    """
    nir = bands["B08"]
    red = bands["B04"]

    two_nir_plus_1 = 2.0 * nir + 1.0
    inside = two_nir_plus_1 ** 2 - 8.0 * (nir - red)
    sqrt_part = safe_sqrt(inside)

    return ((two_nir_plus_1 - sqrt_part) / 2.0).astype(np.float32)


# ======================================================================
# 8. NBR — Normalized Burn Ratio (Key & Benson 2006)
# ======================================================================

def calc_nbr(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    NBR = (NIR - SWIR2) / (NIR + SWIR2)

    Plage : [-1, 1]
    - < -0.1 : zones brûlées récentes
    - > 0.3  : végétation saine
    """
    nir = bands["B08"]
    swir2 = bands["B12"]
    return normalized_difference(nir, swir2)


# ======================================================================
# 9. REDEDGE — Red Edge Normalized Difference
# ======================================================================

def calc_rededge(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    REDEDGE = (REDEDGE2 - REDEDGE1) / (REDEDGE2 + REDEDGE1)

    Plage : [-1, 1]
    - Contraste fin entre les deux bandes Red Edge.
    """
    rededge1 = bands["B05"]
    rededge2 = bands["B06"]
    return normalized_difference(rededge2, rededge1)


# ======================================================================
# 10. VARI — Visible Atmospherically Resistant Index (Gitelson 2002)
# ======================================================================

def calc_vari(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    VARI = (GREEN - RED) / (GREEN + RED - BLUE)

    Plage : [-1, 1]
    - Fonctionne avec les bandes visibles uniquement (pas de NIR).
    - Résistant aux effets atmosphériques.
    """
    green = bands["B03"]
    red = bands["B04"]
    blue = bands["B02"]

    numerator = green - red
    denominator = green + red - blue

    return safe_divide(numerator, denominator)


# ======================================================================
# 11. CARBONATE — Carbonate Index
# ======================================================================

def calc_carbonate(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    CARBONATE = (SWIR1 - SWIR2) / (SWIR1 + SWIR2)

    Plage : [-1, 1]
    - Détection des sols calcaires (roches sédimentaires).
    """
    swir1 = bands["B11"]
    swir2 = bands["B12"]
    return normalized_difference(swir1, swir2)


# ======================================================================
# 12. SI_SOIL — Soil Index (sols nus / salins)
# ======================================================================

def calc_si_soil(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    SI_SOIL = (RED - SWIR1) / (RED + SWIR1)

    Plage : [-1, 1]
    - Détection des sols nus, salins, ou très secs.
    """
    red = bands["B04"]
    swir1 = bands["B11"]
    return normalized_difference(red, swir1)


# ======================================================================
# 13. PSRI — Plant Senescence Reflectance Index (Merzlyak 1999)
# ======================================================================

def calc_psri(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    PSRI = (RED - GREEN) / REDEDGE2

    Plage : [-1, 1] (parfois plus élevé sur sols nus)
    - Détection de la sénescence (jaunissement, maturation).
    - ⚠️ Division par B06 → safe_divide protège.
    """
    red = bands["B04"]
    green = bands["B03"]
    rededge2 = bands["B06"]

    numerator = red - green
    return safe_divide(numerator, rededge2)


# ======================================================================
# 14. FCOVER — Fraction of Vegetation Cover (formule simple NDVI)
# ======================================================================

def calc_fcover(bands: dict[str, np.ndarray]) -> np.ndarray:
    """
    FCOVER = clamp((NDVI - 0.1) / (0.9 - 0.1), 0, 1)

    Plage : [0, 1]
    - 0 : sol nu
    - 1 : couvert végétal total
    - Formule simple pour la phase 1 (ESA utilise un réseau de neurones).
    """
    ndvi = calc_ndvi(bands)
    scaled = (ndvi - FCOVER_NDVI_MIN) / (FCOVER_NDVI_MAX - FCOVER_NDVI_MIN)
    return clamp(scaled, 0.0, 1.0)


# ======================================================================
# Table de résolution : nom → fonction
# ======================================================================

CALC_FUNCTIONS: dict[str, callable] = {
    "calc_ndvi": calc_ndvi,
    "calc_ndmi": calc_ndmi,
    "calc_ndwi": calc_ndwi,
    "calc_ndre": calc_ndre,
    "calc_evi": calc_evi,
    "calc_savi": calc_savi,
    "calc_msavi": calc_msavi,
    "calc_nbr": calc_nbr,
    "calc_rededge": calc_rededge,
    "calc_vari": calc_vari,
    "calc_carbonate": calc_carbonate,
    "calc_si_soil": calc_si_soil,
    "calc_psri": calc_psri,
    "calc_fcover": calc_fcover,
}


def get_calc_function(name: str):
    """
    Résout dynamiquement une fonction de calcul par son nom.

    Args:
        name: nom de la fonction (ex: "calc_ndvi")

    Raises:
        KeyError si la fonction n'existe pas.
    """
    if name not in CALC_FUNCTIONS:
        raise KeyError(
            f"Fonction de calcul inconnue : {name!r}. "
            f"Disponibles : {sorted(CALC_FUNCTIONS.keys())}"
        )
    return CALC_FUNCTIONS[name]


# ======================================================================
# Calcul d'un indice avec gestion d'erreur
# ======================================================================


def compute_indice(
    indice_name: str,
    bands: dict[str, np.ndarray],
) -> tuple[np.ndarray | None, str | None]:
    """
    Calcule un indice avec gestion d'erreur robuste.

    Vérifie d'abord que TOUTES les bandes requises sont présentes.
    Si une bande manque → retourne (None, "message d'erreur").
    Sinon, exécute la fonction et catch les exceptions éventuelles.

    Args:
        indice_name: nom de l'indice (ex: "NDVI")
        bands:       dict {band_name: np.ndarray}

    Returns:
        (resultat, erreur) :
          - (np.ndarray, None)   si OK
          - (None, "message")    si échec
    """
    # Import local pour éviter la dépendance circulaire au chargement
    from app.services.indices.registry import get_indice

    try:
        spec = get_indice(indice_name)
    except KeyError as e:
        return None, str(e)

    # Vérifier les bandes disponibles
    missing = [b for b in spec.required_bands if b not in bands]
    if missing:
        msg = f"bandes manquantes : {missing}"
        logger.warning("[calculator] %s SKIP — %s", indice_name, msg)
        return None, msg

    # Récupérer la fonction
    try:
        func = get_calc_function(spec.calc_func_name)
    except KeyError as e:
        return None, str(e)

    # Exécuter
    try:
        result = func(bands)
        if not isinstance(result, np.ndarray):
            return None, f"résultat non-ndarray : {type(result)}"
        return result.astype(np.float32), None
    except Exception as e:
        logger.exception("[calculator] %s — exception de calcul", indice_name)
        return None, f"{type(e).__name__}: {e}"


def compute_all_indices(
    bands: dict[str, np.ndarray],
) -> dict[str, tuple[np.ndarray | None, str | None]]:
    """
    Calcule les 14 indices à partir d'un dict de bandes.

    Args:
        bands: dict {band_name: np.ndarray}

    Returns:
        dict {indice_name: (resultat_ou_None, erreur_ou_None)}
    """
    from app.services.indices.registry import list_indice_names

    results: dict[str, tuple[np.ndarray | None, str | None]] = {}
    for name in list_indice_names():
        results[name] = compute_indice(name, bands)
    return results