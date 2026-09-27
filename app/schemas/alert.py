from datetime import datetime
from pydantic import BaseModel


class AlertResponse(BaseModel):
    id: int
    parcel_id: int
    alert_type: str
    severity: str
    message: str
    resolved: bool
    created_at: datetime

    class Config:
        from_attributes = True