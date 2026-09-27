from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel

RasterStatus = Literal["pending", "ready", "failed"]


class ParcelCreate(BaseModel):
    farm_id: int
    name: str
    geometry: dict[str, Any]  # objet GeoJSON brut (Polygon)
    culture_type: Optional[str] = None
    soil_type: Optional[str] = None
    irrigation_type: Optional[str] = None


class ParcelUpdate(BaseModel):
    name: Optional[str] = None
    geometry: Optional[dict[str, Any]] = None
    culture_type: Optional[str] = None
    soil_type: Optional[str] = None
    irrigation_type: Optional[str] = None


class ParcelResponse(BaseModel):
    id: int
    farm_id: int
    name: str
    culture_type: Optional[str] = None
    soil_type: Optional[str] = None
    irrigation_type: Optional[str] = None
    area_ha: Optional[float] = None
    status: str  # état agronomique (active / inactive...)
    geometry: Optional[dict[str, Any]] = None

    cog_url: Optional[str] = None
    sentinel_rgb_url: Optional[str] = None
    sentinel_ndvi_url: Optional[str] = None

    raster_status: RasterStatus
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}