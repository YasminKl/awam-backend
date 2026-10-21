"""
Coefficients culturaux (Kc) et références d'analyse de sol.

Le Kc traduit l'évapotranspiration de référence (ET0, calculée depuis la
météo) en besoin réel de la culture :

    ETc = ET0 × Kc(stade)
    besoin net = ETc − pluie

Les références de sol servent à interpréter les analyses de laboratoire
saisies par l'utilisateur (pH, salinité, matière organique).

Source : document de référence AWAM, valeurs FAO-56 adaptées au contexte
tunisien. Les Kc des plantes aromatiques ne figurent pas dans la FAO-56
classique : à ajuster après observation.
"""

from __future__ import annotations

from dataclasses import dataclass


# ======================================================================
# Coefficients culturaux
# ======================================================================


@dataclass(frozen=True)
class KcSet:
    """Kc aux quatre phases du cycle FAO-56."""

    initial: float
    development: float
    mid_season: float
    late_season: float


KC: dict[str, KcSet] = {
    "amandier":    KcSet(0.40, 0.70, 1.00, 0.70),
    "olivier":     KcSet(0.40, 0.45, 0.50, 0.45),   # extensif, cas par défaut
    "caroubier":   KcSet(0.40, 0.55, 0.70, 0.60),   # adulte
    "romarin":     KcSet(0.30, 0.45, 0.60, 0.45),
    "lavande":     KcSet(0.30, 0.50, 0.75, 0.50),
    "sauge":       KcSet(0.40, 0.65, 0.90, 0.65),
    "menthe":      KcSet(0.50, 0.80, 1.05, 0.90),
    "geranium":    KcSet(0.40, 0.70, 0.95, 0.70),
    "verveine":    KcSet(0.40, 0.65, 0.90, 0.65),
    "citronnelle": KcSet(0.50, 0.80, 1.05, 0.90),
    "basilic":     KcSet(0.50, 0.80, 1.05, 0.90),
    "thym":        KcSet(0.30, 0.45, 0.65, 0.45),
}

#: Variantes : olivier intensif et caroubier jeune
KC_VARIANTS: dict[str, KcSet] = {
    "olivier_intensif": KcSet(0.50, 0.65, 0.70, 0.60),
    "caroubier_jeune":  KcSet(0.30, 0.45, 0.60, 0.50),
}


#: Correspondance stade → phase FAO, sur les neuf stades du document AWAM
#: (voir calendar.py). Un stade absent prend "mid_season", la phase de plus
#: forte demande, par prudence.
STAGE_TO_PHASE: dict[str, str] = {
    # Phase initiale : couvert absent ou minimal
    "dormance": "initial",
    "debourrement": "initial",
    # Développement : le couvert se met en place
    "croissance": "development",
    # Mi-saison : demande maximale
    "floraison": "mid_season",
    "nouaison": "mid_season",
    "developpement": "mid_season",
    # Fin de saison : la demande retombe
    "maturation": "late_season",
    "recolte": "late_season",
    # "apres_recolte" dépend de la plante : voir _phase()
}


def _phase(species: str, stage: str) -> str:
    """
    Phase FAO d'un stade pour une espèce.

    L'APRÈS-RÉCOLTE regroupe deux situations qui avaient chacune leur code
    avant la nomenclature à neuf stades, et garde leur phase d'alors :
      - aromatique pérenne : la plante repousse après la coupe (ancien
        « reprise après coupe ») → développement ;
      - arbre fruitier ou annuelle : la saison est finie (anciens « après
        récolte » et « fin de cycle ») → fin de saison.
    """
    if stage == "apres_recolte":
        from app.services.agronomy.species import is_annual, is_orchard

        if is_orchard(species) or is_annual(species):
            return "late_season"
        return "development"
    return STAGE_TO_PHASE.get(stage, "mid_season")


def get_kc(species: str, stage: str) -> float | None:
    """Coefficient cultural de l'espèce au stade donné."""
    kc_set = KC.get(species.lower())
    if not kc_set:
        return None
    return getattr(kc_set, _phase(species.lower(), stage))


def get_kc_for_stages(species: str, stages) -> float | None:
    """
    Coefficient cultural quand PLUSIEURS stades sont actifs à la même date
    (calendar.get_stages_at().codes) : le plus élevé.

    Le stade principal est choisi pour interpréter le satellite (la récolte
    d'abord, elle explique la chute d'indice). Pour l'eau, ce n'est pas le
    bon critère : le thym est récolté en juin pendant qu'il fleurit encore,
    phase de plus forte demande. Prendre le Kc le plus élevé des stades
    actifs suit la règle de prudence de ce module : ne pas sous-irriguer.
    """
    if isinstance(stages, str):
        stages = (stages,)
    values = [kc for kc in (get_kc(species, st) for st in stages) if kc is not None]
    return max(values) if values else None


# ======================================================================
# Références d'analyse de sol
# ======================================================================


@dataclass(frozen=True)
class SoilReference:
    """Plages favorables pour une espèce. None = pas de référence."""

    ph_min: float
    ph_max: float
    #: Salinité (ECe) à ne pas dépasser, en dS/m
    salinity_max: float
    #: Matière organique minimale souhaitée, en %
    organic_matter_min: float
    note: str = ""


SOIL_REFERENCES: dict[str, SoilReference] = {
    "amandier": SoilReference(6.5, 8.0, 1.5, 1.5,
        "Éviter salinité, asphyxie et sols très compacts"),
    "olivier": SoilReference(6.5, 8.5, 3.0, 1.0,
        "Tolère les sols alcalins et pauvres ; pH > 8,5 réduit la croissance"),
    "caroubier": SoilReference(6.5, 8.2, 3.0, 1.0,
        "Privilégier drainage et profondeur du sol"),
    "romarin": SoilReference(6.0, 8.0, 2.0, 1.0,
        "Exige surtout un sol bien drainé"),
    "lavande": SoilReference(6.0, 8.0, 2.0, 1.0,
        "Éviter excès d'eau et sols lourds"),
    "sauge": SoilReference(6.0, 8.0, 2.0, 1.5,
        "Sensible à l'engorgement"),
    "menthe": SoilReference(6.0, 7.5, 1.3, 2.0,
        "Besoin d'humidité mais sensibilité à la salinité"),
    "geranium": SoilReference(6.0, 7.5, 1.5, 1.5,
        "Fertilité et drainage importants"),
    "verveine": SoilReference(6.0, 7.5, 1.5, 1.5,
        "Éviter stress salin et déficit prolongé"),
    "citronnelle": SoilReference(5.5, 7.5, 1.5, 2.0,
        "Forte demande en eau et sensibilité au sel"),
    "basilic": SoilReference(6.0, 7.5, 1.3, 2.0,
        "Culture sensible à la salinité et au stress hydrique"),
    "thym": SoilReference(6.0, 8.0, 2.0, 1.0,
        "Sol drainant, éviter l'excès d'azote"),
}


def get_soil_reference(species: str) -> SoilReference | None:
    return SOIL_REFERENCES.get(species.lower())


def evaluate_soil(
    species: str,
    *,
    ph: float | None = None,
    salinity: float | None = None,
    organic_matter: float | None = None,
) -> list[dict]:
    """
    Compare une analyse de sol aux références de l'espèce.

    Renvoie une ligne par paramètre renseigné :
      {param, value, level, reference, message}
    `level` vaut "green", "yellow" ou "red".
    """
    ref = get_soil_reference(species)
    if not ref:
        return []

    rows: list[dict] = []

    if ph is not None:
        if ref.ph_min <= ph <= ref.ph_max:
            level, msg = "green", "pH favorable"
        elif ph > ref.ph_max + 0.5 or ph < ref.ph_min - 0.5:
            level, msg = "red", "pH défavorable, croissance pénalisée"
        else:
            level, msg = "yellow", "pH en limite, à surveiller"
        rows.append({
            "param": "pH", "value": ph, "level": level,
            "reference": f"{ref.ph_min:.1f}–{ref.ph_max:.1f}".replace(".", ","),
            "message": msg,
        })

    if salinity is not None:
        if salinity <= ref.salinity_max:
            level, msg = "green", "Salinité acceptable"
        elif salinity <= ref.salinity_max * 1.5:
            level, msg = "yellow", "Salinité en limite, surveiller l'eau d'irrigation"
        else:
            level, msg = "red", "Salinité excessive pour cette culture"
        rows.append({
            "param": "Salinité", "value": salinity, "level": level,
            "reference": f"< {ref.salinity_max:.1f} dS/m".replace(".", ","),
            "message": msg,
        })

    if organic_matter is not None:
        if organic_matter >= ref.organic_matter_min:
            level, msg = "green", "Matière organique suffisante"
        elif organic_matter >= ref.organic_matter_min * 0.6:
            level, msg = "yellow", "Matière organique en limite basse"
        else:
            level, msg = "red", "Matière organique insuffisante, prévoir un apport"
        rows.append({
            "param": "Matière organique", "value": organic_matter, "level": level,
            "reference": f"> {ref.organic_matter_min:.1f} %".replace(".", ","),
            "message": msg,
        })

    return rows