"""
Notifications en temps réel : un worker prévient les pages ouvertes qu'une
parcelle a de nouvelles données, pour qu'elles se mettent à jour sans
rechargement.

    Worker Celery ──publish──► Redis (canal CHANNEL) ──► routes/events.py ──► page

Le message est court : la parcelle, le type d'événement et, si utile, la
date de l'image. La page relit elle-même les données concernées.

Types d'événement :
  - "rasters" : image satellite de la carte régénérée
  - "indices" : indices du jour calculés (ou analyse terminée sans scène)
  - "history" : un lot de l'historique de 3 ans enregistré
  - "failed"  : l'analyse a échoué

Une notification n'est JAMAIS bloquante : si Redis est injoignable, la
tâche se termine normalement et ses données sont enregistrées ; seule la
mise à jour automatique de la page est perdue.
"""

from __future__ import annotations

import json
import logging

import redis

from app.core.config import REDIS_URL

logger = logging.getLogger(__name__)

#: Canal Redis des notifications de parcelles
CHANNEL = "awam:parcel_events"


def redis_connection_kwargs() -> dict:
    """
    Options de connexion communes à l'émetteur et au récepteur. Upstash
    exige TLS (rediss://) ; comme pour Celery, le certificat n'est pas
    vérifié. L'option n'est passée qu'en TLS, sinon redis-py la refuse.
    """
    if REDIS_URL.startswith("rediss://"):
        return {"ssl_cert_reqs": "none"}
    return {}


_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            REDIS_URL,
            socket_timeout=5,
            socket_connect_timeout=5,
            **redis_connection_kwargs(),
        )
    return _client


def publish_parcel_event(parcel_id: int, kind: str, **data) -> bool:
    """
    Publie un événement de parcelle. Renvoie False si la publication a
    échoué ; ne lève jamais d'exception.
    """
    payload = {"parcel_id": parcel_id, "kind": kind, **data}
    try:
        _get_client().publish(CHANNEL, json.dumps(payload, default=str))
        return True
    except Exception as e:
        logger.warning("[Événements] Notification non envoyée (%s) : %s", payload, e)
        return False