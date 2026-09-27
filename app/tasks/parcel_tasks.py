"""
Tâches Celery liées aux parcelles.

Responsabilités, pour UNE parcelle :
  1. Rasteriser son masque colorisé
  2. Générer son composite Sentinel-2 RGB + NDVI
  3. Mettre à jour parcel.raster_status en fonction du résultat

Le masque est remplacé à chaque régénération (une seule couleur, pas
d'historique utile). Les composites Sentinel sont conservés en historique
(nouvelle ligne à chaque scène), pour permettre la comparaison temporelle.
"""

import logging
from datetime import date as date_type

from sqlalchemy import text

from app.db.database import SessionLocal
from app.services.rasterizer import rasterize_parcel
from app.services.sentinel_service import generate_sentinel_composites_for_parcel

logger = logging.getLogger(__name__)


def _get_farm_id_for_parcel(db, parcel_id: int) -> int | None:
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


def _replace_mask_raster(db, parcel_id: int, b2_key: str, b2_url: str, bbox) -> None:
    """Remplace le masque existant de la parcelle (une seule couleur, pas d'historique)."""
    db.execute(
        text("DELETE FROM raster WHERE parcel_id = :parcel_id AND raster_type = 'mask'"),
        {"parcel_id": parcel_id},
    )
    db.execute(
        text("""
            INSERT INTO raster (parcel_id, raster_type, nom, scene_date, b2_key, b2_url,
                                bbox_west, bbox_south, bbox_east, bbox_north)
            VALUES (:parcel_id, 'mask', :nom, NULL, :b2_key, :b2_url, :w, :s, :e, :n)
        """),
        {
            "parcel_id": parcel_id,
            "nom": f"mask_parcel_{parcel_id}",
            "b2_key": b2_key,
            "b2_url": b2_url,
            "w": bbox[0] if bbox else None,
            "s": bbox[1] if bbox else None,
            "e": bbox[2] if bbox else None,
            "n": bbox[3] if bbox else None,
        },
    )


def _add_sentinel_raster(
    db,
    parcel_id: int,
    raster_type: str,
    scene_date: str | None,
    b2_key: str,
    b2_url: str,
    bbox,
    cloud_cover: float | None = None,
) -> None:
    """Ajoute une nouvelle ligne d'historique pour un composite Sentinel."""
    parsed_date: date_type | None = None
    if scene_date:
        try:
            parsed_date = date_type.fromisoformat(scene_date)
        except ValueError:
            logger.warning("scene_date invalide reçue : %s", scene_date)

    # Nom unique et lisible : type + parcel + date (si connue)
    nom = f"{raster_type}_parcel_{parcel_id}"
    if scene_date:
        nom += f"_{scene_date}"

    db.execute(
        text("""
            INSERT INTO raster (parcel_id, raster_type, nom, scene_date, cloud_cover,
                                b2_key, b2_url,
                                bbox_west, bbox_south, bbox_east, bbox_north)
            VALUES (:parcel_id, :raster_type, :nom, :scene_date, :cloud_cover,
                    :b2_key, :b2_url, :w, :s, :e, :n)
        """),
        {
            "parcel_id": parcel_id,
            "raster_type": raster_type,
            "nom": nom,
            "scene_date": parsed_date,
            "cloud_cover": cloud_cover,
            "b2_key": b2_key,
            "b2_url": b2_url,
            "w": bbox[0] if bbox else None,
            "s": bbox[1] if bbox else None,
            "e": bbox[2] if bbox else None,
            "n": bbox[3] if bbox else None,
        },
    )


def generate_parcel_rasters(parcel_id: int) -> dict:
    """
    Génère pour UNE parcelle :
      1. Le masque colorisé (via rasterizer.py)
      2. Le composite Sentinel-2 RGB + NDVI (via sentinel_service.py)

    Met à jour parcel.raster_status à la fin :
      - "ready"  si aucune erreur n'est survenue
      - "failed" si une erreur est survenue

    Retourne un dict de statut détaillé.
    """
    db = SessionLocal()
    result: dict = {
        "parcelles_ok": False,
        "parcelles_error": None,
        "sentinel_ok": False,
        "sentinel_skipped": False,
        "sentinel_scene_date": None,
        "sentinel_cloud_cover": None,
        "sentinel_error": None,
    }

    try:
        # ------------------------------------------------------------------
        # Récupération de farm_id
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
            raise

        if farm_id is None:
            result["parcelles_error"] = "Parcelle introuvable."
            logger.error("[task] Parcelle %s introuvable, tâche annulée", parcel_id)
            return result

        # 1. Masque de la parcelle
        try:
            logger.info("🎨 [task] Rasterisation (parcel_id=%s)", parcel_id)
            mask_info = rasterize_parcel(db, farm_id, parcel_id)

            if mask_info:
                _replace_mask_raster(
                    db,
                    parcel_id,
                    b2_key=mask_info["key"],
                    b2_url=mask_info["url"],
                    bbox=mask_info.get("bbox"),
                )
                db.commit()
                result["parcelles_ok"] = True
                logger.info("✅ [task] Masque généré (parcel_id=%s)", parcel_id)
            else:
                logger.info(
                    "ℹ️ [task] Pas de géométrie à rasteriser (parcel_id=%s)", parcel_id
                )

        except Exception as err:
            db.rollback()
            result["parcelles_error"] = str(err)
            logger.exception("⚠️ [task] Rasterisation échouée (parcel_id=%s)", parcel_id)

        # 2. Composite Sentinel-2 RGB + NDVI
        try:
            logger.info("🛰️ [task] Recherche scène Sentinel-2 (parcel_id=%s)", parcel_id)
            sentinel_info = generate_sentinel_composites_for_parcel(db, farm_id, parcel_id)

            if sentinel_info:
                bbox = sentinel_info.get("bbox")
                cloud_cover = sentinel_info.get("cloud_cover")
                scene_date = sentinel_info.get("scene_date")

                _add_sentinel_raster(
                    db,
                    parcel_id,
                    "sentinel_rgb",
                    scene_date,
                    b2_key=sentinel_info["rgb_key"],
                    b2_url=sentinel_info["rgb_url"],
                    bbox=bbox,
                    cloud_cover=cloud_cover,
                )
                _add_sentinel_raster(
                    db,
                    parcel_id,
                    "sentinel_ndvi",
                    scene_date,
                    b2_key=sentinel_info["ndvi_key"],
                    b2_url=sentinel_info["ndvi_url"],
                    bbox=bbox,
                    cloud_cover=cloud_cover,
                )
                db.commit()

                result["sentinel_ok"] = True
                result["sentinel_scene_date"] = scene_date
                result["sentinel_cloud_cover"] = cloud_cover

                logger.info(
                    "✅ [task] Sentinel-2 généré (parcel_id=%s, scène du %s, clouds=%.4f%%)",
                    parcel_id,
                    scene_date,
                    cloud_cover if cloud_cover is not None else -1,
                )
            else:
                result["sentinel_skipped"] = True
                logger.info(
                    "ℹ️ [task] Aucune scène Sentinel-2 trouvée (parcel_id=%s)", parcel_id
                )

        except Exception as err:
            db.rollback()
            result["sentinel_error"] = str(err)
            logger.exception(
                "⚠️ [task] Génération Sentinel-2 échouée (parcel_id=%s)", parcel_id
            )

        # ------------------------------------------------------------------
        # Conclusion : mise à jour de raster_status
        # ------------------------------------------------------------------
        has_error = bool(result.get("parcelles_error") or result.get("sentinel_error"))
        try:
            _set_raster_status(db, parcel_id, "failed" if has_error else "ready")
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "⚠️ [task] Échec de la mise à jour de raster_status (parcel_id=%s)",
                parcel_id,
            )

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
            logger.exception(
                "❌ [celery] Échec génération rasters (parcel_id=%s)", parcel_id
            )
            raise self.retry(exc=exc, countdown=30)

except ImportError as e:
    generate_parcel_rasters_task = None
    logger.error(
        "❌ Celery non configuré (%s) : `generate_parcel_rasters_task` "
        "indisponible. Utilisez `generate_parcel_rasters()` en synchrone.", e
    )