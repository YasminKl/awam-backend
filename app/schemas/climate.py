"""Schémas de la projection climatique (lieux étudiés et résumé)."""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ClimateSiteCreate(BaseModel):
    """Un nouveau lieu à étudier, choisi sur la carte."""

    name: str = Field(..., min_length=1, max_length=150)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class ClimateSiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    farm_id: Optional[int] = None
    latitude: float
    longitude: float
    status: str
    last_error: Optional[str] = None
    sea_distance_km: Optional[float] = None