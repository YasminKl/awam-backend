"""
Tâches Celery de la projection climatique, sur le worker « historique »
(traitements longs, qui ne doivent pas retarder les indices des parcelles).

Déclencheurs :
  - un nouveau lieu étudié (ou une nouvelle ferme)  → climate.prepare_site
  - toutes les heures (h45) : les lieux des nouvelles fermes sont créés,
    les lieux bloqués sont repris, puis le plus ancien lieu « en_attente »
    est lancé                                       → climate.check_pending
  - le 5 de chaque mois, le climat réel du mois écoulé est ajouté
    (ERA5 publie un mois avec quelques jours de retard)
                                                    → climate.update_real_monthly
  - le 20 janvier, la correction des biais est recalculée avec les
    dernières années réelles (fenêtre glissante)    → climate.recompute_yearly

UNE seule préparation à la fois (verrou partagé, app/services/climate_lock) :
Copernicus limite le nombre de demandes en attente par compte. Une
préparation qui trouve le verrou pris ne fait rien ; son lieu reste « en
attente », et il est lancé dès que la préparation en cours se termine.

Copernicus a une file d'attente et coupe parfois la connexion : une
préparation qui échoue est relancée automatiquement plus tard, et l'état
du lieu indique l'erreur entre-temps.
"""

import logging
from datetime import datetime, timedelta, timezone

from app.db.database import SessionLocal
from app.celery_app import celery_app
from app.models.climate import SITE_PENDING, SITE_READY, SITE_RUNNING, ClimateSite
from app.services import climate_projection_service as climate
from app.services.climate_lock import climate_lock, is_locked

logger = logging.getLogger(__name__)

#: Délai avant une nouvelle tentative (s), et nombre de tentatives
RETRY_DELAY_S = 30 * 60
MAX_RETRIES = 3

#: Un lieu « en_cours » depuis plus longtemps, sans préparation en cours
#: (verrou libre), a été interrompu : il est repris
STALE_AFTER = timedelta(hours=6)


@celery_app.task(name="climate.prepare_site", bind=True, max_retries=MAX_RETRIES)
def prepare_site(self, site_id: int) -> dict:
    """Prépare le climat complet d'un lieu ; relancée en cas d'échec."""
    with climate_lock() as acquired:
        if not acquired:
            # Une autre préparation tourne : ce lieu reste « en attente »,
            # il sera lancé dès qu'elle se termine
            logger.info("🌍 [climat] lieu %s en attente : une préparation est déjà en cours", site_id)
            return {"site_id": site_id, "waiting": True}
        db = SessionLocal()
        try:
            summary = climate.prepare_site(db, site_id)
            logger.info("🌍 [climat] lieu %s prêt : %s", site_id, summary)
        except ValueError:
            # Lieu supprimé entre-temps : rien à relancer
            logger.warning("🌍 [climat] lieu %s introuvable", site_id)
            summary = {"site_id": site_id, "skipped": True}
        except Exception as exc:  # noqa: BLE001 — Copernicus, réseau…
            logger.warning(
                "🌍 [climat] lieu %s en échec (tentative %s) : %s", site_id, self.request.retries + 1, exc
            )
            raise self.retry(exc=exc, countdown=RETRY_DELAY_S)
        finally:
            db.close()
    # Verrou rendu : le lieu suivant peut commencer
    check_pending.delay()
    return summary


@celery_app.task(name="climate.check_pending")
def check_pending() -> int:
    """
    Crée le lieu étudié des nouvelles fermes, reprend les lieux bloqués,
    puis lance la préparation du plus ancien lieu en attente, si aucune
    préparation n'est en cours. Retourne le nombre de lieux lancés (0 ou 1).
    """
    db = SessionLocal()
    try:
        climate.ensure_farm_sites(db)
        if is_locked():
            return 0
        # Lieux interrompus (fenêtre fermée, serveur redémarré…) : repris
        stale_before = datetime.now(timezone.utc) - STALE_AFTER
        stale = (
            db.query(ClimateSite)
            .filter(ClimateSite.status == SITE_RUNNING, ClimateSite.updated_at < stale_before)
            .all()
        )
        for site in stale:
            logger.warning("🌍 [climat] lieu %s bloqué « en_cours » : repris", site.id)
            site.status = SITE_PENDING
        db.commit()
        # Un lieu interrompu passe en premier : il attendait déjà avant les autres
        if stale:
            next_id = min(stale, key=lambda s: s.id).id
        else:
            oldest = (
                db.query(ClimateSite.id)
                .filter(ClimateSite.status == SITE_PENDING)
                .order_by(ClimateSite.updated_at.asc(), ClimateSite.id.asc())
                .first()
            )
            next_id = oldest.id if oldest else None
    finally:
        db.close()
    if next_id is None:
        return 0
    prepare_site.delay(next_id)
    return 1


@celery_app.task(name="climate.update_real_monthly")
def update_real_monthly() -> dict:
    """Ajoute le climat réel du mois écoulé à chaque lieu prêt."""
    return _for_each_ready_site(climate.update_real_month, "mise à jour mensuelle")


@celery_app.task(name="climate.recompute_yearly")
def recompute_yearly() -> dict:
    """Recalcule la correction des biais de chaque lieu prêt (fenêtre glissante)."""
    return _for_each_ready_site(climate.recompute_corrections, "recalcul annuel de la correction")


def _for_each_ready_site(action, label: str) -> dict:
    """
    Applique un traitement à chaque lieu prêt ; un lieu en erreur n'arrête
    pas les autres. Les demandes à Copernicus passent par le même verrou que
    les préparations : si l'une d'elles tourne, le traitement attend la
    prochaine exécution.
    """
    with climate_lock() as acquired:
        if not acquired:
            logger.info("🌍 [climat] %s reportée : une préparation est en cours", label)
            return {"done": 0, "failed": 0, "postponed": True}
        db = SessionLocal()
        done, failed = 0, 0
        try:
            ids = [s.id for s in db.query(ClimateSite.id).filter(ClimateSite.status == SITE_READY).all()]
            for site_id in ids:
                try:
                    action(db, site_id)
                    done += 1
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    failed += 1
                    logger.warning("🌍 [climat] %s du lieu %s en échec : %s", label, site_id, exc)
        finally:
            db.close()
    return {"done": done, "failed": failed}