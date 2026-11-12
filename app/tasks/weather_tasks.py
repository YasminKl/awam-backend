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
from app.services.weather_history_service import farm_coordinates, fetch_forecast, save_forecast, sync_farm

logger = logging.getLogger(__name__)

#: Nombre de jours passés resynchronisés chaque matin, au minimum
RECENT_WINDOW_DAYS = 7

#: Début de l'historique pour une ferme qui n'a encore aucune donnée
#: (même date par défaut que le script backfill_weather)
HISTORY_START = date(2020, 1, 1)


def _last_stored_days(db) -> dict[int, date]:
    """Dernier jour enregistré pour chaque ferme qui a déjà des données."""
    rows = db.execute(
        text("SELECT farm_id, MAX(day) AS last_day FROM weather_daily WHERE farm_id IS NOT NULL GROUP BY farm_id")
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
        "forecasts": 0,
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
            
            # Prévisions des prochains jours : une panne n'empêche pas la suite
            try:
                saved = save_forecast(db, farm, fetch_forecast(farm["latitude"], farm["longitude"]))
                summary["forecasts"] += 1
                logger.info("🌦️ [météo] Ferme %s (%s) : %d jours de prévision", farm["id"], farm["name"], saved)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning(
                    "⚠️ [météo] Prévisions indisponibles pour la ferme %s (%s) : %s",
                    farm["id"], farm["name"], exc,
                )
        # Alertes « Besoin en eau », avec la météo qui vient d'être mise à
        # jour : une erreur ici ne remet pas en cause la météo enregistrée
        try:
            from app.services.water_alert_service import sync_all

            summary["water_alerts"] = sync_all(db)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("⚠️ [alertes] Alertes d'eau non mises à jour")
        logger.info(
            "🏁 [météo] Terminé jusqu'au %s : %d fermes OK, %d en échec, "
            "%d rattrapées, %d jours écrits",
            yesterday, summary["ok"], summary["failed"],
            summary["caught_up"], summary["days_written"],
        )
        return summary

    finally:
        db.close()

def sync_farm_history(farm_id: int, today: date | None = None) -> dict:
    """
    Récupère tout de suite l'historique météo d'UNE ferme, sans attendre la
    tâche du matin : depuis HISTORY_START pour une ferme sans données, sinon
    seulement les jours manquants. Puis ses prévisions des prochains jours.
    Lancée à la création d'une ferme (avec sa position) ou d'une parcelle.
    """
    yesterday = (today or date.today()) - timedelta(days=1)
    summary = {"farm_id": farm_id, "days_written": 0, "forecast": False, "skipped": False}
    db = SessionLocal()
    try:
        farms = farm_coordinates(db, farm_id)
        if not farms:
            summary["skipped"] = True
            logger.info("🌦️ [météo] Ferme %s : ni position ni parcelle, historique remis à plus tard", farm_id)
            return summary
        farm = farms[0]
        last_day = _last_stored_days(db).get(farm_id)
        start = HISTORY_START if last_day is None else last_day + timedelta(days=1)
        if start <= yesterday:
            summary["days_written"] = sync_farm(db, farm, start, yesterday)
        try:
            save_forecast(db, farm, fetch_forecast(farm["latitude"], farm["longitude"]))
            summary["forecast"] = True
        except Exception as exc:  # noqa: BLE001 — la tâche du matin réessaiera
            db.rollback()
            logger.warning("⚠️ [météo] Prévisions indisponibles pour la ferme %s : %s", farm_id, exc)
        logger.info(
            "🌦️ [météo] Ferme %s (%s) : historique à jour, %d jours enregistrés (%s → %s)",
            farm_id, farm["name"], summary["days_written"], start, yesterday,
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

    @celery_app.task(
        name="weather.sync_farm_history",
        bind=True,
        max_retries=3,
    )
    def sync_farm_history_task(self, farm_id: int) -> dict:
        """Version Celery de `sync_farm_history`. Retry auto 3 fois, toutes les 5 min."""
        try:
            return sync_farm_history(farm_id)
        except Exception as exc:
            logger.exception("❌ [celery] Échec de l'historique météo de la ferme %s", farm_id)
            raise self.retry(exc=exc, countdown=300)

except ImportError as e:
    sync_weather_daily_task = None
    sync_farm_history_task = None
    logger.error(
        "❌ Celery non configuré (%s) : `sync_weather_daily_task` "
        "indisponible. Utilisez `sync_recent_weather()` en synchrone.", e
    )

    

def launch_farm_history(farm_id: int, countdown: int = 10) -> bool:
    """
    Lance en arrière-plan la récupération de l'historique météo d'une ferme.
    Si Celery ou Redis est indisponible, rien n'est bloqué : la tâche du
    matin rattrapera l'historique. Retourne True si la tâche est lancée.
    """
    if sync_farm_history_task is None:
        return False
    try:
        sync_farm_history_task.apply_async(args=[farm_id], countdown=countdown)
        logger.info("🌦️ [météo] Historique de la ferme %s programmé", farm_id)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("⚠️ [météo] Historique de la ferme %s non programmé : la tâche du matin le rattrapera", farm_id)
        return False