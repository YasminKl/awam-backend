"""
Routes API pour les parcelles.

Chaque parcelle est rattachée à une ferme et possède :
  - une géométrie PostGIS (Polygon)
  - des métadonnées agronomiques (culture, sol, irrigation)
  - un masque colorisé (COG) généré automatiquement
  - optionnellement des composites Sentinel-2 (RGB + NDVI), avec historique
  - 14 indices spectraux (NDVI, NDMI, NDWI, NDRE, EVI, SAVI, MSAVI,
    NBR, REDEDGE, VARI, CARBONATE, SI_SOIL, PSRI, FCOVER)

`status` (état agronomique) et `raster_status` (état du traitement des
rasters) sont deux champs indépendants : ne jamais écrire l'un à la place
de l'autre.

Déclencheurs Celery (uniquement pour les parcelles ACTIVES) :
  - Création d'une parcelle active / modification de géométrie d'une
    parcelle active / passage inactive → active :
      → generate_parcel_rasters (masque + RGB + NDVI)
      → ingest_parcel_indices  (14 indices spectraux)
  - Route manuelle POST /parcels/{id}/refresh :
      → ingest_parcel_indices ET generate_parcel_rasters
        (refusée si la parcelle est inactive)
  - Celery Beat quotidien 6h :
      → ingest_all_active_parcels (parcelles actives uniquement)

Une parcelle inactive ne reçoit aucune analyse : son raster_status vaut
"none" tant qu'aucun traitement n'a été lancé.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.farm import Farm
from app.models.parcel import Parcel
from app.db.database import get_db
from app.models.utilisateur import User
from app.api.routes.auth import get_current_user
from app.schemas.parcel import ParcelCreate, ParcelResponse, ParcelUpdate
from app.services.b2_storage import generate_presigned_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/parcels", tags=["parcels"])

RASTER_TYPE_TO_FIELD = {
    "mask": "cog_url",
    "sentinel_rgb": "sentinel_rgb_url",
    "sentinel_ndvi": "sentinel_ndvi_url",
}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _fetch_raster_urls_for_parcel(db: Session, parcel_id: int) -> dict[str, str | None]:
    """Récupère les URLs signées des rasters les plus récents par type."""
    rows = db.execute(
        text("""
            SELECT DISTINCT ON (raster_type) raster_type, b2_key
            FROM raster
            WHERE parcel_id = :parcel_id
            ORDER BY raster_type, COALESCE(scene_date, created_at::date) DESC, created_at DESC
        """),
        {"parcel_id": parcel_id},
    ).fetchall()

    result: dict[str, str | None] = {v: None for v in RASTER_TYPE_TO_FIELD.values()}
    for row in rows:
        field = RASTER_TYPE_TO_FIELD.get(row.raster_type)
        if not field:
            continue
        try:
            result[field] = generate_presigned_url(row.b2_key, expires_in=3600)
        except Exception:
            logger.exception(
                "Impossible de signer l'URL B2 pour parcel_id=%s type=%s",
                parcel_id,
                row.raster_type,
            )
            result[field] = None

    return result


def _parcel_to_response(parcel_row, raster_urls: dict) -> ParcelResponse:
    """Assemble la réponse ParcelResponse à partir de la ligne SQL + rasters."""
    return ParcelResponse(
        id=parcel_row.id,
        farm_id=parcel_row.farm_id,
        name=parcel_row.name,
        culture_type=parcel_row.culture_type,
        soil_type=parcel_row.soil_type,
        irrigation_type=parcel_row.irrigation_type,
        area_ha=parcel_row.area_ha,
        status=parcel_row.status,
        cog_url=raster_urls.get("cog_url"),
        sentinel_rgb_url=raster_urls.get("sentinel_rgb_url"),
        sentinel_ndvi_url=raster_urls.get("sentinel_ndvi_url"),
        raster_status=parcel_row.raster_status,
        created_at=parcel_row.created_at,
    )


def _launch_ingestion_tasks(parcel_id: int) -> None:
    """
    Lance les 2 tâches Celery pour une parcelle :
      - generate_parcel_rasters (masque + RGB + NDVI)
      - ingest_parcel_indices  (14 indices spectraux)

    Gère les cas où Celery n'est pas configuré (warning, pas d'exception).
    """
    try:
        from app.tasks.parcel_tasks import generate_parcel_rasters_task
        from app.tasks.ingestion_tasks import ingest_parcel_indices_task

        if generate_parcel_rasters_task is not None:
            generate_parcel_rasters_task.delay(parcel_id)
            logger.info("Tâche generate_rasters lancée pour parcel_id=%s", parcel_id)
        else:
            logger.warning(
                "Celery indisponible : rasters non générés pour parcel_id=%s",
                parcel_id,
            )

        if ingest_parcel_indices_task is not None:
            ingest_parcel_indices_task.delay(parcel_id)
            logger.info("Tâche ingest_indices lancée pour parcel_id=%s", parcel_id)
        else:
            logger.warning(
                "Celery indisponible : indices non générés pour parcel_id=%s",
                parcel_id,
            )
    except Exception:
        logger.exception("Échec du lancement des tâches Celery")


# ----------------------------------------------------------------------
# GET /parcels — liste
# ----------------------------------------------------------------------


@router.get("", response_model=list[ParcelResponse])
def list_parcels(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT
                p.id, p.farm_id, p.name,
                p.culture_type, p.soil_type, p.irrigation_type,
                p.status, p.raster_status, p.created_at,
                ST_Area(p.geom::geography) / 10000.0 AS area_ha
            FROM parcel p
            JOIN farm f ON f.id = p.farm_id
            WHERE f.user_id = :user_id
            ORDER BY p.id DESC
        """),
        {"user_id": current_user.id},
    ).fetchall()

    return [
        _parcel_to_response(r, _fetch_raster_urls_for_parcel(db, r.id))
        for r in rows
    ]


# ----------------------------------------------------------------------
# POST /parcels — création
# ----------------------------------------------------------------------


@router.post("", response_model=ParcelResponse, status_code=status.HTTP_201_CREATED)
def create_parcel(
    payload: ParcelCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    farm = db.execute(
        text("SELECT id FROM farm WHERE id = :farm_id AND user_id = :user_id"),
        {"farm_id": payload.farm_id, "user_id": current_user.id},
    ).fetchone()
    if not farm:
        raise HTTPException(404, "Ferme introuvable ou non autorisée")

    geom_json = json.dumps(payload.geometry)

    # area_ha est calculée automatiquement par PostGIS depuis la géométrie.
    # Parcelle active : le traitement démarre (raster_status "pending").
    # Parcelle inactive : aucun traitement (raster_status "none").
    is_active = payload.status == "active"
    row = db.execute(
        text("""
            INSERT INTO parcel (
                farm_id, name, geom, culture_type, soil_type, irrigation_type,
                area_ha, status, raster_status
            )
            VALUES (
                :farm_id, :name,
                ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326),
                :culture_type, :soil_type, :irrigation_type,
                ST_Area(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)::geography) / 10000.0,
                :status, :raster_status
            )
            RETURNING id, farm_id, name,
                      culture_type, soil_type, irrigation_type,
                      status, raster_status, created_at, area_ha
        """),
        {
            "farm_id": payload.farm_id,
            "name": payload.name,
            "geom": geom_json,
            "culture_type": payload.culture_type,
            "soil_type": payload.soil_type,
            "irrigation_type": payload.irrigation_type,
            "status": payload.status,
            "raster_status": "pending" if is_active else "none",
        },
    ).fetchone()
    db.commit()

    # Lance les DEUX tâches (rasters + 14 indices), seulement si la parcelle est active
    if is_active:
        _launch_ingestion_tasks(row.id)

    return ParcelResponse(
        id=row.id,
        farm_id=row.farm_id,
        name=row.name,
        culture_type=row.culture_type,
        soil_type=row.soil_type,
        irrigation_type=row.irrigation_type,
        area_ha=row.area_ha,
        status=row.status,
        cog_url=None,
        sentinel_rgb_url=None,
        sentinel_ndvi_url=None,
        raster_status=row.raster_status,
        created_at=row.created_at,
    )


# ----------------------------------------------------------------------
# POST /parcels/{parcel_id}/refresh — indices + composites Sentinel
# ----------------------------------------------------------------------


@router.post("/{parcel_id}/refresh")
async def refresh_parcel_indices(
    parcel_id: int,
    force_refresh: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Relance l'analyse satellite complète d'une parcelle :
      - les 14 indices spectraux (ingest_parcel_indices)
      - l'image satellite découpée sur le polygone (generate_parcel_rasters)

    - **parcel_id** : id de la parcelle
    - **force_refresh** : si True, ignore le cache et remplace même si
                          la scène existante a moins de nuages

    Retourne immédiatement (tâches Celery lancées en arrière-plan).
    Pour suivre le statut, consulter `parcel.raster_status` ou les logs.
    """
    # Vérifier que la parcelle appartient à l'utilisateur
    parcel = (
        db.query(Parcel)
        .join(Farm)
        .filter(Parcel.id == parcel_id, Farm.user_id == current_user.id)
        .first()
    )
    if not parcel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parcelle introuvable.",
        )

    # Une parcelle inactive ne reçoit pas d'analyse satellite
    if parcel.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La parcelle est inactive : activez-la pour lancer l'analyse.",
        )

    # Marquer la parcelle comme "en cours"
    parcel.raster_status = "pending"
    db.commit()

    # Lancer la tâche Celery (import différé pour éviter un import circulaire)
    from app.tasks.ingestion_tasks import ingest_parcel_indices_task

    if ingest_parcel_indices_task is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Celery n'est pas configuré. Ingestion impossible.",
        )

    try:
        task = ingest_parcel_indices_task.delay(
            parcel_id=parcel_id, force_refresh=force_refresh
        )
        # Régénère aussi les composites (image satellite découpée sur le
        # polygone) : les indices seuls ne mettent pas l'image à jour.
        from app.tasks.parcel_tasks import generate_parcel_rasters_task

        if generate_parcel_rasters_task is not None:
            generate_parcel_rasters_task.delay(parcel_id)
            logger.info("Tâche generate_rasters relancée pour parcel_id=%s", parcel_id)
    except Exception as e:
        parcel.raster_status = "failed"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Impossible de lancer la tâche Celery : {e}",
        )

    return {
        "parcel_id": parcel_id,
        "task_id": task.id,
        "status": "pending",
        "force_refresh": force_refresh,
        "message": (
            "Analyse lancée en arrière-plan (indices + image satellite). "
            "Suivre via /parcels/{id}/raster_status ou les logs Celery."
        ),
    }


# ----------------------------------------------------------------------
# GET /parcels/{parcel_id}/raster_status — statut des rasters
# ----------------------------------------------------------------------


@router.get("/{parcel_id}/raster_status")
async def get_parcel_raster_status(
    parcel_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Retourne le statut de génération des rasters d'une parcelle.

    Valeurs possibles :
      - "pending" : tâche en cours
      - "ready"   : génération terminée avec succès
      - "failed"  : erreur pendant la génération
      - "none"    : aucun traitement lancé (parcelle inactive)
    """
    parcel = (
        db.query(Parcel)
        .join(Farm)
        .filter(Parcel.id == parcel_id, Farm.user_id == current_user.id)
        .first()
    )
    if not parcel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parcelle introuvable.",
        )

    return {
        "parcel_id": parcel_id,
        "raster_status": getattr(parcel, "raster_status", None),
    }


# ----------------------------------------------------------------------
# GET /parcels/{parcel_id} — détail
# ----------------------------------------------------------------------


@router.get("/{parcel_id}", response_model=ParcelResponse)
def get_parcel(
    parcel_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.execute(
        text("""
            SELECT
                p.id, p.farm_id, p.name,
                p.culture_type, p.soil_type, p.irrigation_type,
                p.status, p.raster_status, p.created_at,
                ST_Area(p.geom::geography) / 10000.0 AS area_ha
            FROM parcel p
            JOIN farm f ON f.id = p.farm_id
            WHERE p.id = :parcel_id AND f.user_id = :user_id
        """),
        {"parcel_id": parcel_id, "user_id": current_user.id},
    ).fetchone()

    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parcelle introuvable")

    raster_urls = _fetch_raster_urls_for_parcel(db, parcel_id)
    return _parcel_to_response(row, raster_urls)


# ----------------------------------------------------------------------
# PUT /parcels/{parcel_id} — mise à jour
# ----------------------------------------------------------------------


@router.put("/{parcel_id}", response_model=ParcelResponse)
def update_parcel(
    parcel_id: int,
    payload: ParcelUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.execute(
        text("""
            SELECT p.id, p.status FROM parcel p
            JOIN farm f ON f.id = p.farm_id
            WHERE p.id = :parcel_id AND f.user_id = :user_id
        """),
        {"parcel_id": parcel_id, "user_id": current_user.id},
    ).fetchone()
    if not existing:
        raise HTTPException(404, "Parcelle introuvable")

    # Statut après la mise à jour, et passage éventuel inactive → active
    new_status = payload.status if payload.status is not None else existing.status
    becomes_active = existing.status != "active" and new_status == "active"
    geometry_changed = payload.geometry is not None
    # L'analyse n'est lancée que pour une parcelle active : à l'activation,
    # ou quand la forme d'une parcelle active change.
    launch_analysis = new_status == "active" and (becomes_active or geometry_changed)

    updates: list[str] = []
    params: dict[str, Any] = {"parcel_id": parcel_id}

    if payload.name is not None:
        updates.append("name = :name")
        params["name"] = payload.name
    if payload.culture_type is not None:
        updates.append("culture_type = :culture_type")
        params["culture_type"] = payload.culture_type
    if payload.soil_type is not None:
        updates.append("soil_type = :soil_type")
        params["soil_type"] = payload.soil_type
    if payload.irrigation_type is not None:
        updates.append("irrigation_type = :irrigation_type")
        params["irrigation_type"] = payload.irrigation_type
    if payload.status is not None:  # ⬅️ AJOUT : permet de changer active/inactive
        updates.append("status = :status")
        params["status"] = payload.status
    if payload.geometry is not None:
        updates.append("geom = ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)")
        updates.append(
            "area_ha = ST_Area(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)::geography) / 10000.0"
        )
        params["geom"] = json.dumps(payload.geometry)

    if updates:
        if launch_analysis:
            updates.append("raster_status = 'pending'")
        elif geometry_changed:
            # Forme modifiée sur une parcelle inactive : les anciennes images
            # ne correspondent plus, l'analyse sera lancée à l'activation.
            updates.append("raster_status = 'none'")

        db.execute(
            text(f"UPDATE parcel SET {', '.join(updates)} WHERE id = :parcel_id"),
            params,
        )
        db.commit()

        # Relance les DEUX tâches (rasters + 14 indices) si la parcelle est active
        if launch_analysis:
            _launch_ingestion_tasks(parcel_id)

    row = db.execute(
        text("""
            SELECT
                p.id, p.farm_id, p.name,
                p.culture_type, p.soil_type, p.irrigation_type,
                p.status, p.raster_status, p.created_at,
                ST_Area(p.geom::geography) / 10000.0 AS area_ha
            FROM parcel p
            WHERE p.id = :parcel_id
        """),
        {"parcel_id": parcel_id},
    ).fetchone()

    raster_urls = _fetch_raster_urls_for_parcel(db, parcel_id)
    return _parcel_to_response(row, raster_urls)


# DELETE /parcels/{parcel_id}
# ----------------------------------------------------------------------


@router.delete("/{parcel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_parcel(
    parcel_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.execute(
        text("""
            SELECT p.id FROM parcel p
            JOIN farm f ON f.id = p.farm_id
            WHERE p.id = :parcel_id AND f.user_id = :user_id
        """),
        {"parcel_id": parcel_id, "user_id": current_user.id},
    ).fetchone()
    if not existing:
        raise HTTPException(404, "Parcelle introuvable")

    # La clé étrangère activity.parcel_id n'a pas de ON DELETE CASCADE :
    # on supprime d'abord les affectations d'employés et les activités de
    # la parcelle, puis la parcelle (alertes, indices et rasters suivent
    # par cascade). Le tout dans une seule transaction.
    db.execute(
        text("""
            DELETE FROM activity_employee
            WHERE activity_id IN (SELECT id FROM activity WHERE parcel_id = :parcel_id)
        """),
        {"parcel_id": parcel_id},
    )
    db.execute(
        text("DELETE FROM activity WHERE parcel_id = :parcel_id"),
        {"parcel_id": parcel_id},
    )
    db.execute(
        text("DELETE FROM parcel WHERE id = :parcel_id"),
        {"parcel_id": parcel_id},
    )
    db.commit()
    return None