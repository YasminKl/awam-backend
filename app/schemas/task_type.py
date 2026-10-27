from typing import Optional

from pydantic import BaseModel, field_validator


def _clean_name(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("Indiquez le nom du type de tâche.")
    if len(v) > 60:
        raise ValueError("Le nom ne doit pas dépasser 60 caractères.")
    return v


class TaskTypeCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        return _clean_name(v)


class TaskTypeUpdate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        return _clean_name(v)


class TaskTypeResponse(BaseModel):
    """Un type de tâche, prédéfini ou libre, tel qu'affiché."""

    #: Identifiant du type libre ; None pour un type prédéfini
    id: Optional[int] = None
    #: Valeur enregistrée dans farm_employee.team (et plus tard activity)
    code: str
    label: str
    predefined: bool