"""
Météo du jour d'une ferme (barre du haut), à partir d'Open-Meteo.

  - Chaque réponse est gardée 30 minutes dans Redis, par point (arrondi à
    0,01°, environ 1 km) : AWAM interroge Open-Meteo beaucoup moins souvent,
    ce qui éloigne aussi de ses limites gratuites.
  - En cas d'erreur passagère, 3 tentatives (app/services/open_meteo_client).
  - Si Open-Meteo reste indisponible, la DERNIÈRE météo connue (jusqu'à
    6 heures) est renvoyée, marquée « stale », plutôt qu'une erreur.
"""

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import get_current_user
from app.models.utilisateur import User
from app.schemas.weather import WeatherResponse
from app.services import events
from app.services.open_meteo_client import get_json

logger = logging.getLogger(__name__)

router = APIRouter(tags=["weather"])

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

#: Une météo plus récente que ceci est servie sans interroger Open-Meteo
FRESH_S = 30 * 60
#: Une météo plus ancienne n'est plus servie, même en cas de panne
KEEP_S = 6 * 3600


def _cache_key(latitude: float, longitude: float) -> str:
    return f"awam:weather:{round(latitude, 2)}:{round(longitude, 2)}"


def _cache_get(key: str) -> dict | None:
    """Dernière météo gardée, ou None ; Redis indisponible : pas de cache."""
    try:
        raw = events._get_client().get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("🌦️ [météo] cache indisponible : %s", exc)
        return None


def _cache_set(key: str, data: dict) -> None:
    try:
        events._get_client().set(key, json.dumps({"at": time.time(), "data": data}), ex=KEEP_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("🌦️ [météo] cache indisponible : %s", exc)


@router.get("/weather", response_model=WeatherResponse)
async def get_weather(
    latitude: float,
    longitude: float,
    user: User = Depends(get_current_user),
):
    key = _cache_key(latitude, longitude)
    cached = _cache_get(key)
    if cached and time.time() - cached["at"] < FRESH_S:
        return {**cached["data"], "age_minutes": int((time.time() - cached["at"]) // 60)}

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code",
        "timezone": "auto",
    }
    try:
        payload = await get_json(OPEN_METEO_URL, params, timeout=10.0)
    except Exception as exc:  # noqa: BLE001 — panne d'Open-Meteo
        if cached:
            age = int((time.time() - cached["at"]) // 60)
            logger.warning("🌦️ [météo] Open-Meteo indisponible : météo d'il y a %d min servie (%s)", age, exc)
            return {**cached["data"], "stale": True, "age_minutes": age}
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Météo momentanément indisponible.",
        )

    current = payload.get("current", {})
    data = {
        "temperature": current.get("temperature_2m"),
        "humidity": current.get("relative_humidity_2m"),
        "precipitation": current.get("precipitation"),
        "wind_speed": current.get("wind_speed_10m"),
        "weather_code": current.get("weather_code"),
    }
    _cache_set(key, data)
    return data