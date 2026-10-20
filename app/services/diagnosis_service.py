"""
Moteur de diagnostic des cultures.

Transforme des valeurs d'indices en interprétation actionnable :

    mesure satellite → contexte (stade, âge) → diagnostic → action

Deux lectures complémentaires, car aucune ne suffit seule :

  1. Les SEUILS du référentiel agronomique AWAM, déclinés par stade et
     par âge. Ils situent la parcelle par rapport à une norme.
  2. La VARIATION depuis la mesure précédente. Elle ne dépend d'aucun
     seuil et reste fiable même quand la norme est mal connue — cas de
     la plupart des plantes aromatiques, pour lesquelles aucune valeur
     de référence n'est publiée.

Règles reprises du document agronomique :
  - le stade vient du calendrier, pas de la courbe (les pérennes n'ont
    pas de signal exploitable) ;
  - les seuils viennent du document des indices, chacun à SA période et
    pour SON âge (thresholds.get_rules_at), indépendamment du stade ;
  - une baisse pendant une coupe ou une récolte n'est jamais une alerte ;
  - ce n'est pas un indice isolé qui compte, mais leur combinaison ;
  - une anomalie n'est confirmée qu'à la seconde observation.

Ce module ne fait aucun I/O : il reçoit des valeurs, il renvoie un
diagnostic. Les lectures en base sont faites par les routes et les tâches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.services.agronomy.calendar import get_stages_at, is_cultural_period
from app.services.agronomy.species import get_species, is_deciduous, stage_label
from app.services.agronomy.thresholds import NOT_EVALUATED, Threshold, get_rules_at

#: Baisse relative à partir de laquelle une variation est significative
SIGNIFICANT_DROP = 0.15   # 15 %
WATCH_DROP = 0.10         # 10 %

#: Part de parcelle observée en dessous de laquelle on n'alerte pas :
#: une moyenne calculée sur un coin de parcelle n'est pas représentative.
MIN_VALID_RATIO_FOR_ALERT = 0.40


@dataclass
class Indicator:
    """Un indice évalué dans son contexte."""

    name: str
    value: float
    #: Écart avec la mesure précédente (None si première observation)
    delta: float | None = None
    #: "low" (sous le seuil) | "normal" | "high" (excellent)
    #: | "info" (affiché sans verdict, voir NOT_EVALUATED)
    level: str = "normal"
    #: Rappel du seuil, ex. "normal 0,45–0,70"
    threshold_label: str = ""
    unit: str = ""

    @property
    def drop_ratio(self) -> float | None:
        """Baisse relative par rapport à la mesure précédente."""
        if self.delta is None or self.delta >= 0:
            return None
        previous = self.value - self.delta
        if previous <= 0:
            return None
        return abs(self.delta) / previous


@dataclass
class Risk:
    severity: str        # watch | alert | strong_alert
    diagnosis: str
    measure: str
    action: str


@dataclass
class Diagnosis:
    stage: str | None
    stage_label: str
    stage_period: str
    #: Âge de la plante en années, None si date de plantation inconnue
    age_years: float | None = None
    indicators: list[Indicator] = field(default_factory=list)
    risks: list[Risk] = field(default_factory=list)
    #: Pourquoi ce stade est important, d'après l'agronome
    explanation: str = ""
    #: Ce qu'il faut faire à ce stade, d'après l'agronome
    recommendation: str = ""
    #: Lecture croisée des indices, en une phrase
    reading: str = ""
    #: Vrai si une coupe ou récolte explique les baisses observées
    cultural_drop: bool = False
    #: Vrai si l'âge manque alors que les seuils en dépendent
    age_required: bool = False
    severity: str = "normal"


# ======================================================================
# 1. Âge de la plante
# ======================================================================


def compute_age_years(planting_date: date | None, at: date | None = None) -> float | None:
    """
    Âge de la plante en années, depuis sa date de plantation.

    Déterminant pour les pérennes : un olivier de 2 ans et un de 15 ans
    n'ont pas les mêmes valeurs normales de NDVI.
    """
    if planting_date is None:
        return None
    reference = at or date.today()
    return max((reference - planting_date).days / 365.25, 0.0)


# ======================================================================
# 2. Évaluation des indices
# ======================================================================


def evaluate_indicators(
    thresholds: tuple[Threshold, ...],
    values: dict[str, float],
    previous: dict[str, float] | None = None,
) -> list[Indicator]:
    """
    Compare chaque indice suivi à cette date avec son seuil, et calcule sa
    variation depuis la mesure précédente.

    `thresholds` vient de thresholds.get_rules_at : les seuils de toutes
    les règles du document qui s'appliquent à cette date et à cet âge.

    Seuls les indices prévus à cette période sont retournés : en afficher
    quinze noierait l'information utile.
    """
    previous = previous or {}
    indicators: list[Indicator] = []

    for threshold in thresholds:
        value = values.get(threshold.indice)
        if value is None:
            continue

        before = previous.get(threshold.indice)

        # Indice affiché pour information, sans comparaison au seuil.
        # Le FCOVER est stocké entre 0 et 1 : affiché en %, comme son unité.
        if threshold.indice in NOT_EVALUATED:
            scale = 100.0 if threshold.unit == "%" else 1.0
            indicators.append(
                Indicator(
                    name=threshold.indice,
                    value=value * scale,
                    delta=(value - before) * scale if before is not None else None,
                    level="info",
                    threshold_label=NOT_EVALUATED[threshold.indice],
                    unit=threshold.unit,
                )
            )
            continue

        indicators.append(
            Indicator(
                name=threshold.indice,
                value=value,
                delta=(value - before) if before is not None else None,
                level=threshold.evaluate(value),
                threshold_label=threshold.label,
                unit=threshold.unit,
            )
        )

    return indicators

    

# ======================================================================
# 3. Lecture croisée
# ======================================================================


def cross_read(indicators: list[Indicator]) -> tuple[str, list[Risk]]:
    """
    Interprète la COMBINAISON des indices, pas chacun isolément.

    C'est ici que se joue la différence entre un stress hydrique et un
    problème nutritionnel : les règles viennent du document agronomique.
    """
    by_name = {i.name: i for i in indicators}

    def falling(name: str, threshold: float = WATCH_DROP) -> bool:
        ind = by_name.get(name)
        ratio = ind.drop_ratio if ind else None
        return ratio is not None and ratio >= threshold

    def is_low(name: str) -> bool:
        ind = by_name.get(name)
        return ind is not None and ind.level == "low"

    risks: list[Risk] = []
    reading = ""

    # --- Signal hydrique : NDMI bas ou en baisse --------------------
    # Le MSI n'est PAS une confirmation indépendante : il se déduit du
    # NDMI, MSI = (1 - NDMI) / (1 + NDMI), et monte mécaniquement dès que
    # le NDMI baisse. Il n'y a donc qu'un seul signal, qui vaut « à
    # surveiller » ; c'est sa répétition sur deux images (règle de
    # confirmation, voir compute_severity) qui le fait passer en alerte.
    water_signal = is_low("NDMI") or falling("NDMI", SIGNIFICANT_DROP)

    if water_signal and not falling("NDVI"):
        # On n'affirme que le couvert est stable que si le NDVI est suivi à ce stade
        reading = (
            "Le NDMI est bas ou en baisse alors que le couvert reste stable : stress hydrique précoce."
            if "NDVI" in by_name
            else "Le NDMI est bas ou en baisse : stress hydrique précoce."
        )
        risks.append(Risk(
            severity="watch",
            diagnosis="Stress hydrique précoce",
            measure="Contrôler l'humidité du sol avant perte de couvert",
            action="Ajuster l'irrigation progressivement, sans excès",
        ))
    elif water_signal:
        reading = "Le NDMI est bas ou en baisse et le couvert recule : stress hydrique possible."
        risks.append(Risk(
            severity="watch",
            diagnosis="Stress hydrique possible",
            measure="Mesurer l'humidité du sol, le débit et la pression d'irrigation",
            action="Restaurer une irrigation régulière si le déficit est confirmé",
        ))

    # --- Problème nutritionnel : NDRE baisse, NDMI stable -----------
    if (is_low("NDRE") or falling("NDRE", SIGNIFICANT_DROP)) and not falling("NDMI"):
        reading = reading or "Le NDRE baisse sans déficit hydrique : origine nutritionnelle ou sanitaire."
        risks.append(Risk(
            severity="watch",
            diagnosis="Baisse de chlorophylle sans déficit hydrique",
            measure="SPAD et analyse foliaire",
            action="Vérifier azote, fer et état racinaire avant toute fertilisation",
        ))

    # --- Perte de vigueur : NDVI et NDRE baissent ensemble ----------
    if falling("NDVI", SIGNIFICANT_DROP) and falling("NDRE", WATCH_DROP):
        reading = "Le NDVI et le NDRE baissent ensemble : perte de vigueur généralisée."
        risks.append(Risk(
            severity="alert",
            diagnosis="Perte de vigueur ou stress physiologique",
            measure="Mesurer couverture foliaire, SPAD et humidité du sol",
            action="Rechercher maladie, carence, ravageurs ou effet d'une taille",
        ))

    # --- Couvert qui recule, hydratation normale --------------------
    # « Hydratation normale » : NDMI ni bas ni en baisse.
    elif falling("NDVI", SIGNIFICANT_DROP) and not water_signal:
        reading = reading or "Le couvert recule alors que l'hydratation reste normale : cause non hydrique."
        risks.append(Risk(
            severity="watch",
            diagnosis="Baisse de couvert sans déficit hydrique",
            measure="Vérifier taille, fauche, récolte ou état sanitaire",
            action="Écarter d'abord une cause culturale avant diagnostic sanitaire",
        ))

    # --- Développement insuffisant : valeur basse, sans variation ----
    if not risks and any(i.level == "low" for i in indicators):
        low_names = ", ".join(i.name for i in indicators if i.level == "low")
        reading = f"{low_names} sous l'intervalle attendu pour ce stade."
        risks.append(Risk(
            severity="watch",
            diagnosis="Développement en dessous de l'intervalle attendu",
            measure="Comparer avec les parcelles voisines de même âge et vérifier l'installation",
            action="Confirmer sur le terrain avant toute intervention",
        ))

    if not reading:
        if any(i.level == "high" for i in indicators):
            reading = "Indices au-dessus de l'intervalle attendu : développement excellent."
        else:
            reading = "Indices dans l'intervalle attendu, aucune anomalie détectée."

    return reading, risks


# ======================================================================
# 4. Gravité
# ======================================================================


def compute_severity(
    risks: list[Risk],
    indicators: list[Indicator],
    previous_severity: str | None = None,
) -> str:
    """
    Niveau global. Une anomalie confirmée sur une seconde observation
    monte d'un cran : c'est la règle des deux dates du document.
    """
    if not risks and all(i.level != "low" for i in indicators):
        return "normal"

    base = "normal"
    if any(r.severity == "alert" for r in risks):
        base = "alert"
    elif risks or any(i.level == "low" for i in indicators):
        base = "watch"

    # Confirmation : la même anomalie revient une seconde fois
    if previous_severity in ("alert", "strong_alert") and base == "alert":
        return "strong_alert"
    if previous_severity in ("watch", "alert") and base == "watch":
        return "alert"
    return base


# ======================================================================
# 5. Point d'entrée
# ======================================================================


def diagnose(
    species: str,
    scene_date: date,
    values: dict[str, float],
    previous_values: dict[str, float] | None = None,
    previous_severity: str | None = None,
    recent_harvest: bool = False,
    planting_date: date | None = None,
    valid_ratio: float | None = None,
) -> Diagnosis:
    """
    Diagnostic complet d'une culture pour une image satellite.

    Args:
        species:           code du référentiel (olivier, lavande...)
        scene_date:        date de l'image analysée
        values:            valeurs des indices pour cette image
        previous_values:   valeurs de l'image précédente, pour les variations
        previous_severity: gravité du diagnostic précédent, pour la confirmation
        recent_harvest:    une récolte a été enregistrée récemment
        planting_date:     pour calculer l'âge et choisir les bons seuils
        valid_ratio:       part de parcelle réellement observée (0 à 1)

    Returns:
        Diagnosis prêt à afficher ou à archiver.
    """
    state = get_stages_at(species, scene_date, planting_date)
    period = state.principal if state else None
    stage = period.stage if period else None
    spec = get_species(species)
    age = compute_age_years(planting_date, scene_date)

    result = Diagnosis(
        stage=stage,
        stage_label=stage_label(stage) if stage else "Stade inconnu",
        stage_period=period.label if period else "",
        age_years=round(age, 1) if age is not None else None,
    )

    # Image antérieure à la plantation : elle ne concerne pas cette culture
    if planting_date is not None and scene_date < planting_date:
        result.stage_label = "Pas encore plantée"
        result.reading = "Image antérieure à la date de plantation : pas de diagnostic."
        return result

    if not spec or not stage:
        result.reading = "Espèce non référencée : aucune interprétation possible."
        return result

    # Seuils : les règles du document qui couvrent cette date et cet âge
    selection = get_rules_at(species, scene_date, age)
    result.explanation = selection.explanation
    result.recommendation = selection.recommendation
    result.indicators = evaluate_indicators(
        selection.thresholds, values, previous_values
    )

    # Seuils dépendants de l'âge mais date de plantation absente : la
    # tranche aux seuils les plus bas est appliquée, et on le signale.
    result.age_required = selection.age_required

    # Une baisse pendant une coupe ou une récolte n'est pas une anomalie
    result.cultural_drop = recent_harvest or is_cultural_period(species, scene_date, planting_date)
    if result.cultural_drop:
        result.reading = (
            "Période de coupe ou de récolte : les baisses de couvert sont "
            "attendues et ne déclenchent pas d'alerte."
        )
        result.severity = "normal"
        return result

    # Espèce caduque en repos : un indice bas est normal, pas un stress
    if is_deciduous(species) and stage == "dormance":
        result.reading = (
            "Espèce à feuilles caduques en repos : des indices bas sont "
            "normaux à cette période."
        )
        result.severity = "normal"
        return result

    result.reading, result.risks = cross_read(result.indicators)
    result.severity = compute_severity(result.risks, result.indicators, previous_severity)

    # Observation partielle : on n'alerte pas sur un coin de parcelle
    if valid_ratio is not None and valid_ratio < MIN_VALID_RATIO_FOR_ALERT:
        result.reading += (
            f" Attention : seuls {valid_ratio:.0%} de la parcelle étaient "
            "observables sur cette image, le diagnostic reste à confirmer."
        )
        if result.severity in ("alert", "strong_alert"):
            result.severity = "watch"

    return result