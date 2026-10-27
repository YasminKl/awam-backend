"""
Limitation des tentatives de connexion (protection contre la force brute).

Sans limite, n'importe qui peut essayer des milliers de mots de passe sur
/auth/login. Deux compteurs d'ÉCHECS, gardés dans Redis :

  - par compte (e-mail)  : MAX_FAILURES_PER_EMAIL échecs en WINDOW_SECONDS
                           → ce compte est bloqué jusqu'à la fin de la fenêtre ;
  - par adresse IP       : MAX_FAILURES_PER_IP échecs en WINDOW_SECONDS
                           → protège contre l'essai de nombreux comptes
                             depuis une même machine.

Une connexion réussie remet à zéro le compteur du compte. Les échecs sur un
e-mail inexistant comptent aussi : sinon, la différence de comportement
révélerait quels comptes existent.

L'e-mail n'est jamais stocké en clair dans Redis : seule son empreinte
SHA-256 sert de clé.

Si Redis est injoignable, la connexion reste possible (journal d'alerte) :
une panne de Redis ne doit pas empêcher tout le monde de se connecter.

⚠️  Hébergement derrière un proxy (Nginx, plateforme) : l'adresse IP vue
    par l'application est alors celle du proxy. Il faudra lire l'en-tête
    X-Forwarded-For, à configurer avec le proxy.
"""

from __future__ import annotations

import hashlib
import logging
import math

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

#: Échecs autorisés pour un même compte, puis blocage
MAX_FAILURES_PER_EMAIL = 5
#: Échecs autorisés depuis une même adresse IP, tous comptes confondus
MAX_FAILURES_PER_IP = 20
#: Durée de la fenêtre de comptage, et donc du blocage, en secondes
WINDOW_SECONDS = 15 * 60

_PREFIX = "awam:login:fail"


def _redis():
    """Même connexion Redis que les notifications (app/services/events.py)."""
    from app.services.events import _get_client

    return _get_client()


def _keys(email: str, ip: str | None) -> list[tuple[str, int]]:
    """Clés Redis des deux compteurs, avec leur limite."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    keys = [(f"{_PREFIX}:email:{digest}", MAX_FAILURES_PER_EMAIL)]
    if ip:
        keys.append((f"{_PREFIX}:ip:{ip}", MAX_FAILURES_PER_IP))
    return keys


def ensure_login_allowed(email: str, ip: str | None) -> None:
    """
    Refuse la tentative (429) si le compte ou l'adresse IP a dépassé sa
    limite d'échecs. Indique dans combien de minutes réessayer.
    """
    try:
        client = _redis()
        for key, limit in _keys(email, ip):
            failures = int(client.get(key) or 0)
            if failures >= limit:
                ttl = client.ttl(key)
                wait_s = ttl if ttl and ttl > 0 else WINDOW_SECONDS
                minutes = max(math.ceil(wait_s / 60), 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        "Trop de tentatives de connexion. "
                        f"Réessayez dans {minutes} minute{'s' if minutes > 1 else ''}."
                    ),
                    headers={"Retry-After": str(wait_s)},
                )
    except HTTPException:
        raise
    except Exception:
        logger.warning("[Connexion] Redis injoignable : limite des tentatives non vérifiée")


def record_failed_login(email: str, ip: str | None) -> None:
    """Compte un échec. La fenêtre démarre au premier échec."""
    try:
        client = _redis()
        for key, _ in _keys(email, ip):
            if client.incr(key) == 1:
                client.expire(key, WINDOW_SECONDS)
    except Exception:
        logger.warning("[Connexion] Redis injoignable : échec de connexion non compté")


def reset_failed_logins(email: str) -> None:
    """Connexion réussie : remet à zéro le compteur du compte."""
    try:
        key, _ = _keys(email, None)[0]
        _redis().delete(key)
    except Exception:
        logger.warning("[Connexion] Redis injoignable : compteur non remis à zéro")