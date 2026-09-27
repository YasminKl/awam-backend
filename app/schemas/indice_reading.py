from datetime import datetime
from pydantic import BaseModel


class IndiceReadingResponse(BaseModel):
    id: int
    parcel_id: int
    indice_name: str
    value: float
    recorded_at: datetime

    class Config:
        from_attributes = True