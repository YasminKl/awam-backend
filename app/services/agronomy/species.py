"""
Espèces cultivées référencées.

Une espèce porte son type (pérenne ou annuelle) et la liste ordonnée de
ses stades. C'est le point d'entrée du référentiel : calendrier, seuils,
Kc et références de sol sont tous indexés sur ces codes.

Les stades suivent la nomenclature du document agronomique AWAM.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class CropType(str, Enum):
    """
    Pérenne  : le couvert persiste ou revient d'une année sur l'autre.
    Annuelle : cycle complet depuis le semis, replantée chaque saison.
    """

    PERENNIAL = "perennial"
    ANNUAL = "annual"


@dataclass(frozen=True)
class SpeciesSpec:
    code: str
    label: str
    crop_type: CropType
    #: Stades dans l'ordre du cycle, codes utilisés par le calendrier
    stages: tuple[str, ...]
    #: Vrai si l'espèce perd son feuillage l'hiver : un indice bas y est
    #: alors normal, à ne pas confondre avec un stress.
    deciduous: bool = False
    #: Vrai pour les arbres fruitiers : leur première récolte n'arrive
    #: qu'après plusieurs années. La récolte prévue n'est alors calculée
    #: que si l'utilisateur indique que le verger est en production.
    orchard: bool = False


SPECIES: dict[str, SpeciesSpec] = {
    # ---------------- Arboriculture ----------------
    "olivier": SpeciesSpec(
        code="olivier", label="Olivier", crop_type=CropType.PERENNIAL,
        orchard=True,
        stages=("repos", "debourrement", "floraison", "nouaison",
                "maturation", "recolte"),
    ),
    "amandier": SpeciesSpec(
        code="amandier", label="Amandier", crop_type=CropType.PERENNIAL,
        deciduous=True, orchard=True,
        stages=("dormance", "debourrement", "floraison", "grossissement",
                "maturation", "recolte", "post_recolte"),
    ),
    "caroubier": SpeciesSpec(
        code="caroubier", label="Caroubier", crop_type=CropType.PERENNIAL,
        orchard=True,
        stages=("repos", "croissance", "floraison", "formation_gousses",
                "maturation", "recolte"),
    ),

    # ---------------- Plantes aromatiques et médicinales ----------------
    "romarin": SpeciesSpec(
        code="romarin", label="Romarin", crop_type=CropType.PERENNIAL,
        stages=("repos", "croissance", "floraison", "pre_recolte",
                "recolte", "reprise_coupe"),
    ),
    "lavande": SpeciesSpec(
        code="lavande", label="Lavande", crop_type=CropType.PERENNIAL,
        stages=("repos", "reprise", "floraison", "recolte", "reprise_coupe"),
    ),
    "sauge": SpeciesSpec(
        code="sauge", label="Sauge officinale", crop_type=CropType.PERENNIAL,
        stages=("repos", "croissance", "floraison", "pre_recolte",
                "recolte", "reprise_coupe"),
    ),
    "menthe": SpeciesSpec(
        code="menthe", label="Menthe", crop_type=CropType.PERENNIAL,
        stages=("repos", "croissance", "pre_recolte", "recolte"),
    ),
    "geranium": SpeciesSpec(
        code="geranium", label="Géranium rosat", crop_type=CropType.PERENNIAL,
        stages=("repos", "croissance", "recolte", "reprise_coupe"),
    ),
    "verveine": SpeciesSpec(
        code="verveine", label="Verveine odorante", crop_type=CropType.PERENNIAL,
        deciduous=True,
        stages=("repos", "croissance", "floraison", "recolte", "reprise_coupe"),
    ),
    "citronnelle": SpeciesSpec(
        code="citronnelle", label="Citronnelle", crop_type=CropType.PERENNIAL,
        stages=("ralentissement", "croissance", "pre_recolte", "recolte"),
    ),
    "thym": SpeciesSpec(
        code="thym", label="Thym", crop_type=CropType.PERENNIAL,
        stages=("repos", "croissance", "prefloraison", "floraison",
                "recolte", "reprise_coupe"),
    ),

    # ---------------- Cultures annuelles ----------------
    "basilic": SpeciesSpec(
        code="basilic", label="Basilic", crop_type=CropType.ANNUAL,
        stages=("semis", "croissance", "pre_recolte", "recolte", "fin_cycle"),
    ),
}


#: Libellés lisibles des stades, partagés par toutes les espèces
STAGE_LABELS: dict[str, str] = {
    "repos": "Repos hivernal",
    "dormance": "Dormance",
    "ralentissement": "Ralentissement",
    "debourrement": "Débourrement",
    "reprise": "Reprise végétative",
    "croissance": "Croissance active",
    "prefloraison": "Pré-floraison",
    "floraison": "Floraison",
    "nouaison": "Nouaison et grossissement",
    "formation_gousses": "Développement des gousses",
    "grossissement": "Grossissement du fruit",
    "maturation": "Maturation",
    "pre_recolte": "Pré-récolte",
    "recolte": "Récolte",
    "post_recolte": "Après récolte",
    "reprise_coupe": "Reprise après coupe",
    "semis": "Semis ou plantation",
    "fin_cycle": "Fin de cycle",
}


def get_species(code: str) -> SpeciesSpec | None:
    return SPECIES.get(code.lower())


def list_species() -> list[SpeciesSpec]:
    return sorted(SPECIES.values(), key=lambda s: s.label)


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage.replace("_", " ").capitalize())


def is_orchard(code: str) -> bool:
    """Vrai pour les arbres fruitiers (olivier, amandier, caroubier)."""
    spec = SPECIES.get(code.lower())
    return bool(spec and spec.orchard)


def is_deciduous(code: str) -> bool:
    """
    Vrai si l'espèce perd ses feuilles l'hiver. Un NDVI bas en saison
    froide y est alors normal et ne doit pas déclencher d'alerte.
    """
    spec = SPECIES.get(code.lower())
    return bool(spec and spec.deciduous)


# ======================================================================
# Variétés
# ======================================================================


class VarietyOrigin(str, Enum):
    """Origine d'une variété, pour regrouper la liste dans le formulaire."""

    TUNISIAN = "tunisian"
    SPANISH = "spanish"
    INTERNATIONAL = "international"


@dataclass(frozen=True)
class Variety:
    name: str
    origin: VarietyOrigin


#: Libellés des groupes, dans l'ordre d'affichage du formulaire
ORIGIN_LABELS: dict[VarietyOrigin, str] = {
    VarietyOrigin.TUNISIAN: "Variétés tunisiennes",
    VarietyOrigin.SPANISH: "Variétés espagnoles",
    VarietyOrigin.INTERNATIONAL: "Types cultivés",
}

_TN = VarietyOrigin.TUNISIAN
_ES = VarietyOrigin.SPANISH
_INT = VarietyOrigin.INTERNATIONAL

#: Variétés, ou types cultivés, proposés pour chaque espèce.
#: Pour les plantes aromatiques, il s'agit de types (espèces proches ou
#: chémotypes) plutôt que de variétés au sens strict : c'est ce qui
#: détermine le rendement et la qualité de l'huile essentielle.
#: Une espèce sans liste (caroubier) se saisit librement, et l'option
#: « Autre » reste toujours possible côté formulaire.
VARIETIES: dict[str, tuple[Variety, ...]] = {
    "olivier": (
        Variety("Chemlali", _TN), Variety("Chétoui", _TN),
        Variety("Oueslati", _TN), Variety("Zalmati", _TN),
        Variety("Zarrazi", _TN), Variety("Gerboui", _TN),
        Variety("Meski", _TN),
        Variety("Arbequina", _ES), Variety("Arbosana", _ES),
        Variety("Picual", _ES),
    ),
    "amandier": (
        Variety("Achaak", _TN), Variety("Zahaf", _TN),
        Variety("Ksontini", _TN), Variety("Blanco", _TN),
        Variety("Mazetto", _TN),
    ),
    "lavande": (
        Variety("Lavande stoechas", _TN),
        Variety("Lavande vraie", _INT), Variety("Lavandin Grosso", _INT),
        Variety("Lavandin Super", _INT),
    ),
    "menthe": (
        Variety("Menthe verte", _TN), Variety("Menthe pouliot", _TN),
        Variety("Menthe poivrée", _INT),
    ),
    "thym": (
        Variety("Thym à têtes", _TN), Variety("Thym commun", _INT),
    ),
    "romarin": (Variety("Romarin officinal", _TN),),
    "sauge": (Variety("Sauge officinale", _INT),),
    "geranium": (
        Variety("Géranium rosat type Bourbon", _INT),
        Variety("Géranium rosat type Égypte", _INT),
    ),
    "basilic": (
        Variety("Petit basilic", _TN), Variety("Grand vert", _INT),
    ),
    "citronnelle": (
        Variety("Citronnelle de l'Inde", _INT),
        Variety("Citronnelle de Java", _INT),
    ),
    "verveine": (Variety("Verveine odorante", _INT),),
}


def get_varieties(code: str) -> tuple[Variety, ...]:
    return VARIETIES.get(code.lower(), ())


def is_annual(code: str) -> bool:
    """
    Vrai pour une culture annuelle (basilic). Après sa récolte, le cycle
    est terminé : l'espèce peut être déclarée à nouveau sur la parcelle.
    Une pérenne, elle, reste en place après la récolte.
    """
    spec = SPECIES.get(code.lower())
    return bool(spec and spec.crop_type == CropType.ANNUAL)


# ======================================================================
# Lecture des cultures saisies sur une parcelle
# ======================================================================


def _normalize(text: str) -> str:
    """Minuscules, sans accents ni espaces superflus."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def _build_aliases() -> dict[str, str]:
    """
    Toutes les écritures reconnues pour chaque espèce : code, libellé,
    singulier, pluriel, et le premier mot du libellé (« sauge » pour
    « Sauge officinale »).
    """
    aliases: dict[str, str] = {}
    for spec in SPECIES.values():
        for form in {spec.code, spec.label, spec.label.split()[0]}:
            base = _normalize(form)
            aliases[base] = spec.code
            aliases[base + "s"] = spec.code
            aliases[base + "x"] = spec.code
    # Écritures courantes du nom du fruit plutôt que de l'arbre
    aliases.update({
        "olive": "olivier", "olives": "olivier",
        "amande": "amandier", "amandes": "amandier",
        "caroube": "caroubier", "caroubes": "caroubier",
        "geranium rosat": "geranium", "rose geranium": "geranium",
        "verveine odorante": "verveine",
    })
    return aliases


_ALIASES = _build_aliases()

#: Séparateurs acceptés entre deux cultures : , ; / + & et « et »
_SEPARATORS = re.compile(r"\s*(?:,|;|/|\+|&|\bet\b)\s*", re.IGNORECASE)


def parse_cultures(text: str | None) -> tuple[list[str], list[str]]:
    """
    Découpe le champ culture d'une parcelle en espèces du référentiel.

    "Olivier, Lavande"   → (["olivier", "lavande"], [])
    "oliviers + thym"    → (["olivier", "thym"], [])
    "Olivier et figuier" → (["olivier"], ["figuier"])

    Returns:
        (codes reconnus sans doublon, textes non reconnus)
    """
    if not text or not text.strip():
        return [], []

    found: list[str] = []
    unknown: list[str] = []
    for part in _SEPARATORS.split(text):
        part = part.strip()
        if not part:
            continue
        code = _ALIASES.get(_normalize(part))
        if code:
            if code not in found:
                found.append(code)
        else:
            unknown.append(part)
    return found, unknown




# ======================================================================
# Délai entre la plantation (plants repiqués) et la PREMIÈRE récolte
# ======================================================================
#
# Valeurs retenues après recherche croisée (Perplexity, Mistral,
# référentiels AWAM du projet). Règle : une valeur est retenue si au moins
# deux sources indépendantes concordent ; à défaut, la source la plus
# proche de la Tunisie ; à égalité, la valeur la plus prudente.
#
# STATUT : toutes les valeurs sont « à confirmer ». Aucune référence
# tunisienne officielle chiffrée n'a été trouvée, et les phrases citées
# viennent de résumés d'outils d'IA, pas d'une lecture directe des
# documents. Elles seront remplacées par les délais réels observés sur les
# parcelles (date de plantation → date de première récolte) ou par l'avis
# d'un expert tunisien (CRDA, INRAT).
#
# Arbres fruitiers : pas de délai ici. Leur récolte prévue dépend de la
# case « verger en production » saisie par l'utilisateur.


@dataclass(frozen=True)
class FirstHarvestDelay:
    """Délai avant la première récolte, avec sa justification."""

    days: int
    #: Sources qui contiennent ou recoupent la valeur
    sources: str
    #: Nombre de sources concordantes et statut de vérification
    confidence: str


FIRST_HARVEST_DELAYS: dict[str, FirstHarvestDelay] = {
    "lavande": FirstHarvestDelay(
        365,
        "Fiche SUAMME (Languedoc-Roussillon sec) : « Pas de récolte la première année » ; "
        "CRA Occitanie (lavandin) : « ne produit pas les douze premiers mois suivant la plantation » ; "
        "référentiel AWAM : installation 0–1 an, floraison après 1 an",
        "plusieurs sources concordantes, à confirmer",
    ),
    "romarin": FirstHarvestDelay(
        365,
        "Fiche SUAMME (Languedoc-Roussillon sec) : « la première récolte intervient en année 2 » ; "
        "référentiel AWAM : installation 0–1 an, pré-récolte après 1 an",
        "plusieurs sources concordantes, à confirmer",
    ),
    "thym": FirstHarvestDelay(
        365,
        "Fiche SUAMME (Languedoc-Roussillon sec) : « la 1ère récolte intervient en année 2 »",
        "source unique, à confirmer",
    ),
    "sauge": FirstHarvestDelay(
        75,
        "UW–Madison Extension : « Sage takes about 75 days from planting to first harvest » ; "
        "Permapotes : semis d'avril à juin, récolte de juin à octobre la même année",
        "plusieurs sources concordantes, à confirmer",
    ),
    "menthe": FirstHarvestDelay(
        90,
        "Référentiel AWAM : installation 0–3 mois ; Culture et production de la menthe (Maroc) : "
        "« La 1ère coupe a lieu après 60 à 70 jours du début de la plantation » ; "
        "source indienne : 100 à 120 jours. Valeur du référentiel AWAM, dans la fourchette des sources",
        "plusieurs sources concordantes, à confirmer",
    ),
    "geranium": FirstHarvestDelay(
        182,
        "Mémoire, université de Tlemcen (Algérie) : « La récolte des feuilles et des tiges se fait "
        "à partir de 6 mois » ; J. Mater. Environ. Sci. : 6 à 8 mois ; "
        "référentiel AWAM : installation 0–6 mois",
        "plusieurs sources concordantes, à confirmer",
    ),
    "verveine": FirstHarvestDelay(
        0,
        "Bio Nouvelle-Aquitaine : « 1ère année : 1 coupe », récolte mi-juillet ; "
        "première récolte en juillet selon une seconde source : récolte possible dès la première saison",
        "plusieurs sources concordantes, à confirmer",
    ),
    "citronnelle": FirstHarvestDelay(
        182,
        "Atlaseeds (Maroc) : « 4 à 6 mois pour une première récolte » ; PROSEA : 6 à 8 mois ; "
        "Useful Tropical Plants : 120 à 240 jours. Valeur commune aux trois",
        "plusieurs sources concordantes, à confirmer",
    ),
    "basilic": FirstHarvestDelay(
        45,
        "Industrial Crops and Products : première récolte « at 40 DAT » ; "
        "extensions universitaires : 30 à 45 jours",
        "plusieurs sources concordantes, à confirmer",
    ),
}


def first_harvest_delay(code: str) -> FirstHarvestDelay | None:
    """Délai avant la première récolte, ou None (arbres fruitiers, espèce inconnue)."""
    return FIRST_HARVEST_DELAYS.get(code)