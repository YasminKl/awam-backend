from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, field_validator

from app.schemas.employee import EmployeeResponse

#: a_realiser : planifiée ; realisee : faite ; a_entretenir : faite, à suivre
ActivityStatus = Literal["a_realiser", "realisee", "a_entretenir"]


def _clean_type(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("Le type de tâche est requis.")
    if len(v) > 100:
        raise ValueError("Le type de tâche ne doit pas dépasser 100 caractères.")
    return v


def _with_timezone(v: Optional[datetime]) -> Optional[datetime]:
    if v is not None and v.tzinfo is None:
        raise ValueError("La date doit inclure une information de fuseau horaire.")
    return v


class ActivityCreate(BaseModel):
    parcel_id: int
    activity_type: str
    #: Par défaut « realisee » : une activité enregistrée sans état est faite
    status: ActivityStatus = "realisee"
    #: Date prévue, obligatoire pour une tâche à réaliser
    planned_for: Optional[date] = None
    #: Date réelle d'une tâche faite ; maintenant si absente
    performed_at: Optional[datetime] = None
    crop_id: Optional[int] = None
    team: Optional[str] = None
    #: Tâche principale, pour une tâche attachée
    parent_id: Optional[int] = None
    employee_ids: List[int] = []

    @field_validator("activity_type")
    @classmethod
    def type_valid(cls, v: str) -> str:
        return _clean_type(v)

    @field_validator("performed_at")
    @classmethod
    def performed_at_has_timezone(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _with_timezone(v)


class ActivityUpdate(BaseModel):
    parcel_id: Optional[int] = None
    activity_type: Optional[str] = None
    status: Optional[ActivityStatus] = None
    planned_for: Optional[date] = None
    performed_at: Optional[datetime] = None
    crop_id: Optional[int] = None
    team: Optional[str] = None
    employee_ids: Optional[List[int]] = None

    @field_validator("activity_type")
    @classmethod
    def type_valid(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _clean_type(v)

    @field_validator("performed_at")
    @classmethod
    def performed_at_has_timezone(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _with_timezone(v)


class ActivityComplete(BaseModel):
    """Marque une tâche planifiée comme faite."""

    #: Date réelle ; maintenant si absente
    performed_at: Optional[datetime] = None
    #: Employés qui l'ont VRAIMENT faite
    employee_ids: List[int]
    #: Vrai : faite, mais à suivre (« à entretenir »)
    follow_up: bool = False

    @field_validator("employee_ids")
    @classmethod
    def at_least_one(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("Indiquez au moins un employé qui a fait la tâche.")
        return v

    @field_validator("performed_at")
    @classmethod
    def performed_at_has_timezone(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _with_timezone(v)


class ActivityResponse(BaseModel):
    id: int
    parcel_id: int
    activity_type: str
    status: str = "realisee"
    planned_for: Optional[date] = None
    performed_at: Optional[datetime] = None
    crop_id: Optional[int] = None
    team: Optional[str] = None
    parent_id: Optional[int] = None
    employees: List[EmployeeResponse] = []

    class Config:
        from_attributes = True