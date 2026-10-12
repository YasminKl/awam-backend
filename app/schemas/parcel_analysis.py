from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


def _check_ph(v: Optional[float], label: str) -> Optional[float]:
    if v is None:
        return v
    if not 0 <= v <= 14:
        raise ValueError(f"Le pH {label} doit être compris entre 0 et 14.")
    return v


def _check_pct(v: Optional[float], label: str) -> Optional[float]:
    if v is None:
        return v
    if not 0 <= v <= 100:
        raise ValueError(f"{label} doit être compris entre 0 et 100 %.")
    return v


def _check_positive(v: Optional[float], label: str) -> Optional[float]:
    if v is None:
        return v
    if v < 0:
        raise ValueError(f"{label} ne peut pas être négatif.")
    return v


class ParcelAnalysisBase(BaseModel):
    analyzed_at: Optional[datetime] = None

    soil_type: Optional[str] = None
    soil_ph: Optional[float] = None
    soil_humidity_pct: Optional[float] = None
    soil_organic_matter_pct: Optional[float] = None
    soil_salinity_g_l: Optional[float] = None

    water_ph: Optional[float] = None
    water_nitrates_mg_l: Optional[float] = None

    notes: Optional[str] = None

    @field_validator("soil_ph")
    @classmethod
    def soil_ph_valid(cls, v: Optional[float]) -> Optional[float]:
        return _check_ph(v, "du sol")

    @field_validator("water_ph")
    @classmethod
    def water_ph_valid(cls, v: Optional[float]) -> Optional[float]:
        return _check_ph(v, "de l'eau")

    @field_validator("soil_humidity_pct")
    @classmethod
    def humidity_valid(cls, v: Optional[float]) -> Optional[float]:
        return _check_pct(v, "L'humidité du sol")

    @field_validator("soil_organic_matter_pct")
    @classmethod
    def organic_valid(cls, v: Optional[float]) -> Optional[float]:
        return _check_pct(v, "La matière organique")

    @field_validator("soil_salinity_g_l")
    @classmethod
    def salinity_valid(cls, v: Optional[float]) -> Optional[float]:
        return _check_positive(v, "La salinité")

    @field_validator("water_nitrates_mg_l")
    @classmethod
    def nitrates_valid(cls, v: Optional[float]) -> Optional[float]:
        return _check_positive(v, "Les nitrates")

    @field_validator("soil_type")
    @classmethod
    def soil_type_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if len(v) > 100:
            raise ValueError("Le type de sol ne doit pas dépasser 100 caractères.")
        return v or None

    @field_validator("notes")
    @classmethod
    def notes_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if len(v) > 1000:
            raise ValueError("Les notes ne doivent pas dépasser 1000 caractères.")
        return v or None


class ParcelAnalysisCreate(ParcelAnalysisBase):
    """Analyse saisie à la création d'une parcelle ou ajoutée ensuite."""


class ParcelAnalysisUpdate(ParcelAnalysisBase):
    """Tous les champs restent facultatifs : seuls ceux envoyés sont modifiés."""


class ParcelAnalysisResponse(ParcelAnalysisBase):
    id: int
    parcel_id: int
    analyzed_at: datetime
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}