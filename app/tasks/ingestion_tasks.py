"""
Tâches Celery liées à l'ingestion des 14 indices spectraux Sentinel-2.

Responsabilités :
  1. Ingérer les 14 indices pour UNE parcelle (à la demande)
  2. Ingérer les 14 indices pour TOUTES les parcelles actives (cron quotidien)
  3. Mettre à jour parcel.raster_status en fonction du résultat

Déclencheurs :
  - Celery Beat quotidien (6h) → ingest_all_active_parcels
  - Route API POST /parcels/{id}/refresh → ingest_parcel_indices
  - Création/modification de parcelle → ingest_parcel_indices.delay(...)

⚠️  Cette tâche est INDÉPENDANTE de `parcel_tasks.py` (RGB + NDVI).
    Les deux peuvent coexister. À terme, on pourra fusionner.
"""

import logging

from sqlalchemy import text

from app.db.database import SessionLocal
from app.services.sentinel_service import (
    generate_all_indices_for_parcel,
    generate_all_indices_for_parcel_safe,
)

logger = logging.getLogger(__name__)


# ======================================================================
# Helpers
# ======================================================================


def _get_farm_id_for_parcel(db, parcel_id: int) -> int | None:
    """Récupère le farm_id d'une parcelle (ou None)."""
    row = db.execute(
        text("SELECT farm_id FROM parcel WHERE id = :parcel_id"),
        {"parcel_id": parcel_id},
    ).fetchone()
    return row.farm_id if row else None


def _set_raster_status(db, parcel_id: int, raster_status: str) -> None:
    """Met à jour uniquement raster_status, jamais status (agronomique)."""
    db.execute(
        text("UPDATE parcel SET raster_status = :raster_status WHERE id = :parcel_id"),
        {"raster_status": raster_status, "parcel_id": parcel_id},
    )


# ======================================================================
# Fonction métier synchrone — UNE parcelle
# ======================================================================


def ingest_parcel_indices(parcel_id: int, *, force_refresh: bool = False) -> dict:
    """
    Ingère les 14 indices spectraux pour UNE parcelle.

    Flux :
      1. Récupère le farm_id
      2. Appelle generate_all_indices_for_parcel_safe()
      3. Met à jour parcel.raster_status ("ready" / "failed")
      4. Retourne un dict de statut détaillé

    Args:
        parcel_id:     id de la parcelle
        force_refresh: si True, ignore le cache et remplace même si la
                       scène existante a moins de nuages

    Returns:
        dict avec :
          - parcel_id
          - ok (bool)
          - error (str | None)
          - scene_id, scene_date, cloud_cover
          - inserted, skipped, cached
          - indices_calculated, indices_skipped
    """
    db = SessionLocal()
    result: dict = {
        "parcel_id": parcel_id,
        "ok": False,
        "error": None,
        "scene_id": None,
        "scene_date": None,
        "cloud_cover": None,
        "inserted": 0,
        "skipped": 0,
        "cached": False,
        "indices_calculated": [],
        "indices_skipped": {},
    }

    try:
        # ------------------------------------------------------------------
        # 1. Récupérer farm_id
        # ------------------------------------------------------------------
        try:
            farm_id = _get_farm_id_for_parcel(db, parcel_id)
        except Exception as e:
            logger.exception(
                "⚠️ [ingestion] Impossible de récupérer farm_id pour parcel_id=%s",
                parcel_id,
            )
            result["error"] = f"farm_id lookup: {e}"
            try:
                _set_raster_status(db, parcel_id, "failed")
                db.commit()
            except Exception:
                db.rollback()
            return result

        if farm_id is None:
            result["error"] = "Parcelle introuvable."
            logger.error("[ingestion] Parcelle %s introuvable", parcel_id)
            return result

        # ------------------------------------------------------------------
        # 2. Appeler le service d'ingestion
        # ------------------------------------------------------------------
        logger.info(
            "🛰️ [ingestion] Démarrage ingestion (parcel_id=%s, force_refresh=%s)",
            parcel_id, force_refresh,
        )

        info = generate_all_indices_for_parcel_safe(
            parcel_id=parcel_id, force_refresh=force_refresh
        )

        if info is None:
            result["error"] = "Aucune scène / géométrie exploitable."
            logger.info(
                "ℹ️ [ingestion] Parcelle %s : aucune scène exploitable", parcel_id
            )
            try:
                _set_raster_status(db, parcel_id, "ready")
                db.commit()
            except Exception:
                db.rollback()
            return result

        # ------------------------------------------------------------------
        # 3. Propager les infos
        # ------------------------------------------------------------------
        result["scene_id"] = info.get("scene_id")
        result["scene_date"] = info.get("scene_date")
        result["cloud_cover"] = info.get("cloud_cover")
        result["inserted"] = info.get("inserted", 0)
        result["skipped"] = info.get("skipped", 0)
        result["cached"] = info.get("cached", False)
        result["indices_calculated"] = info.get("indices_calculated", [])
        result["indices_skipped"] = info.get("indices_skipped", {})
        result["ok"] = True

        logger.info(
            "✅ [ingestion] Terminé (parcel_id=%s, scène=%s, inséré=%d, skippé=%d, cached=%s)",
            parcel_id,
            result["scene_date"],
            result["inserted"],
            result["skipped"],
            result["cached"],
        )

        # ------------------------------------------------------------------
        # 4. Mettre à jour raster_status
        # ------------------------------------------------------------------
        try:
            _set_raster_status(db, parcel_id, "ready")
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "⚠️ [ingestion] Échec mise à jour raster_status (parcel_id=%s)",
                parcel_id,
            )

        return result

    except Exception as e:
        logger.exception(
            "❌ [ingestion] Exception pour parcel_id=%s : %s", parcel_id, e
        )
        result["error"] = str(e)
        try:
            _set_raster_status(db, parcel_id, "failed")
            db.commit()
        except Exception:
            db.rollback()
        return result

    finally:
        db.close()


# ======================================================================
# Fonction métier synchrone — TOUTES les parcelles actives
# ======================================================================


def ingest_all_active_parcels(*, force_refresh: bool = False) -> dict:
    """
    Ingère les 14 indices pour TOUTES les parcelles actives.

    "Actives" = parcelles existantes en base (on peut affiner plus tard
    avec un filtre : status='active', ou raster_status != 'failed', etc.).

    Returns:
        dict {total, ok, failed, skipped_cached, results: [...]}
    """
    db = SessionLocal()
    summary: dict = {
        "total": 0,
        "ok": 0,
        "failed": 0,
        "skipped_cached": 0,
        "results": [],
    }

    try:
        rows = db.execute(
            text("SELECT id FROM parcel ORDER BY id")
        ).fetchall()
        parcel_ids = [r.id for r in rows]
        summary["total"] = len(parcel_ids)

        logger.info(
            "🛰️ [ingestion] Démarrage batch : %d parcelles à traiter",
            len(parcel_ids),
        )

        for pid in parcel_ids:
            try:
                res = ingest_parcel_indices(pid, force_refresh=force_refresh)
                summary["results"].append(res)

                if res["ok"]:
                    if res.get("cached"):
                        summary["skipped_cached"] += 1
                    else:
                        summary["ok"] += 1
                else:
                    summary["failed"] += 1

            except Exception as e:
                logger.exception(
                    "❌ [ingestion] Échec parcel_id=%s : %s", pid, e
                )
                summary["failed"] += 1
                summary["results"].append({
                    "parcel_id": pid,
                    "ok": False,
                    "error": str(e),
                })

        logger.info(
            "🏁 [ingestion] Batch terminé : %d OK, %d failed, %d cached",
            summary["ok"], summary["failed"], summary["skipped_cached"],
        )
        return summary

    finally:
        db.close()


# ======================================================================
# Wrappers Celery
# ======================================================================

try:
    from app.celery_app import celery_app

    @celery_app.task(
        name="parcels.ingest_indices",
        bind=True,
        max_retries=2,
    )
    def ingest_parcel_indices_task(
        self, parcel_id: int, force_refresh: bool = False
    ) -> dict:
        """Version Celery de `ingest_parcel_indices`. Retry auto 2 fois."""
        try:
            return ingest_parcel_indices(
                parcel_id, force_refresh=force_refresh
            )
        except Exception as exc:
            logger.exception(
                "❌ [celery] Échec ingestion indices (parcel_id=%s)", parcel_id
            )
            raise self.retry(exc=exc, countdown=60)

    @celery_app.task(
        name="parcels.ingest_all_active_parcels",
        bind=True,
        max_retries=1,
    )
    def ingest_all_active_parcels_task(
        self, force_refresh: bool = False
    ) -> dict:
        """Version Celery de `ingest_all_active_parcels`. Retry auto 1 fois."""
        try:
            return ingest_all_active_parcels(force_refresh=force_refresh)
        except Exception as exc:
            logger.exception("❌ [celery] Échec batch ingestion indices")
            raise self.retry(exc=exc, countdown=300)

except ImportError as e:
    ingest_parcel_indices_task = None
    ingest_all_active_parcels_task = None
    logger.error(
        "❌ Celery non configuré (%s) : tâches d'ingestion indisponibles. "
        "Utilisez `ingest_parcel_indices()` en synchrone.", e
    )