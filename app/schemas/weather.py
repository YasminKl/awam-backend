from typing import Optional
from pydantic import BaseModel


class WeatherResponse(BaseModel):
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    precipitation: Optional[float] = None
    wind_speed: Optional[float] = None
    weather_code: Optional[int] = None
    #: Vrai si Open-Meteo est indisponible et que c'est la dernière météo
    #: connue qui est renvoyée
    stale: bool = False
    #: Âge de cette météo, en minutes
    age_minutes: int = 0