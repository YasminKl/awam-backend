"""
Verrou de la projection climatique : UNE seule préparation à la fois.

Copernicus limite le nombre de demandes en attente par compte et par jeu de
données : plusieurs lieux préparés en parallèle dépasseraient cette limite
(« Maximum number queued requests… reached »). Le verrou, gardé dans Redis,
est partagé par les workers Celery et par le script manuel.

Une préparation qui ne peut pas prendre le verrou ne fait rien : son lieu
reste « en attente », et la tâche horaire (ou la fin de la préparation en
cours) le lancera ensuite.
"""

import logging
from contextlib import contextmanager
from typing import Iterator

from redis.exceptions import LockError, RedisError

from app.services import events

logger = logging.getLogger(__name__)

LOCK_KEY = "awam:climate:prepare"

#: Durée maximale du verrou : au-delà, il expire seul (préparation
#: interrompue sans pouvoir le rendre, ex. serveur arrêté)
LOCK_TIMEOUT_S = 24 * 3600


@contextmanager
def climate_lock() -> Iterator[bool]:
    """
    Prend le verrou sans attendre. Donne True si la préparation peut
    commencer, False si une autre est déjà en cours. Le verrou est rendu
    à la sortie du bloc.
    """
    lock = events._get_client().lock(LOCK_KEY, timeout=LOCK_TIMEOUT_S)
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock.release()
            except LockError:
                # Verrou expiré entre-temps : rien à rendre
                pass


def is_locked() -> bool:
    """Une préparation est-elle en cours ? En cas de doute (Redis indisponible) : oui."""
    try:
        return bool(events._get_client().exists(LOCK_KEY))
    except RedisError as exc:
        logger.warning("🌍 [climat] état du verrou inconnu (Redis) : %s", exc)
        return True