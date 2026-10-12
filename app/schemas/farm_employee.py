from datetime import datetime
from typing import Optional
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
    employee: EmployeeResponse

    class Config:
        from_attributes = True