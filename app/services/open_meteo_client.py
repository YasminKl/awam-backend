"""
Appels « en direct » à Open-Meteo (météo du jour, secours du bilan
hydrique), avec de nouvelles tentatives en cas d'erreur passagère :
lenteur, coupure réseau, serveur surchargé (5xx) ou trop sollicité (429).

L'historique quotidien, lui, passe par weather_history_service, qui a son
propre mécanisme de tentatives.
"""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

#: Tentatives, et pauses (s) entre elles
ATTEMPTS = 3
PAUSES_S = (1.0, 3.0)


def is_temporary(exc: Exception) -> bool:
    """Erreur passagère : lenteur, coupure réseau, serveur surchargé."""
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


async def get_json(url: str, params: dict, timeout: float = 10.0) -> dict:
    """
    GET vers Open-Meteo, avec jusqu'à ATTEMPTS tentatives si l'erreur est
    passagère. Lève la dernière erreur si toutes échouent.
    """
    for attempt in range(ATTEMPTS):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=timeout)
                response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 — on trie ci-dessous
            if attempt == ATTEMPTS - 1 or not is_temporary(exc):
                raise
            pause = PAUSES_S[min(attempt, len(PAUSES_S) - 1)]
            logger.warning(
                "🌦️ [météo] Open-Meteo indisponible (%s), nouvelle tentative dans %.0f s",
                exc.__class__.__name__, pause,
            )
            await asyncio.sleep(pause)
    return {}