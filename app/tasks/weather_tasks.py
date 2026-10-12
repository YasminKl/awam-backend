"""
Tâches Celery liées à l'historique météo.

Déclencheur :
  - Celery Beat quotidien (5 h 30, avant les indices) → weather.sync_daily

Chaque matin, pour chaque ferme, la tâche reprend à partir du dernier
jour enregistré et va jusqu'à hier :
  - fonctionnement normal : les 7 derniers jours sont resynchronisés,
    car Open-Meteo corrige parfois les valeurs récentes et l'archive
    définitive remplace progressivement les valeurs de prévision ;
  - après un arrêt du serveur, quelle que soit sa durée : tous les jours
    manquants depuis le dernier jour enregistré sont rattrapés ;
  - nouvelle ferme sans aucune donnée : l'historique complet est rempli
    depuis HISTORY_START.

Le jour en cours n'est pas stocké, car il n'est pas terminé.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import text

from app.db.database import SessionLocal
from app.services.weather_history_service import farm_coordinates, sync_farm

logger = logging.getLogger(__name__)

#: Nombre de jours passés resynchronisés chaque matin, au minimum
RECENT_WINDOW_DAYS = 7

#: Début de l'historique pour une ferme qui n'a encore aucune donnée
#: (même date par défaut que le script backfill_weather)
HISTORY_START = date(2026, 1, 1)


def _last_stored_days(db) -> dict[int, date]:
    """Dernier jour enregistré pour chaque ferme qui a déjà des données."""
    rows = db.execute(
        text("SELECT farm_id, MAX(day) AS last_day FROM weather_daily GROUP BY farm_id")
    ).fetchall()

    last_days = {}
    for r in rows:
        day = r.last_day
        if isinstance(day, str):
            day = date.fromisoformat(day[:10])
        last_days[r.farm_id] = day
    return last_days


def catch_up_start(
    last_day: date | None, yesterday: date, window_days: int = RECENT_WINDOW_DAYS
) -> date:
    """
    Premier jour à synchroniser pour une ferme.

    Le plus ancien entre le lendemain du dernier jour enregistré et le
    début de la fenêtre glissante : on comble les trous sans jamais
    renoncer à la mise à jour des jours récents.
    """
    if last_day is None:
        return HISTORY_START

    window_start = yesterday - timedelta(days=window_days - 1)
    return min(last_day + timedelta(days=1), window_start)


def sync_recent_weather(days: int = RECENT_WINDOW_DAYS) -> dict:
    """
    Synchronise chaque ferme depuis son dernier jour enregistré jusqu'à
    hier. Une ferme en erreur n'empêche pas de traiter les suivantes.

    Returns:
        dict {end, farms, ok, failed, caught_up, days_written}
    """
    yesterday = date.today() - timedelta(days=1)
    window_start = yesterday - timedelta(days=days - 1)

    summary = {
        "end": yesterday.isoformat(),
        "farms": 0,
        "ok": 0,
        "failed": 0,
        "caught_up": 0,
        "days_written": 0,
    }

    db = SessionLocal()
    try:
        last_days = _last_stored_days(db)
        farms = farm_coordinates(db)
        summary["farms"] = len(farms)

        for farm in farms:
            last_day = last_days.get(farm["id"])
            start = catch_up_start(last_day, yesterday, days)

            catching_up = start < window_start
            if catching_up:
                logger.info(
                    "🌦️ [météo] Rattrapage ferme %s (%s) : dernier jour %s, reprise au %s",
                    farm["id"], farm["name"], last_day or "aucun", start,
                )

            try:
                written = sync_farm(db, farm, start, yesterday)
                summary["ok"] += 1
                summary["days_written"] += written
                if catching_up:
                    summary["caught_up"] += 1
                logger.info(
                    "🌦️ [météo] Ferme %s (%s) : %d jours enregistrés (%s → %s)",
                    farm["id"], farm["name"], written, start, yesterday,
                )
            except Exception:
                db.rollback()
                summary["failed"] += 1
                logger.exception(
                    "⚠️ [météo] Échec pour la ferme %s (%s)", farm["id"], farm["name"]
                )

        logger.info(
            "🏁 [météo] Terminé jusqu'au %s : %d fermes OK, %d en échec, "
            "%d rattrapées, %d jours écrits",
            yesterday, summary["ok"], summary["failed"],
            summary["caught_up"], summary["days_written"],
        )
        return summary

    finally:
        db.close()


# ======================================================================
# Wrapper Celery
# ======================================================================

try:
    from app.celery_app import celery_app

    @celery_app.task(
        name="weather.sync_daily",
        bind=True,
        max_retries=2,
    )
    def sync_weather_daily_task(self) -> dict:
        """Version Celery de `sync_recent_weather`. Retry auto 2 fois."""
        try:
            return sync_recent_weather()
        except Exception as exc:
            logger.exception("❌ [celery] Échec synchronisation météo")
            raise self.retry(exc=exc, countdown=600)

except ImportError as e:
    sync_weather_daily_task = None
    logger.error(
        "❌ Celery non configuré (%s) : `sync_weather_daily_task` "
        "indisponible. Utilisez `sync_recent_weather()` en synchrone.", e
    )