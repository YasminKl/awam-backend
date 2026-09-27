"""
Routes API pour les parcelles.

Chaque parcelle est rattachée à une ferme et possède :
  - une géométrie PostGIS (Polygon)
  - des métadonnées agronomiques (culture, sol, irrigation)
  - un masque colorisé (COG) généré automatiquement
  - optionnellement des composites Sentinel-2 (RGB + NDVI), avec historique

`status` (état agronomique) et `raster_status` (état du traitement des
rasters) sont deux champs indépendants : ne jamais écrire l'un à la place
de l'autre.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

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
    # status garde sa valeur par défaut ("active") — seul raster_status
    # reflète que le traitement des rasters démarre.
    row = db.execute(
        text("""
            INSERT INTO parcel (
                farm_id, name, geom, culture_type, soil_type, irrigation_type,
                area_ha, raster_status
            )
            VALUES (
                :farm_id, :name,
                ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326),
                :culture_type, :soil_type, :irrigation_type,
                ST_Area(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)::geography) / 10000.0,
                'pending'
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
        },
    ).fetchone()
    db.commit()

    try:
        from app.tasks.parcel_tasks import generate_parcel_rasters_task

        if generate_parcel_rasters_task is not None:
            generate_parcel_rasters_task.delay(row.id)
        else:
            logger.warning(
                "Celery indisponible : rasters non générés pour parcel_id=%s", row.id
            )
    except Exception:
        logger.exception("Échec du lancement de la tâche Celery")

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
            SELECT p.id FROM parcel p
            JOIN farm f ON f.id = p.farm_id
            WHERE p.id = :parcel_id AND f.user_id = :user_id
        """),
        {"parcel_id": parcel_id, "user_id": current_user.id},
    ).fetchone()
    if not existing:
        raise HTTPException(404, "Parcelle introuvable")

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
    if payload.geometry is not None:
        updates.append("geom = ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)")
        updates.append(
            "area_ha = ST_Area(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)::geography) / 10000.0"
        )
        params["geom"] = json.dumps(payload.geometry)

    if updates:
        if payload.geometry is not None:
            updates.append("raster_status = 'pending'")

        db.execute(
            text(f"UPDATE parcel SET {', '.join(updates)} WHERE id = :parcel_id"),
            params,
        )
        db.commit()

        if payload.geometry is not None:
            try:
                from app.tasks.parcel_tasks import generate_parcel_rasters_task

                if generate_parcel_rasters_task is not None:
                    generate_parcel_rasters_task.delay(parcel_id)
            except Exception:
                logger.exception("Échec du lancement de la tâche Celery")

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


# ----------------------------------------------------------------------
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

    db.execute(
        text("DELETE FROM parcel WHERE id = :parcel_id"),
        {"parcel_id": parcel_id},
    )
    db.commit()
    return None