from typing import Optional
from pydantic import BaseModel


class WeatherResponse(BaseModel):
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    precipitation: Optional[float] = None
    wind_speed: Optional[float] = None
    weather_code: Optional[int] = None