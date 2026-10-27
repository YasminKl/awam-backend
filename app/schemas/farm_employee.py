from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator

from app.schemas.employee import EmployeeResponse

ALLOWED_ROLES = {"coordinateur", "membre"}


class FarmEmployeeCreate(BaseModel):
    employee_id: int
    role: Optional[str] = "membre"

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_ROLES:
            raise ValueError(f"Rôle invalide. Valeurs autorisées : {', '.join(ALLOWED_ROLES)}.")
        return v


class FarmEmployeeUpdate(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    #: Code du type de tâche de l'équipe ; null = sans équipe
    team: Optional[str] = None

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_ROLES:
            raise ValueError(f"Rôle invalide. Valeurs autorisées : {', '.join(ALLOWED_ROLES)}.")
        return v


class FarmEmployeeResponse(BaseModel):
    id: int
    farm_id: int
    role: Optional[str] = None
    hired_at: Optional[datetime] = None
    is_active: bool
    #: Équipe dans cette ferme (code d'un type de tâche), None si sans équipe
    team: Optional[str] = None
    employee: EmployeeResponse

    class Config:
        from_attributes = True


class TeamAssignment(BaseModel):
    """Place plusieurs employés d'une ferme dans une équipe, ou les en retire."""

    #: Code du type de tâche de l'équipe ; null = retirer de leur équipe
    team: Optional[str] = None
    #: Rattachements employé-ferme concernés (au moins un)
    link_ids: List[int]

    @field_validator("link_ids")
    @classmethod
    def at_least_one(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("Choisissez au moins un employé.")
        return list(dict.fromkeys(v))