import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import get_current_user
from app.models.utilisateur import User
from app.schemas.weather import WeatherResponse

router = APIRouter(tags=["weather"])

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@router.get("/weather", response_model=WeatherResponse)
async def get_weather(
    latitude: float,
    longitude: float,
    user: User = Depends(get_current_user),
):
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code",
        "timezone": "auto",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(OPEN_METEO_URL, params=params, timeout=10.0)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Erreur lors de la récupération des données météo : {e}",
        )

    data = response.json()
    current = data.get("current", {})

    return {
        "temperature": current.get("temperature_2m"),
        "humidity": current.get("relative_humidity_2m"),
        "precipitation": current.get("precipitation"),
        "wind_speed": current.get("wind_speed_10m"),
        "weather_code": current.get("weather_code"),
    }