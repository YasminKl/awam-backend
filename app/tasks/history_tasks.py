"""
Tâches Celery : constitution de l'historique Sentinel-2 d'une parcelle,
en arrière-plan et sans affichage. L'utilisateur voit simplement la plage
habituelle apparaître sous la courbe une fois l'historique constitué.

Déroulement (file dédiée « awam_queues:historique ») :

    history.plan_parcel
        · liste les dates de passage du satellite sur HISTORY_YEARS ans,
          moins celles déjà en base
        · lance la chaîne :
    history.ingest_batch (lot 1) → (lot 2) → … → (lot N)
        · chaque lot : BATCH_SIZE dates, environ 4 à 5 minutes

Pourquoi des lots : une tâche de 30 à 60 minutes serait considérée comme
perdue par le broker Redis au bout d'une heure, et relancée en double ;
une coupure tardive ferait tout recommencer. Avec des lots courts, une
coupure ne coûte qu'un lot.

Relances sûres : les dates déjà en base sont toujours ignorées, donc un
double lancement ou une reprise ne crée aucun doublon.

Déclencheurs :
  - création d'une parcelle active (routes/parcels.py), quelques minutes
    après les premières tâches ;
  - VÉRIFICATION HORAIRE (history.check_missing, Celery Beat) : toute
    parcelle active sans historique est repérée et son historique lancé,
    quelle qu'en soit la raison (parcelle ancienne, activée plus tard,
    Redis ou worker indisponible au moment de la création).

Protections, dans Redis :
  - « en cours » : une parcelle dont l'historique vient d'être lancé n'est
    pas relancée pendant RUNNING_LOCK_S ;
  - « terminé » : une parcelle dont l'historique vient de se terminer n'est
    plus réexaminée pendant DONE_MARK_S (ses dates trop nuageuses ne sont
    jamais en base, la réexaminer chaque heure ne servirait à rien).

⚠️  Ces tâches sont longues : elles doivent tourner sur un worker DÉDIÉ,
    pour ne jamais bloquer les indices du jour ni la tâche de 6 h :

    celery -A app.celery_app worker -Q awam_queues:historique --pool=solo -n historique@%h
"""

import logging

from sqlalchemy import text

from app.db.database import SessionLocal
from app.services import history_service as hs
from app.services.events import publish_parcel_event

logger = logging.getLogger(__name__)

#: File Celery dédiée à l'historique (voir celery_app.py)
HISTORY_QUEUE = "awam_queues:historique"

#: Une parcelle est « sans historique » si sa plus ancienne mesure est
#: postérieure au début attendu de plus de ce délai. Le délai tolère un
#: début d'année nuageux, sans aucune image exploitable pendant des semaines.
MISSING_GRACE_DAYS = 60
#: « En cours » : pas de relance pendant ce délai (un historique dure 1 à 3 h)
RUNNING_LOCK_S = 6 * 3600
#: « Terminé » : pas de réexamen pendant ce délai
DONE_MARK_S = 7 * 24 * 3600

_RUNNING_KEY = "awam:history:running:{}"
_DONE_KEY = "awam:history:done:{}"


def _redis():
    """Même connexion Redis que les notifications (app/services/events.py)."""
    from app.services.events import _get_client

    return _get_client()


def _acquire_running(parcel_id: int) -> bool:
    """
    Pose le verrou « en cours ». Faux s'il est déjà posé. Redis injoignable :
    vrai, pour ne pas bloquer le lancement (la mise en file échouera de
    toute façon, et la vérification horaire réessaiera).
    """
    try:
        return bool(_redis().set(_RUNNING_KEY.format(parcel_id), "1", nx=True, ex=RUNNING_LOCK_S))
    except Exception:
        logger.warning("[Historique] Verrou indisponible (parcelle %s)", parcel_id)
        return True


def _release_running(parcel_id: int) -> None:
    try:
        _redis().delete(_RUNNING_KEY.format(parcel_id))
    except Exception:
        pass


def _mark_done(parcel_id: int) -> None:
    """Historique terminé : lève « en cours », pose « terminé »."""
    try:
        r = _redis()
        r.delete(_RUNNING_KEY.format(parcel_id))
        r.set(_DONE_KEY.format(parcel_id), "1", ex=DONE_MARK_S)
    except Exception:
        logger.warning("[Historique] Marque « terminé » non posée (parcelle %s)", parcel_id)


def _is_done(parcel_id: int) -> bool:
    try:
        return bool(_redis().exists(_DONE_KEY.format(parcel_id)))
    except Exception:
        return False


def _parcel_exists(db, parcel_id: int) -> bool:
    return db.execute(
        text("SELECT 1 FROM parcel WHERE id = :pid"), {"pid": parcel_id}
    ).scalar() is not None


def plan_parcel_history(parcel_id: int, years: int = hs.HISTORY_YEARS) -> dict:
    """Liste les dates à traiter et lance la chaîne de lots."""
    db = SessionLocal()
    try:
        if not _parcel_exists(db, parcel_id):
            return {"parcel_id": parcel_id, "skipped": "parcelle introuvable"}

        plan = hs.plan_parcel(db, parcel_id, years=years)
        if plan is None:
            return {"parcel_id": parcel_id, "skipped": "pas de géométrie"}
    finally:
        db.close()

    if not plan.todo:
        _mark_done(parcel_id)
        logger.info("[Historique] Parcelle %s : déjà complet (%s dates)", parcel_id, plan.found)
        return {"parcel_id": parcel_id, "todo": 0, "already": plan.already}

    lots = hs.batches(plan.todo)
    _launch_chain(parcel_id, lots)
    logger.info(
        "[Historique] Parcelle %s : %s dates à traiter en %s lots (%s → %s)",
        parcel_id, len(plan.todo), len(lots), plan.start, plan.end,
    )
    return {"parcel_id": parcel_id, "todo": len(plan.todo), "batches": len(lots), "already": plan.already}


def ingest_history_batch(parcel_id: int, dates: list[str]) -> dict:
    """Traite un lot de dates."""
    db = SessionLocal()
    try:
        if not _parcel_exists(db, parcel_id):
            return {"parcel_id": parcel_id, "skipped": "parcelle supprimée"}
        result = hs.process_dates(db, parcel_id, dates)
        # Prévient les pages ouvertes : la plage habituelle se recalcule
        if result.inserted:
            publish_parcel_event(parcel_id, "history")
        logger.info(
            "[Historique] Parcelle %s : lot de %s dates · %s enregistrées · %s nuageuses · %s échecs",
            parcel_id, len(dates), result.inserted, result.cloudy, result.failed,
        )
        return {
            "parcel_id": parcel_id,
            "inserted": result.inserted,
            "cloudy": result.cloudy,
            "failed": result.failed,
        }
    finally:
        db.close()


def finalize_parcel_history(parcel_id: int) -> dict:
    """Dernière étape de la chaîne : l'historique est terminé."""
    _mark_done(parcel_id)
    logger.info("[Historique] Parcelle %s : historique terminé", parcel_id)
    return {"parcel_id": parcel_id, "done": True}


def _launch_chain(parcel_id: int, lots: list[list[str]]) -> None:
    """Enchaîne les lots puis la finalisation, sur la file dédiée."""
    from celery import chain

    chain(
        *[ingest_history_batch_task.si(parcel_id, lot) for lot in lots],
        finalize_parcel_history_task.si(parcel_id),
    ).apply_async(queue=HISTORY_QUEUE)


def enqueue_history(parcel_id: int, countdown: int = 0) -> bool:
    """
    Met la constitution de l'historique en file. Renvoie False si Celery
    ou le broker est indisponible : la création de la parcelle n'échoue pas
    pour autant, et le script permettra de constituer l'historique plus tard.
    """
    if plan_parcel_history_task is None:
        logger.warning("[Historique] Celery indisponible : historique non lancé (parcelle %s)", parcel_id)
        return False
    if not _acquire_running(parcel_id):
        logger.info("[Historique] Parcelle %s : historique déjà en cours, pas de relance", parcel_id)
        return True
    try:
        plan_parcel_history_task.apply_async(
            args=[parcel_id], countdown=countdown, queue=HISTORY_QUEUE
        )
        return True
    except Exception:
        _release_running(parcel_id)
        logger.exception("[Historique] Mise en file impossible (parcelle %s)", parcel_id)
        return False


# ======================================================================
# Vérification horaire : parcelles actives sans historique
# ======================================================================


def find_parcels_missing_history(db, years: int = hs.HISTORY_YEARS) -> list[int]:
    """
    Parcelles actives sans historique : leur plus ancienne mesure (ou
    l'absence de mesure) est postérieure au début attendu de plus de
    MISSING_GRACE_DAYS jours.

    Début attendu : le 1er janvier d'il y a `years` ans, ou la plus ancienne
    date de plantation si elle est plus récente (avant, la culture
    n'existait pas). Même règle que history_service.plan_parcel.
    """
    rows = db.execute(
        text("""
            WITH etat AS (
                SELECT p.id,
                       GREATEST(
                           make_date(EXTRACT(YEAR FROM CURRENT_DATE)::int - :years, 1, 1),
                           COALESCE(
                               (SELECT MIN(c.planting_date) FROM crop c WHERE c.parcel_id = p.id),
                               DATE '1900-01-01'
                           )
                       ) AS debut_attendu,
                       (SELECT MIN(r.scene_date) FROM indice_reading r
                        WHERE r.parcel_id = p.id) AS premiere_mesure
                FROM parcel p
                WHERE p.status = 'active' AND p.geom IS NOT NULL
            )
            SELECT id FROM etat
            WHERE premiere_mesure IS NULL
               OR premiere_mesure > debut_attendu + :grace
            ORDER BY id
        """),
        {"years": years, "grace": MISSING_GRACE_DAYS},
    ).fetchall()
    return [r.id for r in rows]


def check_missing_history() -> dict:
    """
    Lance l'historique des parcelles actives qui n'en ont pas. Ignore celles
    dont l'historique est en cours ou vient de se terminer.
    """
    db = SessionLocal()
    try:
        missing = find_parcels_missing_history(db)
    finally:
        db.close()

    launched, skipped_done = [], []
    for parcel_id in missing:
        if _is_done(parcel_id):
            skipped_done.append(parcel_id)
            continue
        if enqueue_history(parcel_id):
            launched.append(parcel_id)

    if missing:
        logger.info(
            "[Historique] Vérification : %s parcelle(s) sans historique %s · lancées %s · terminées récemment %s",
            len(missing), missing, launched, skipped_done,
        )
    return {"missing": missing, "launched": launched, "skipped_done": skipped_done}


# ======================================================================
# Wrappers Celery
# ======================================================================

try:
    from app.celery_app import celery_app

    @celery_app.task(name="history.plan_parcel", bind=True, max_retries=3)
    def plan_parcel_history_task(self, parcel_id: int, years: int = hs.HISTORY_YEARS) -> dict:
        """Planification : relancée jusqu'à 3 fois si le catalogue est injoignable."""
        try:
            return plan_parcel_history(parcel_id, years=years)
        except Exception as exc:
            logger.exception("[Historique] Échec de la planification (parcelle %s)", parcel_id)
            raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))

    @celery_app.task(name="history.ingest_batch", bind=True, max_retries=3)
    def ingest_history_batch_task(self, parcel_id: int, dates: list[str]) -> dict:
        """Un lot : relancé jusqu'à 3 fois en cas de coupure réseau."""
        try:
            return ingest_history_batch(parcel_id, dates)
        except Exception as exc:
            logger.exception("[Historique] Échec d'un lot (parcelle %s)", parcel_id)
            raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))

    @celery_app.task(name="history.finalize")
    def finalize_parcel_history_task(parcel_id: int) -> dict:
        return finalize_parcel_history(parcel_id)

    @celery_app.task(name="history.check_missing")
    def check_missing_history_task() -> dict:
        """Vérification horaire, planifiée dans celery_app.py (beat_schedule)."""
        return check_missing_history()

except ImportError as e:
    plan_parcel_history_task = None
    ingest_history_batch_task = None
    finalize_parcel_history_task = None
    check_missing_history_task = None
    logger.error("Celery non configuré (%s) : historique indisponible en tâche de fond.", e)