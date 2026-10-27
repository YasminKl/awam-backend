from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel


class AlertHistoryItem(BaseModel):
    """Niveau du diagnostic pour une image de l'épisode."""

    scene_date: date
    severity: str
    label: str


class AlertResponse(BaseModel):
    id: int
    parcel_id: int
    alert_type: str
    #: Niveau interne : "alert" | "strong_alert"
    severity: str
    #: Libellé affiché : « Anomalie détectée », « Anomalie persistante »
    severity_label: str = ""
    message: str
    resolved: bool
    created_at: datetime

    crop_id: Optional[int] = None
    crop_label: Optional[str] = None
    parcel_name: Optional[str] = None
    farm_name: Optional[str] = None

    cause: Optional[str] = None
    evidence: Optional[str] = None
    action: Optional[str] = None
    valid_ratio: Optional[float] = None
    first_scene_date: Optional[date] = None
    last_scene_date: Optional[date] = None

    resolution: Optional[str] = None
    resolution_label: Optional[str] = None
    resolved_at: Optional[datetime] = None
    #: Dernière création, mise à jour ou fermeture
    updated_at: Optional[datetime] = None

    #: Niveau de chaque image de l'épisode, de la plus ancienne à la plus récente
    history: list[AlertHistoryItem] = []

    class Config:
        from_attributes = True


class AlertCount(BaseModel):
    open: int


class AlertResolve(BaseModel):
    #: "treated" : problème vérifié et traité ; "false_alarm" : fausse alerte
    resolution: Literal["treated", "false_alarm"] = "treated"