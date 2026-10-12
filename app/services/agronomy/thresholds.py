"""
Seuils d'interprétation des indices, par culture, par stade et par âge.

SOURCE : document de référence agronomique AWAM
("choix et utilité des indices spectraux pour cultures"), établi par
l'équipe agronomique du projet pour les parcelles de Makthar et Sidi
Mechreg.

Trois niveaux, repris du document :
  low      — sous le seuil bas : anomalie à vérifier
  normal   — dans l'intervalle attendu
  high     — au-dessus : développement excellent (pas une alerte)

L'ÂGE est déterminant pour les pérennes : un jeune olivier couvre peu de
sol, son NDVI est structurellement bas sans que ce soit un problème. Le
document décline donc les seuils par tranche d'âge là où c'est pertinent.

⚠️  Ces seuils sont des RÉFÉRENCES DU PROJET, non des valeurs publiées.
    Ils doivent être calibrés sur les observations terrain au fil des
    saisons. Le moteur de diagnostic s'appuie également sur la VARIATION
    de chaque parcelle, qui ne dépend d'aucun seuil.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Threshold:
    """
    Seuil d'un indice à un stade donné.

    `low` et `high` délimitent l'intervalle normal :
        valeur < low   → "low"     (anomalie)
        low ≤ v ≤ high → "normal"
        valeur > high  → "high"    (excellent)

    `inverted` pour le MSI, dont une valeur ÉLEVÉE signale le stress :
    les comparaisons sont alors inversées.
    """

    indice: str
    low: float
    high: float
    #: MSI : la hausse signale le stress, pas la baisse
    inverted: bool = False
    #: Unité d'affichage ("" pour les indices, "%" pour le FCover)
    unit: str = ""

    def evaluate(self, value: float) -> str:
        if self.inverted:
            if value > self.high:
                return "low"      # MSI élevé = stress
            if value < self.low:
                return "high"     # MSI bas = bonne hydratation
            return "normal"

        if value < self.low:
            return "low"
        if value > self.high:
            return "high"
        return "normal"

    @property
    def label(self) -> str:
        """Rappel du seuil, affiché sous la valeur."""
        fmt = lambda v: f"{v:g}".replace(".", ",")
        if self.inverted:
            return f"alerte > {fmt(self.high)}{self.unit}"
        return f"normal {fmt(self.low)}–{fmt(self.high)}{self.unit}"


@dataclass(frozen=True)
class StageRule:
    """
    Règle complète d'un stade : les indices à surveiller, l'explication
    agronomique et la recommandation, toutes deux issues du document.

    `age_min` / `age_max` en années : la règle ne s'applique qu'aux
    plantes de cette tranche. None = tous âges.
    """

    stage: str
    thresholds: tuple[Threshold, ...]
    explanation: str = ""
    recommendation: str = ""
    age_min: float | None = None
    age_max: float | None = None

    def matches_age(self, age_years: float | None) -> bool:
        """Vrai si la règle s'applique à cet âge (ou si l'âge est inconnu)."""
        if self.age_min is None and self.age_max is None:
            return True
        if age_years is None:
            return False   # règle par âge mais âge inconnu : on l'écarte
        if self.age_min is not None and age_years < self.age_min:
            return False
        if self.age_max is not None and age_years >= self.age_max:
            return False
        return True


# Repli hydrique : hors des stades décrits, un stress reste détectable
DEFAULT_RULE = StageRule(
    stage="*",
    thresholds=(
        Threshold("NDMI", 0.15, 0.35),
        Threshold("MSI", 1.00, 1.30, inverted=True),
    ),
    explanation="Hors stade de suivi rapproché : surveillance hydrique générale.",
    recommendation="Comparer avec l'historique de la parcelle.",
)


#: Indices AFFICHÉS mais NON comparés à leur seuil, avec la raison
#: montrée à l'utilisateur. Les seuils du document restent écrits dans
#: RULES, pour la traçabilité, et seront réactivés une fois clarifiés.
#:
#: FCOVER : le document donne des seuils de fraction de couvert réelle
#: (en %), alors qu'AWAM calcule le FCOVER comme une transformation
#: linéaire du NDVI, (NDVI - 0,1) / 0,8. Les deux ne sont pas comparables :
#: pour un olivier adulte, un NDVI normal (0,45 à 0,70) donne un FCOVER
#: de 44 à 75 %, quand le document attend 65 à 85 %. Le comparer au seuil
#: produisait donc des alertes contradictoires avec le NDVI. En attente
#: de la définition exacte retenue par l'agronome.
#:
#: MSI : il se déduit exactement du NDMI, MSI = (1 - NDMI) / (1 + NDMI),
#: car les deux utilisent les mêmes bandes (B08 et B11). Leurs seuils du
#: document se contredisent : un NDMI normal (0,15 à 0,35) correspond à
#: un MSI de 0,48 à 0,74, jugé « excellent », et un MSI normal (1,00 à
#: 1,30) correspond à un NDMI de 0 à -0,13, jugé en stress. Aucune
#: parcelle ne pouvait donc être normale sur les deux. Le NDMI, présent
#: dans toutes les règles hydriques, reste seul évalué, en attendant que
#: l'agronome harmonise les deux intervalles.
NOT_EVALUATED: dict[str, str] = {
    "FCOVER": "non évalué : définition à préciser avec l'agronome",
    "MSI": "non évalué : redondant avec le NDMI, seuils à harmoniser avec l'agronome",
}


RULES: dict[str, tuple[StageRule, ...]] = {

    # ==================================================================
    # OLIVIER — seuils déclinés par âge au débourrement
    # ==================================================================
    "olivier": (
        StageRule(
            stage="debourrement", age_min=0, age_max=3,
            thresholds=(
                Threshold("NDVI", 0.30, 0.50),
                Threshold("SAVI", 0.25, 0.45),
                Threshold("FCOVER", 25, 40, unit="%"),
            ),
            explanation="Reprise végétative après le repos hivernal. Chez les jeunes arbres, une faible biomasse est normale.",
            recommendation="Vérifier irrigation, mortalité et fertilisation azotée.",
        ),
        StageRule(
            stage="debourrement", age_min=3, age_max=10,
            thresholds=(
                Threshold("NDVI", 0.35, 0.60),
                Threshold("FCOVER", 45, 65, unit="%"),
            ),
            explanation="Développement rapide de la canopée.",
            recommendation="Contrôler la croissance annuelle.",
        ),
        StageRule(
            stage="debourrement", age_min=10,
            thresholds=(
                Threshold("NDVI", 0.45, 0.70),
                Threshold("FCOVER", 65, 85, unit="%"),
            ),
            explanation="Canopée mature.",
            recommendation="Comparer avec les années précédentes.",
        ),
        StageRule(
            stage="floraison",
            thresholds=(
                Threshold("NDRE", 0.20, 0.35),
                Threshold("FCOVER", 60, 80, unit="%"),
            ),
            explanation="Le NDRE mesure la chlorophylle et l'activité photosynthétique, nécessaires à la floraison.",
            recommendation="Vérifier la nutrition azotée et l'irrigation.",
        ),
        StageRule(
            stage="nouaison",
            thresholds=(
                Threshold("NDMI", 0.15, 0.35),
                Threshold("MSI", 1.00, 1.30, inverted=True),
                Threshold("FCOVER", 70, 90, unit="%"),
            ),
            explanation="Stade critique : la disponibilité en eau détermine le grossissement des olives.",
            recommendation="Ajuster l'irrigation selon l'ET₀.",
        ),
        StageRule(
            stage="maturation",
            thresholds=(
                Threshold("MSI", 1.00, 1.30, inverted=True),
                Threshold("NDVI", 0.45, 0.70),
                Threshold("FCOVER", 65, 85, unit="%"),
            ),
            explanation="Contrôle de la sénescence et de la qualité de maturation.",
            recommendation="Optimiser la date de récolte.",
        ),
    ),
        # ==================================================================
    # AMANDIER — caduc : NDVI bas en hiver est normal
    # ==================================================================
    "amandier": (
        StageRule(
            stage="debourrement", age_min=0, age_max=3,
            thresholds=(Threshold("NDVI", 0.30, 0.50),),
            explanation="Début de croissance printanière après défoliation hivernale.",
            recommendation="Vérifier la reprise végétative.",
        ),
        StageRule(
            stage="debourrement", age_min=3,
            thresholds=(Threshold("NDVI", 0.35, 0.60),),
            explanation="Reprise d'un verger établi après défoliation hivernale.",
            recommendation="Vérifier la reprise végétative.",
        ),
        StageRule(
            stage="floraison",
            thresholds=(Threshold("NDRE", 0.20, 0.35),),
            explanation="Floraison sensible aux stress climatiques, notamment au gel.",
            recommendation="Contrôler la nutrition et l'humidité.",
        ),
        StageRule(
            stage="grossissement",
            thresholds=(
                Threshold("NDMI", 0.15, 0.35),
                Threshold("MSI", 1.00, 1.30, inverted=True),
            ),
            explanation="Développement du fruit dépendant de l'eau.",
            recommendation="Irrigation raisonnée.",
        ),
    ),

    # ==================================================================
    # CAROUBIER
    # ==================================================================
    "caroubier": (
        StageRule(
            stage="croissance", age_min=0, age_max=5,
            thresholds=(
                Threshold("NDVI", 0.35, 0.60),
                Threshold("FCOVER", 30, 50, unit="%"),
            ),
            explanation="Développement progressif de la couronne, croissance lente des jeunes plants.",
            recommendation="Vérifier l'installation.",
        ),
        StageRule(
            stage="croissance", age_min=5,
            thresholds=(
                Threshold("NDVI", 0.40, 0.65),
                Threshold("FCOVER", 55, 75, unit="%"),
            ),
            explanation="Couronne établie.",
            recommendation="Comparer avec les arbres de même âge.",
        ),
        StageRule(
            stage="formation_gousses",
            thresholds=(
                Threshold("NDMI", 0.15, 0.35),
                Threshold("FCOVER", 60, 80, unit="%"),
            ),
            explanation="Le remplissage des gousses dépend fortement du statut hydrique.",
            recommendation="Ajuster l'irrigation si nécessaire.",
        ),
    ),

    # ==================================================================
    # ROMARIN
    # ==================================================================
    "romarin": (
        StageRule(
            stage="croissance", age_min=0, age_max=1,
            thresholds=(
                Threshold("NDVI", 0.30, 0.55),
                Threshold("SAVI", 0.25, 0.50),
                Threshold("FCOVER", 30, 50, unit="%"),
            ),
            explanation="Installation lente après plantation. Le SAVI réduit l'effet du sol nu.",
            recommendation="Fertilisation légère et irrigation.",
        ),
        StageRule(
            stage="croissance", age_min=1,
            thresholds=(
                Threshold("NDVI", 0.35, 0.60),
                Threshold("SAVI", 0.30, 0.55),
                Threshold("FCOVER", 50, 80, unit="%"),
            ),
            explanation="Production de biomasse sur plantation établie.",
            recommendation="Fertilisation légère et irrigation.",
        ),
        StageRule(
            stage="pre_recolte",
            thresholds=(
                Threshold("NDMI", 0.15, 0.35),
                Threshold("FCOVER", 65, 90, unit="%"),
            ),
            explanation="Le stress hydrique réduit la qualité des huiles essentielles.",
            recommendation="Irrigation avant récolte.",
        ),
    ),

    # ==================================================================
    # LAVANDE
    # ==================================================================
    "lavande": (
        StageRule(
            stage="reprise", age_min=0, age_max=1,
            thresholds=(
                Threshold("NDVI", 0.30, 0.55),
                Threshold("FCOVER", 25, 45, unit="%"),
            ),
            explanation="Jeune plantation, faible couverture initiale.",
            recommendation="Vérifier l'homogénéité des plants.",
        ),
        StageRule(
            stage="reprise", age_min=1,
            thresholds=(
                Threshold("NDVI", 0.35, 0.60),
                Threshold("FCOVER", 45, 75, unit="%"),
            ),
            explanation="Développement végétatif sur plantation établie.",
            recommendation="Vérifier l'homogénéité des plants.",
        ),
        StageRule(
            stage="floraison",
            thresholds=(
                Threshold("NDRE", 0.20, 0.35),
                Threshold("FCOVER", 60, 85, unit="%"),
            ),
            explanation="La chlorophylle influence directement la production florale.",
            recommendation="Fertilisation équilibrée.",
        ),
    ),

    # ==================================================================
    # SAUGE OFFICINALE
    # ==================================================================
    "sauge": (
        StageRule(
            stage="croissance",
            thresholds=(
                Threshold("NDVI", 0.30, 0.55),
                Threshold("FCOVER", 50, 75, unit="%"),
            ),
            explanation="Production foliaire.",
            recommendation="Vérifier l'irrigation.",
        ),
        StageRule(
            stage="pre_recolte",
            thresholds=(
                Threshold("NDMI", 0.15, 0.35),
                Threshold("FCOVER", 65, 85, unit="%"),
            ),
            explanation="L'eau influence la biomasse et la qualité avant récolte.",
            recommendation="Réduire le stress hydrique.",
        ),
    ),

    # ==================================================================
    # MENTHE — très exigeante en eau
    # ==================================================================
    "menthe": (
        StageRule(
            stage="croissance",
            thresholds=(
                Threshold("NDVI", 0.40, 0.70),
                Threshold("FCOVER", 75, 95, unit="%"),
            ),
            explanation="Culture très exigeante en eau, forte capacité de couverture par stolons.",
            recommendation="Irrigation fréquente.",
        ),
        StageRule(
            stage="pre_recolte",
            thresholds=(
                Threshold("NDMI", 0.20, 0.40),
                Threshold("FCOVER", 85, 98, unit="%"),
            ),
            explanation="Le stress réduit fortement la biomasse foliaire.",
            recommendation="Maintenir une humidité élevée.",
        ),
    ),
        # ==================================================================
    # GÉRANIUM ROSAT
    # ==================================================================
    "geranium": (
        StageRule(
            stage="croissance",
            thresholds=(
                Threshold("NDVI", 0.35, 0.60),
                Threshold("FCOVER", 70, 95, unit="%"),
            ),
            explanation="Production végétative directement liée au rendement en huile essentielle.",
            recommendation="Optimiser irrigation et nutrition.",
        ),
    ),

    # ==================================================================
    # VERVEINE ODORANTE
    # ==================================================================
    "verveine": (
        StageRule(
            stage="croissance",
            thresholds=(Threshold("NDVI", 0.35, 0.60),),
            explanation="Développement foliaire.",
            recommendation="Contrôle de la biomasse.",
        ),
    ),

    # ==================================================================
    # CITRONNELLE
    # ==================================================================
    "citronnelle": (
        StageRule(
            stage="croissance",
            thresholds=(Threshold("NDVI", 0.40, 0.70),),
            explanation="Forte production foliaire.",
            recommendation="Irrigation régulière.",
        ),
        StageRule(
            stage="pre_recolte",
            thresholds=(Threshold("NDMI", 0.20, 0.40),),
            explanation="L'état hydrique conditionne la teneur en huile essentielle.",
            recommendation="Éviter tout déficit hydrique.",
        ),
    ),

    # ==================================================================
    # BASILIC
    # ==================================================================
    "basilic": (
        StageRule(
            stage="croissance",
            thresholds=(Threshold("NDVI", 0.45, 0.75),),
            explanation="Croissance rapide et forte biomasse.",
            recommendation="Fertilisation azotée modérée.",
        ),
        StageRule(
            stage="pre_recolte",
            thresholds=(Threshold("NDMI", 0.25, 0.45),),
            explanation="Une bonne humidité améliore la qualité des feuilles.",
            recommendation="Irrigation avant récolte.",
        ),
    ),

    # ==================================================================
    # THYM
    # ==================================================================
    "thym": (
        StageRule(
            stage="croissance",
            thresholds=(
                Threshold("NDVI", 0.40, 0.60),
                Threshold("SAVI", 0.35, 0.55),
                Threshold("FCOVER", 45, 70, unit="%"),
            ),
            explanation="Le SAVI corrige l'effet du sol sur les cultures peu couvrantes.",
            recommendation="Contrôler le couvert végétal.",
        ),
        StageRule(
            stage="prefloraison",
            thresholds=(
                Threshold("NDRE", 0.20, 0.35),
                Threshold("FCOVER", 60, 80, unit="%"),
            ),
            explanation="État nutritionnel avant la production florale.",
            recommendation="Ajuster la fertilisation.",
        ),
    ),

}


def get_rule(species: str, stage: str, age_years: float | None = None) -> StageRule:
    """
    Règle applicable à une culture, pour un stade et un âge donnés.

    Si plusieurs règles existent pour le stade (cas des seuils par âge),
    on retient celle qui correspond à l'âge. Âge inconnu : on prend la
    règle la plus large, en signalant que la date de plantation manque.
    """
    rules = RULES.get(species.lower())
    if not rules:
        return DEFAULT_RULE

    for_stage = [r for r in rules if r.stage == stage]
    if not for_stage:
        return DEFAULT_RULE

    # Une seule règle : pas de distinction d'âge
    if len(for_stage) == 1:
        return for_stage[0]

    matching = [r for r in for_stage if r.matches_age(age_years)]
    if matching:
        return matching[0]

    # Âge inconnu : la tranche la plus large (seuils les plus bas)
    return min(for_stage, key=lambda r: r.thresholds[0].low)


def get_thresholds(species: str, stage: str, age_years: float | None = None):
    """Seuils applicables, dans l'ordre d'affichage."""
    return get_rule(species, stage, age_years).thresholds


def watched_indices(species: str, stage: str, age_years: float | None = None) -> tuple[str, ...]:
    """Noms des indices suivis à ce stade."""
    return tuple(t.indice for t in get_thresholds(species, stage, age_years))


def has_age_specific_rules(species: str, stage: str) -> bool:
    """Vrai si ce stade a des seuils différents selon l'âge de la plante."""
    rules = RULES.get(species.lower()) or ()
    return len([r for r in rules if r.stage == stage]) > 1