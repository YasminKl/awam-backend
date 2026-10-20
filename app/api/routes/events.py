"""
Canal de notifications en temps réel vers les pages ouvertes (Server-Sent
Events). La page ouvre UNE connexion ; le back lui transmet les événements
de ses parcelles dès qu'un worker les publie (app/services/events.py).

Format envoyé :
    event: parcel
    data: {"parcel_id": 25, "kind": "indices", "scene_date": "2026-10-13"}

Seuls les événements des parcelles de l'utilisateur connecté sont transmis.
Un commentaire est envoyé toutes les HEARTBEAT_S secondes pour garder la
connexion ouverte ; si elle se coupe, le navigateur se reconnecte seul.
"""

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.core.config import REDIS_URL
from app.core.security import get_current_user
from app.db.database import SessionLocal
from app.models.utilisateur import User
from app.services.events import CHANNEL, redis_connection_kwargs

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

#: Intervalle des messages de maintien de connexion, en secondes
HEARTBEAT_S = 25
#: Délai de reconnexion conseillé au navigateur, en millisecondes
RETRY_MS = 5000


def _owned_parcel_ids(user_id: int) -> set[int]:
    """Parcelles de l'utilisateur. Session propre : le flux dure longtemps."""
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT p.id FROM parcel p
                JOIN farm f ON f.id = p.farm_id
                WHERE f.user_id = :uid
            """),
            {"uid": user_id},
        ).fetchall()
        return {r.id for r in rows}
    finally:
        db.close()


async def _event_stream(request: Request, user_id: int, pubsub):
    """Lit le canal Redis et transmet les événements des parcelles de l'utilisateur."""
    owned = await asyncio.to_thread(_owned_parcel_ids, user_id)
    yield f"retry: {RETRY_MS}\n\n"
    last_beat = time.monotonic()

    while True:
        if await request.is_disconnected():
            break

        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message and message.get("type") == "message":
            try:
                raw = message["data"]
                event = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                parcel_id = int(event["parcel_id"])
            except Exception:
                continue

            # Parcelle inconnue : peut-être créée depuis l'ouverture du flux
            if parcel_id not in owned:
                owned = await asyncio.to_thread(_owned_parcel_ids, user_id)
            if parcel_id in owned:
                yield f"event: parcel\ndata: {json.dumps(event)}\n\n"

        if time.monotonic() - last_beat >= HEARTBEAT_S:
            yield ": maintien\n\n"
            last_beat = time.monotonic()


@router.get("/events")
async def stream_events(request: Request, user: User = Depends(get_current_user)):
    """Flux des événements des parcelles de l'utilisateur connecté."""
    client = aioredis.from_url(REDIS_URL, **redis_connection_kwargs())
    pubsub = client.pubsub()
    await pubsub.subscribe(CHANNEL)
    user_id = user.id

    async def body():
        try:
            async for chunk in _event_stream(request, user_id, pubsub):
                yield chunk
        finally:
            try:
                await pubsub.unsubscribe(CHANNEL)
                await pubsub.aclose()
                await client.aclose()
            except Exception:
                logger.debug("[Événements] Fermeture du flux incomplète", exc_info=True)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Empêche un éventuel proxy de retenir les messages
            "X-Accel-Buffering": "no",
        },
    )