from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

#: Niveaux du référentiel agronomique :
#:   low    — sous l'intervalle attendu (anomalie à vérifier)
#:   normal — dans l'intervalle attendu
#:   high   — au-dessus : développement excellent (pas une alerte)
#:   info   — affiché sans verdict (seuil en attente de clarification)
Level = Literal["low", "normal", "high", "info"]

#: Niveaux des références de sol (inchangés)
SoilLevel = Literal["green", "yellow", "red"]
Severity = Literal["normal", "watch", "alert", "strong_alert"]


class IndicatorResponse(BaseModel):
    """Un indice évalué, prêt à afficher : le front ne recalcule rien."""

    name: str
    value: float
    delta: Optional[float] = None
    level: Level
    threshold_label: str = ""
    #: "" pour les indices, "%" pour le FCover
    unit: str = ""


class RiskResponse(BaseModel):
    severity: Severity
    diagnosis: str
    measure: str
    action: str


class StageInfo(BaseModel):
    stage: Optional[str] = None
    label: str
    period: str = ""
    #: Jours écoulés depuis la plantation (None si date inconnue)
    days_since_planting: Optional[int] = None
    #: Âge de la plante en années : choisit le jeu de seuils de l'agronome
    age_years: Optional[float] = None


class DiagnosisResponse(BaseModel):
    crop_id: int
    scene_date: Optional[date] = None
    stage: StageInfo
    indicators: list[IndicatorResponse] = []
    risks: list[RiskResponse] = []
    reading: str = ""
    #: Pourquoi ce stade compte, d'après le référentiel agronomique
    explanation: str = ""
    #: Ce qu'il faut faire à ce stade, d'après le référentiel agronomique
    recommendation: str = ""
    cultural_drop: bool = False
    #: Vrai si les seuils dépendent de l'âge mais que la date de
    #: plantation n'est pas renseignée
    age_required: bool = False
    severity: Severity = "normal"


class WaterNeedResponse(BaseModel):
    days: int
    et0_mm: float
    kc: Optional[float] = None
    etc_mm: float
    rain_mm: float
    effective_rain_mm: float
    net_need_mm: float
    net_need_m3: Optional[float] = None
    message: str


class SoilRowResponse(BaseModel):
    param: str
    value: float
    level: SoilLevel
    reference: str
    message: str


class NeedsResponse(BaseModel):
    """Onglet Besoins : irrigation + fertilisation."""

    crop_id: int
    water: Optional[WaterNeedResponse] = None
    soil: list[SoilRowResponse] = []
    #: Date de l'analyse de sol utilisée
    soil_analysis_date: Optional[date] = None
    #: Culture clôturée (terminée, abandonnée, annuelle récoltée) : aucun
    #: besoin n'est calculé, et voici pourquoi
    closed_reason: Optional[str] = None


class StageTimelineItem(BaseModel):
    stage: str
    label: str
    period: str
    is_current: bool = False
    cultural_drop: bool = False


class ParcelInterpretation(BaseModel):
    """
    Interprétation, déjà enregistrée, de l'analyse d'une date pour une
    culture : indices, verdict de chaque indice, et lecture du diagnostic.
    """

    crop_id: int
    crop_label: str
    scene_date: date
    stage_label: Optional[str] = None
    #: normal | watch | alert | strong_alert
    severity: str
    severity_label: str
    #: Lecture du diagnostic, ex. « Stress hydrique précoce »
    reading: Optional[str] = None
    #: Vrai en période de coupe ou de récolte : la baisse est attendue
    cultural_drop: bool = False
    indicators: list[IndicatorResponse] = []