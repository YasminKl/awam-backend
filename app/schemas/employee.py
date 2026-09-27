from typing import Optional
from pydantic import BaseModel, field_validator


class EmployeeCreate(BaseModel):
    full_name: str
    phone: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def full_name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Le nom complet est requis.")
        if len(v) > 150:
            raise ValueError("Le nom complet ne doit pas dépasser 150 caractères.")
        return v

    @field_validator("phone")
    @classmethod
    def phone_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if len(v) > 30:
            raise ValueError("Le numéro de téléphone ne doit pas dépasser 30 caractères.")
        return v or None


class EmployeeUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def full_name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Le nom complet est requis.")
        if len(v) > 150:
            raise ValueError("Le nom complet ne doit pas dépasser 150 caractères.")
        return v

    @field_validator("phone")
    @classmethod
    def phone_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if len(v) > 30:
            raise ValueError("Le numéro de téléphone ne doit pas dépasser 30 caractères.")
        return v or None


class EmployeeResponse(BaseModel):
    id: int
    full_name: str
    phone: Optional[str] = None

    class Config:
        from_attributes = True