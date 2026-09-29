"""
Registre des 14 indices spectraux Sentinel-2.

Table de référence PURE (pas d'I/O, pas de DB) contenant pour chaque indice :
  - son nom officiel
  - la formule mathématique (référence documentaire)
  - les bandes Sentinel-2 requises (pour détecter les skips)
  - ses bornes théoriques (pour la validation)
  - la fonction de calcul associée (résolue dynamiquement)

⚠️  Ce module ne fait AUCUN calcul. Les fonctions de calcul vivent dans
    `calculator.py`, et sont référencées ici par leur NOM (str) pour
    éviter une dépendance circulaire.

Rappel des bandes Sentinel-2 L2A utilisées :
  B02 = Blue       (10 m)
  B03 = Green      (10 m)
  B04 = Red        (10 m)
  B05 = RedEdge1   (20 m → upsamplé 5 m)
  B06 = RedEdge2   (20 m → upsamplé 5 m)
  B08 = NIR        (10 m)
  B11 = SWIR1      (20 m → upsamplé 5 m)
  B12 = SWIR2      (20 m → upsamplé 5 m)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ======================================================================
# Structure d'un indice
# ======================================================================


@dataclass(frozen=True)
class IndiceSpec:
    """
    Spécification complète d'un indice spectral.

    Attributes:
        name:              Nom officiel (ex: "NDVI")
        formula:           Formule mathématique lisible (documentaire)
        required_bands:    Bandes Sentinel-2 obligatoires (ex: ["B04", "B08"])
        theoretical_min:   Borne théorique minimale (pour validation)
        theoretical_max:   Borne théorique maximale (pour validation)
        calc_func_name:    Nom de la fonction dans calculator.py (résolue plus tard)
        description:       Description courte (pour doc/frontend)
        multi_band_order:  Position dans le COG multi-bande (None si séparé)
        separate_cog:      True si l'indice a son propre COG visuel
    """

    name: str
    formula: str
    required_bands: tuple[str, ...]
    theoretical_min: float
    theoretical_max: float
    calc_func_name: str
    description: str = ""
    multi_band_order: int | None = None
    separate_cog: bool = False

    @property
    def is_separate(self) -> bool:
        """True si l'indice est stocké dans un COG séparé (NDVI, NDMI, NDWI)."""
        return self.separate_cog

    @property
    def is_in_multiband(self) -> bool:
        """True si l'indice est dans le COG multi-bande."""
        return self.multi_band_order is not None


# ======================================================================
# Table de référence — LES 14 INDICES
# ======================================================================

INDICES_REGISTRY: dict[str, IndiceSpec] = {

    # ------------------------------------------------------------------
    # 1. NDVI — Normalized Difference Vegetation Index
    #    Le plus utilisé, mesure la vigueur végétale.
    #    COG séparé (visuel principal).
    # ------------------------------------------------------------------
    "NDVI": IndiceSpec(
        name="NDVI",
        formula="(B08 - B04) / (B08 + B04)",
        required_bands=("B04", "B08"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_ndvi",
        description="Vigueur de la végétation (Normalized Difference Vegetation Index)",
        multi_band_order=None,
        separate_cog=True,
    ),

    # ------------------------------------------------------------------
    # 2. NDMI — Normalized Difference Moisture Index
    #    Humidité de la végétation (NIR vs SWIR1).
    #    COG séparé (visuel).
    # ------------------------------------------------------------------
    "NDMI": IndiceSpec(
        name="NDMI",
        formula="(B08 - B11) / (B08 + B11)",
        required_bands=("B08", "B11"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_ndmi",
        description="Humidité de la végétation (Normalized Difference Moisture Index)",
        multi_band_order=None,
        separate_cog=True,
    ),

    # ------------------------------------------------------------------
    # 3. NDWI — Normalized Difference Water Index (McFeeters 1996)
    #    Détection des surfaces en eau (Green vs NIR).
    #    COG séparé (visuel).
    # ------------------------------------------------------------------
    "NDWI": IndiceSpec(
        name="NDWI",
        formula="(B03 - B08) / (B03 + B08)",
        required_bands=("B03", "B08"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_ndwi",
        description="Présence d'eau (Normalized Difference Water Index, McFeeters 1996)",
        multi_band_order=None,
        separate_cog=True,
    ),

    # ------------------------------------------------------------------
    # 4. NDRE — Normalized Difference Red Edge
    #    Très sensible à la chlorophylle, utile en agriculture de précision.
    #    Dans le multi-bande (B1).
    # ------------------------------------------------------------------
    "NDRE": IndiceSpec(
        name="NDRE",
        formula="(B08 - B05) / (B08 + B05)",
        required_bands=("B05", "B08"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_ndre",
        description="Chlorophylle via Red Edge (Normalized Difference Red Edge)",
        multi_band_order=1,
    ),

    # ------------------------------------------------------------------
    # 5. EVI — Enhanced Vegetation Index (Huete et al. 2002)
    #    Corrige les effets atmosphériques et de sol.
    #    Dans le multi-bande (B2).
    # ------------------------------------------------------------------
    "EVI": IndiceSpec(
        name="EVI",
        formula="2.5 * (B08 - B04) / (B08 + 6*B04 - 7.5*B02 + 1)",
        required_bands=("B02", "B04", "B08"),
        theoretical_min=-2.0,
        theoretical_max=2.0,
        calc_func_name="calc_evi",
        description="Végétation améliorée (Enhanced Vegetation Index, Huete 2002)",
        multi_band_order=2,
    ),

    # ------------------------------------------------------------------
    # 6. SAVI — Soil Adjusted Vegetation Index (Huete 1988)
    #    Correction de l'effet du sol (L=0.5).
    #    Dans le multi-bande (B3).
    # ------------------------------------------------------------------
    "SAVI": IndiceSpec(
        name="SAVI",
        formula="((B08 - B04) / (B08 + B04 + 0.5)) * 1.5",
        required_bands=("B04", "B08"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_savi",
        description="Végétation ajustée au sol (Soil Adjusted Vegetation Index, Huete 1988)",
        multi_band_order=3,
    ),

    # ------------------------------------------------------------------
    # 7. MSAVI — Modified Soil Adjusted Vegetation Index (Qi 1994)
    #    Version auto-adaptative de SAVI.
    #    Dans le multi-bande (B4).
    # ------------------------------------------------------------------
    "MSAVI": IndiceSpec(
        name="MSAVI",
        formula="(2*B08 + 1 - sqrt((2*B08 + 1)^2 - 8*(B08 - B04))) / 2",
        required_bands=("B04", "B08"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_msavi",
        description="SAVI modifié auto-adaptatif (Modified SAVI, Qi 1994)",
        multi_band_order=4,
    ),

    # ------------------------------------------------------------------
    # 8. NBR — Normalized Burn Ratio (Key & Benson 2006)
    #    Détection des zones brûlées (NIR vs SWIR2).
    #    Dans le multi-bande (B5).
    # ------------------------------------------------------------------
    "NBR": IndiceSpec(
        name="NBR",
        formula="(B08 - B12) / (B08 + B12)",
        required_bands=("B08", "B12"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_nbr",
        description="Zones brûlées (Normalized Burn Ratio, Key & Benson 2006)",
        multi_band_order=5,
    ),

    # ------------------------------------------------------------------
    # 9. REDEDGE — Red Edge Normalized Difference (simple)
    #    Utilise B05 et B06 pour un contraste fin.
    #    Dans le multi-bande (B6).
    # ------------------------------------------------------------------
    "REDEDGE": IndiceSpec(
        name="REDEDGE",
        formula="(B06 - B05) / (B06 + B05)",
        required_bands=("B05", "B06"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_rededge",
        description="Contraste Red Edge (B06 vs B05)",
        multi_band_order=6,
    ),

    # ------------------------------------------------------------------
    # 10. VARI — Visible Atmospherically Resistant Index (Gitelson 2002)
    #     Indice RGB, utile sans NIR.
    #     Dans le multi-bande (B7).
    # ------------------------------------------------------------------
    "VARI": IndiceSpec(
        name="VARI",
        formula="(B03 - B04) / (B03 + B04 - B02)",
        required_bands=("B02", "B03", "B04"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_vari",
        description="Végétation résistante à l'atmosphère (VARI, Gitelson 2002)",
        multi_band_order=7,
    ),

    # ------------------------------------------------------------------
    # 11. CARBONATE — Carbonate Index
    #     Détection des sols calcaires (SWIR).
    #     Dans le multi-bande (B8).
    # ------------------------------------------------------------------
    "CARBONATE": IndiceSpec(
        name="CARBONATE",
        formula="(B11 - B12) / (B11 + B12)",
        required_bands=("B11", "B12"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_carbonate",
        description="Sols calcaires (Carbonate Index, SWIR)",
        multi_band_order=8,
    ),

    # ------------------------------------------------------------------
    # 12. SI_SOIL — Soil Index (salinity / bare soil)
    #     Détection des sols nus / salins.
    #     Dans le multi-bande (B9).
    # ------------------------------------------------------------------
    "SI_SOIL": IndiceSpec(
        name="SI_SOIL",
        formula="(B04 - B11) / (B04 + B11)",
        required_bands=("B04", "B11"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_si_soil",
        description="Sols nus / salins (Soil Index)",
        multi_band_order=9,
    ),

    # ------------------------------------------------------------------
    # 13. PSRI — Plant Senescence Reflectance Index (Merzlyak 1999)
    #     Détection de la sénescence (jaunissement).
    #     Dans le multi-bande (B10).
    # ------------------------------------------------------------------
    "PSRI": IndiceSpec(
        name="PSRI",
        formula="(B04 - B03) / B06",
        required_bands=("B03", "B04", "B06"),
        theoretical_min=-1.0,
        theoretical_max=1.0,
        calc_func_name="calc_psri",
        description="Sénescence des plantes (Plant Senescence Reflectance Index, Merzlyak 1999)",
        multi_band_order=10,
    ),

    # ------------------------------------------------------------------
    # 14. FCOVER — Fraction of Vegetation Cover (formule simple)
    #     Estimation du couvert végétal (0 = sol nu, 1 = couvert total).
    #     Dans le multi-bande (B11).
    # ------------------------------------------------------------------
    "FCOVER": IndiceSpec(
        name="FCOVER",
        formula="clamp((NDVI - 0.1) / (0.9 - 0.1), 0, 1)",
        required_bands=("B04", "B08"),
        theoretical_min=0.0,
        theoretical_max=1.0,
        calc_func_name="calc_fcover",
        description="Fraction de couvert végétal (formule simple basée sur NDVI)",
        multi_band_order=11,
    ),
}


# ======================================================================
# Fonctions utilitaires
# ======================================================================


def get_indice(name: str) -> IndiceSpec:
    """
    Récupère la spec d'un indice par son nom.

    Raises:
        KeyError: si l'indice n'existe pas.
    """
    name_upper = name.upper()
    if name_upper not in INDICES_REGISTRY:
        raise KeyError(
            f"Indice inconnu : {name!r}. "
            f"Indices disponibles : {sorted(INDICES_REGISTRY.keys())}"
        )
    return INDICES_REGISTRY[name_upper]


def list_indice_names() -> list[str]:
    """Liste les 14 noms d'indices dans l'ordre logique."""
    return list(INDICES_REGISTRY.keys())


def list_required_bands() -> set[str]:
    """Retourne l'union de toutes les bandes requises par au moins un indice."""
    bands: set[str] = set()
    for spec in INDICES_REGISTRY.values():
        bands.update(spec.required_bands)
    return bands


def filter_available_indices(available_bands: set[str]) -> list[str]:
    """
    Retourne la liste des indices calculables avec les bandes disponibles.

    Args:
        available_bands: set des bandes présentes dans la scène (ex: {"B02","B03",...})

    Returns:
        Liste des noms d'indices dont TOUTES les bandes requises sont dispo.

    Exemple:
        >>> filter_available_indices({"B04", "B08"})
        ['NDVI', 'SAVI', 'MSAVI', 'FCOVER']  # ceux qui n'utilisent que B04+B08
    """
    available: list[str] = []
    for name, spec in INDICES_REGISTRY.items():
        if set(spec.required_bands).issubset(available_bands):
            available.append(name)
    return available


def get_missing_bands(indice_name: str, available_bands: set[str]) -> list[str]:
    """
    Retourne les bandes manquantes pour un indice donné.

    Exemple:
        >>> get_missing_bands("NDMI", {"B04", "B08"})
        ['B11']
    """
    spec = get_indice(indice_name)
    return [b for b in spec.required_bands if b not in available_bands]


# ======================================================================
# Mapping pour le COG multi-bande (ordre des bandes 1..11)
# ======================================================================

MULTIBAND_ORDER: dict[int, str] = {
    spec.multi_band_order: name
    for name, spec in INDICES_REGISTRY.items()
    if spec.multi_band_order is not None
}

# Vérification de cohérence au chargement du module
assert len(MULTIBAND_ORDER) == 11, (
    f"Le COG multi-bande doit contenir 11 bandes, "
    f"trouvé : {len(MULTIBAND_ORDER)} → {sorted(MULTIBAND_ORDER.keys())}"
)
assert sorted(MULTIBAND_ORDER.keys()) == list(range(1, 12)), (
    f"Les positions multi-bande doivent être 1..11, "
    f"trouvé : {sorted(MULTIBAND_ORDER.keys())}"
)

SEPARATE_COGS: tuple[str, ...] = tuple(
    name for name, spec in INDICES_REGISTRY.items() if spec.separate_cog
)
assert len(SEPARATE_COGS) == 3, (
    f"On doit avoir 3 COG séparés (NDVI, NDMI, NDWI), trouvé : {SEPARATE_COGS}"
)