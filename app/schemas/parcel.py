from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, field_validator

# "none" : aucun traitement lancé (parcelle inactive, pas d'analyse satellite)
RasterStatus = Literal["pending", "ready", "failed", "none"]


class ParcelCreate(BaseModel):
    farm_id: int
    name: str
    geometry: dict[str, Any]  # objet GeoJSON brut (Polygon)
    culture_type: Optional[str] = None
    soil_type: Optional[str] = None
    irrigation_type: Optional[str] = None
    # Une parcelle inactive ne reçoit aucune analyse satellite
    status: Literal["active", "inactive"] = "active"

    @field_validator("culture_type")
    @classmethod
    def culture_length(cls, v: Optional[str]) -> Optional[str]:
        # Même limite que la colonne de la base (100 caractères)
        if v is not None and len(v) > 100:
            raise ValueError("La culture ne doit pas dépasser 100 caractères.")
        return v

    @field_validator("soil_type")
    @classmethod
    def soil_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 100:
            raise ValueError("Le type de sol ne doit pas dépasser 100 caractères.")
        return v

    @field_validator("irrigation_type")
    @classmethod
    def irrigation_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 100:
            raise ValueError("Le type d'irrigation ne doit pas dépasser 100 caractères.")
        return v


class ParcelUpdate(BaseModel):
    name: Optional[str] = None
    geometry: Optional[dict[str, Any]] = None
    culture_type: Optional[str] = None
    soil_type: Optional[str] = None
    irrigation_type: Optional[str] = None
    status: Optional[Literal["active", "inactive"]] = None

    @field_validator("culture_type")
    @classmethod
    def culture_length(cls, v: Optional[str]) -> Optional[str]:
        # Même limite que la colonne de la base (100 caractères)
        if v is not None and len(v) > 100:
            raise ValueError("La culture ne doit pas dépasser 100 caractères.")
        return v

    @field_validator("soil_type")
    @classmethod
    def soil_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 100:
            raise ValueError("Le type de sol ne doit pas dépasser 100 caractères.")
        return v

    @field_validator("irrigation_type")
    @classmethod
    def irrigation_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 100:
            raise ValueError("Le type d'irrigation ne doit pas dépasser 100 caractères.")
        return v


class ParcelResponse(BaseModel):
    id: int
    farm_id: int
    name: str
    culture_type: Optional[str] = None
    soil_type: Optional[str] = None
    irrigation_type: Optional[str] = None
    area_ha: Optional[float] = None
    status: str  # état agronomique (active / inactive...), indépendant des rasters
    geometry: Optional[dict[str, Any]] = None  # forme du polygone renvoyée au frontend

    cog_url: Optional[str] = None
    sentinel_rgb_url: Optional[str] = None
    sentinel_ndvi_url: Optional[str] = None

    raster_status: RasterStatus
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}