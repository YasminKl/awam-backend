"""
Utilitaires de calcul pour les indices spectraux.

Toutes les fonctions :
  - travaillent sur des np.ndarray float32
  - gèrent les NaN et les divisions par zéro SANS crasher
  - ne font AUCUNE I/O (pas de fichier, pas de DB)

Convention de masquage :
  - np.nan  = pixel invalide (nuage, nodata, SCL masqué)
  - Ces NaN sont propagés tout au long du calcul
  - Les stats (mean, median, etc.) les ignorent (np.nanmean, etc.)
  - L'écriture COG les remplace par -9999.0 (nodata GDAL)
"""

from __future__ import annotations

import numpy as np


# ======================================================================
# Constantes
# ======================================================================

# Valeur nodata pour les COG (GDAL)
NODATA_VALUE: float = -9999.0

# Epsilon pour éviter les divisions par zéro "presque nulles"
EPSILON: float = 1e-10


# ======================================================================
# Division sécurisée
# ======================================================================


def safe_divide(
    numerator: np.ndarray,
    denominator: np.ndarray,
    *,
    fill: float = np.nan,
) -> np.ndarray:
    """
    Division protégée contre la division par zéro.

    Là où |denominator| < EPSILON, on retourne `fill` (NaN par défaut).
    Là où numerator ou denominator est NaN, on retourne NaN.

    Args:
        numerator:   tableau numérateur
        denominator: tableau dénominateur
        fill:        valeur retournée quand le dénominateur est ~0

    Returns:
        np.ndarray float32 avec la division sécurisée

    Exemple:
        >>> a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        >>> b = np.array([1.0, 0.0, 3.0], dtype=np.float32)
        >>> safe_divide(a, b)
        array([1., nan, 1.], dtype=float32)
    """
    num = np.asarray(numerator, dtype=np.float32)
    den = np.asarray(denominator, dtype=np.float32)

    # Masque : dénominateur ~0 OU NaN quelque part
    invalid = (
        ~np.isfinite(den)
        | (np.abs(den) < EPSILON)
        | ~np.isfinite(num)
    )

    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(invalid, fill, num / den).astype(np.float32)

    return result


# ======================================================================
# Normalized Difference (ND)
# ======================================================================


def normalized_difference(
    a: np.ndarray,
    b: np.ndarray,
) -> np.ndarray:
    """
    Calcule (a - b) / (a + b), protégé contre la division par zéro.

    C'est la brique de base de NDVI, NDMI, NDWI, NDRE, NBR, etc.

    Args:
        a: première bande (ex: NIR)
        b: seconde bande (ex: Red)

    Returns:
        np.ndarray float32 dans [-1, 1] (NaN si a+b ~ 0)
    """
    a32 = np.asarray(a, dtype=np.float32)
    b32 = np.asarray(b, dtype=np.float32)
    return safe_divide(a32 - b32, a32 + b32)


# ======================================================================
# Clamp / bornes
# ======================================================================


def clamp(
    arr: np.ndarray,
    min_value: float,
    max_value: float,
) -> np.ndarray:
    """
    Borne un tableau entre min_value et max_value.
    Préserve les NaN (np.clip les écrase sinon).

    Args:
        arr:       tableau d'entrée
        min_value: borne inférieure
        max_value: borne supérieure

    Returns:
        np.ndarray float32 borné, NaN préservés
    """
    arr32 = np.asarray(arr, dtype=np.float32)
    nan_mask = ~np.isfinite(arr32)

    clipped = np.clip(arr32, min_value, max_value).astype(np.float32)
    clipped[nan_mask] = np.nan
    return clipped


# ======================================================================
# Overflow / domaine
# ======================================================================


def safe_sqrt(arr: np.ndarray) -> np.ndarray:
    """
    Racine carrée protégée : les valeurs < 0 deviennent NaN au lieu de
    provoquer un warning "invalid value encountered in sqrt".

    Utile pour MSAVI : sqrt((2*NIR+1)^2 - 8*(NIR-RED))
    """
    arr32 = np.asarray(arr, dtype=np.float32)
    with np.errstate(invalid="ignore"):
        result = np.sqrt(np.where(arr32 < 0, np.nan, arr32)).astype(np.float32)
    return result


# ======================================================================
# Masquage / nettoyage
# ======================================================================


def mask_invalid(
    arr: np.ndarray,
    additional_mask: np.ndarray | None = None,
) -> np.ndarray:
    """
    Force les pixels invalides (non-finis) à NaN, et applique un masque
    supplémentaire optionnel (ex: masque SCL).

    Args:
        arr:              tableau d'entrée
        additional_mask:  masque booléen optionnel (True = à masquer)

    Returns:
        np.ndarray float32 avec NaN sur les pixels invalides
    """
    arr32 = np.asarray(arr, dtype=np.float32).copy()
    arr32[~np.isfinite(arr32)] = np.nan
    if additional_mask is not None:
        arr32[additional_mask] = np.nan
    return arr32


def nan_to_nodata(arr: np.ndarray, nodata: float = NODATA_VALUE) -> np.ndarray:
    """
    Remplace les NaN par une valeur nodata (avant écriture COG).

    Args:
        arr:    tableau avec NaN
        nodata: valeur de remplacement (défaut -9999.0)

    Returns:
        np.ndarray float32 sans NaN
    """
    arr32 = np.asarray(arr, dtype=np.float32)
    return np.where(np.isfinite(arr32), arr32, np.float32(nodata)).astype(np.float32)


# ======================================================================
# Statistiques robustes (ignorent les NaN)
# ========================================