"""
Tâches Celery liées aux parcelles.

Responsabilités, pour UNE parcelle :
  1. Générer son composite Sentinel-2 RGB + NDVI, découpé sur le polygone
  2. Mettre à jour parcel.raster_status en fonction du résultat

Les composites Sentinel sont conservés en historique (nouvelle ligne à
chaque scène), pour permettre la comparaison temporelle. Chaque ligne
porte un nom lisible et la couverture nuageuse de la scène.

Le masque colorisé n'est plus généré : il n'était affiché nulle part, et
l'image satellite est déjà découpée sur la forme exacte de la parcelle.
"""

import logging
from datetime import date as date_type

from sqlalchemy import text

from app.services.events import publish_parcel_event
from app.db.database import SessionLocal
from app.services.sentinel_service import generate_sentinel_composites_for_parcel

logger = logging.getLogger(__name__)


def _get_farm_id_for_parcel(db, parcel_id: int) -> int | None:
    row = db.execute(
        text("SELECT farm_id FROM parcel WHERE id = :parcel_id"),
        {"parcel_id": parcel_id},
    ).fetchone()
    return row.farm_id if row else None


def _get_parcel_name(db, parcel_id: int) -> str:
    """Nom de la parcelle, pour nommer ses images."""
    return db.execute(
        text("SELECT name FROM parcel WHERE id = :parcel_id"),
        {"parcel_id": parcel_id},
    ).scalar() or f"Parcelle {parcel_id}"


#: Libellé de chaque type d'image dans son nom
RASTER_LABELS = {
    "sentinel_rgb": "Image satellite",
    "sentinel_ndvi": "NDVI",
}


def _raster_name(parcel_name: str, raster_type: str, scene_date: date_type | None) -> str:
    """Nom lisible d'une image : « Kemel 1 · Image satellite · 06/10/2026 »."""
    parts = [parcel_name, RASTER_LABELS.get(raster_type, raster_type)]
    if scene_date:
        parts.append(scene_date.strftime("%d/%m/%Y"))
    return " · ".join(parts)[:255]


def _set_raster_status(db, parcel_id: int, raster_status: str) -> None:
    """Met à jour uniquement raster_status, jamais status (agronomique)."""
    db.execute(
        text("UPDATE parcel SET raster_status = :raster_status WHERE id = :parcel_id"),
        {"raster_status": raster_status, "parcel_id": parcel_id},
    )


def _add_sentinel_raster(
    db, parcel_id: int, raster_type: str, scene_date: str | None,
    b2_key: str, b2_url: str, bbox,
    *, parcel_name: str, cloud_cover: float | None,
) -> None:
    """
    Ajoute une nouvelle ligne d'historique pour un composite Sentinel, avec
    son nom lisible et la couverture nuageuse de la scène (en %, sur toute
    la scène Sentinel-2, pas seulement sur la parcelle).
    """
    parsed_date: date_type | None = None
    if scene_date:
        try:
            parsed_date = date_type.fromisoformat(scene_date)
        except ValueError:
            logger.warning("scene_date invalide reçue : %s", scene_date)

    db.execute(
        text("""
            INSERT INTO raster (parcel_id, raster_type, scene_date, nom, cloud_cover,
                                b2_key, b2_url,
                                bbox_west, bbox_south, bbox_east, bbox_north)
            VALUES (:parcel_id, :raster_type, :scene_date, :nom, :cloud_cover,
                    :b2_key, :b2_url, :w, :s, :e, :n)
        """),
        {
            "parcel_id": parcel_id, "raster_type": raster_type, "scene_date": parsed_date,
            "nom": _raster_name(parcel_name, raster_type, parsed_date),
            "cloud_cover": cloud_cover,
            "b2_key": b2_key, "b2_url": b2_url,
            "w": bbox[0] if bbox else None, "s": bbox[1] if bbox else None,
            "e": bbox[2] if bbox else None, "n": bbox[3] if bbox else None,
        },
    )


def generate_parcel_rasters(parcel_id: int) -> dict:
    """
    Génère pour UNE parcelle le composite Sentinel-2 RGB + NDVI (via
    sentinel_service.py), découpé sur le polygone.

    Met à jour parcel.raster_status à la fin :
      - "ready"  si aucune erreur n'est survenue
      - "failed" si une erreur est survenue (Sentinel, ou avant même de
                 pouvoir démarrer le traitement)

    Retourne un dict de statut détaillé.
    """
    db = SessionLocal()
    result: dict = {
        "sentinel_ok": False,
        "sentinel_skipped": False,
        "sentinel_scene_date": None,
        "sentinel_cloud_cover": None,
        "sentinel_error": None,
    }

    try:
        # ------------------------------------------------------------------
        # Récupération de farm_id : isolée dans son propre try/except pour
        # ne jamais laisser raster_status bloqué sur "pending" en cas de
        # panne DB à cette étape précise. On relance l'exception d'origine
        # après la tentative de marquage, pour ne pas changer le comportement
        # de retry Celery existant.
        # ------------------------------------------------------------------
        try:
            farm_id = _get_farm_id_for_parcel(db, parcel_id)
        except Exception:
            logger.exception(
                "⚠️ [task] Impossible de récupérer farm_id pour parcel_id=%s", parcel_id
            )
            try:
                _set_raster_status(db, parcel_id, "failed")
                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "⚠️ [task] Impossible de marquer raster_status=failed (parcel_id=%s)",
                    parcel_id,
                )
            raise  # comportement inchangé : Celery voit l'échec et retry comme avant

        if farm_id is None:
            result["sentinel_error"] = "Parcelle introuvable."
            logger.error("[task] Parcelle %s introuvable, tâche annulée", parcel_id)
            return result

        parcel_name = _get_parcel_name(db, parcel_id)

        # Composite Sentinel-2 RGB + NDVI
        try:
            logger.info("🛰️ [task] Recherche scène Sentinel-2 (parcel_id=%s)", parcel_id)
            sentinel_info = generate_sentinel_composites_for_parcel(db, farm_id, parcel_id)

            if sentinel_info:
                bbox = sentinel_info.get("bbox")
                cloud_cover = sentinel_info.get("cloud_cover")

                _add_sentinel_raster(
                    db, parcel_id, "sentinel_rgb", sentinel_info.get("scene_date"),
                    b2_key=sentinel_info["rgb_key"], b2_url=sentinel_info["rgb_url"], bbox=bbox,
                    parcel_name=parcel_name, cloud_cover=cloud_cover,
                )
                _add_sentinel_raster(
                    db, parcel_id, "sentinel_ndvi", sentinel_info.get("scene_date"),
                    b2_key=sentinel_info["ndvi_key"], b2_url=sentinel_info["ndvi_url"], bbox=bbox,
                    parcel_name=parcel_name, cloud_cover=cloud_cover,
                )
                db.commit()

                result["sentinel_ok"] = True
                result["sentinel_scene_date"] = sentinel_info.get("scene_date")
                result["sentinel_cloud_cover"] = sentinel_info.get("cloud_cover")

                logger.info(
                    "✅ [task] Sentinel-2 généré (parcel_id=%s, scène du %s)",
                    parcel_id, sentinel_info.get("scene_date"),
                )
            else:
                result["sentinel_skipped"] = True
                logger.info("ℹ️ [task] Aucune scène Sentinel-2 trouvée (parcel_id=%s)", parcel_id)

        except Exception as err:
            db.rollback()
            result["sentinel_error"] = str(err)
            logger.exception("⚠️ [task] Génération Sentinel-2 échouée (parcel_id=%s)", parcel_id)

        # ------------------------------------------------------------------
        # Conclusion : c'est ici qu'on corrige le bug d'origine. On met à
        # jour raster_status pour que la parcelle ne reste jamais bloquée
        # sur "pending" une fois le traitement terminé.
        # ------------------------------------------------------------------
        has_error = bool(result.get("sentinel_error"))
        try:
            _set_raster_status(db, parcel_id, "failed" if has_error else "ready")
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("⚠️ [task] Échec de la mise à jour de raster_status (parcel_id=%s)", parcel_id)

        # Prévient les pages ouvertes : la carte recharge son image
        publish_parcel_event(parcel_id, "failed" if has_error else "rasters")

        if has_error:
            logger.warning(
                "⚠️ [task] Terminé avec erreurs pour parcel_id=%s : %s",
                parcel_id,
                {k: v for k, v in result.items() if k.endswith("_error") and v},
            )
        else:
            logger.info("🏁 [task] Terminé avec succès pour parcel_id=%s", parcel_id)

        return result

    finally:
        db.close()


# ----------------------------------------------------------------------
# Wrapper Celery
# ----------------------------------------------------------------------

try:
    from app.celery_app import celery_app

    @celery_app.task(
        name="parcels.generate_rasters",
        bind=True,
        max_retries=2,
    )
    def generate_parcel_rasters_task(self, parcel_id: int) -> dict:
        """Version Celery de `generate_parcel_rasters`. Retry auto 2 fois."""
        try:
            return generate_parcel_rasters(parcel_id)
        except Exception as exc:
            logger.exception("❌ [celery] Échec génération rasters (parcel_id=%s)", parcel_id)
            raise self.retry(exc=exc, countdown=30)

except ImportError as e:
    generate_parcel_rasters_task = None
    logger.error(
        "❌ Celery non configuré (%s) : `generate_parcel_rasters_task` "
        "indisponible. Utilisez `generate_parcel_rasters()` en synchrone.", e
    )