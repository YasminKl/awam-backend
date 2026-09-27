from datetime import date, datetime
from typing import Literal, Optional
from pydantic import BaseModel

# Source unique des types de raster valides
RasterType = Literal["mask", "sentinel_rgb", "sentinel_ndvi"]


class RasterResponse(BaseModel):
    id: int
    parcel_id: int
    raster_type: RasterType
    scene_date: Optional[date] = None
    url: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RasterCurrentResponse(BaseModel):
    url: Optional[str] = None
    scene_date: Optional[date] = None
    bbox: Optional[list[float]] = None