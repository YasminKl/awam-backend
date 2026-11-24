import re
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
        # Chiffres, espaces et signes + ( ) - . seulement, ex. « (+216) 20 123 456 »
        if not re.fullmatch(r"[0-9+()\-.\s]*", v):
            raise ValueError("Le téléphone ne peut contenir que des chiffres, des espaces et les signes + ( ) - .")
        return v or None


class EmployeeUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    #: Faux : rendre l'employé inactif ; vrai : le réactiver
    is_active: Optional[bool] = None

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
        # Chiffres, espaces et signes + ( ) - . seulement, ex. « (+216) 20 123 456 »
        if not re.fullmatch(r"[0-9+()\-.\s]*", v):
            raise ValueError("Le téléphone ne peut contenir que des chiffres, des espaces et les signes + ( ) - .")
        return v or None


class EmployeeResponse(BaseModel):
    id: int
    full_name: str
    phone: Optional[str] = None
    is_active: bool = True

    class Config:
        from_attributes = True