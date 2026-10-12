"""
Routes API pour le dashboard utilisateur.

Fournit :
  - GET /dashboard/parcels : liste enrichie des parcelles de l'utilisateur
                             (nom, ferme, géométrie, surface, métadonnées agronomiques, dates)
  - GET /dashboard/stats   : statistiques globales
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.utilisateur import User
from app.api.routes.auth import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ----------------------------------------------------------------------
# GET /dashboard/parcels — liste enrichie
# ----------------------------------------------------------------------


@router.get("/parcels")
async def get_my_parcelles(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Retourne toutes les parcelles (de toutes les fermes) de l'utilisateur
    courant avec leur géométrie au format GeoJSON et leurs métadonnées.
    """
    result = db.execute(
        text("""
            SELECT
                p.id,
                p.name AS nom,
                f.id AS farm_id,
                f.name AS farm_name,
                ST_AsGeoJSON(p.geom)::json AS geometry,
                ST_Area(p.geom::geography) AS surface_m2,
                p.culture_type,
                p.soil_type,
                p.irrigation_type,
                p.status,
                p.raster_status,
                p.created_at,
                p.updated_at
            FROM parcel p
            JOIN farm f ON f.id = p.farm_id
            WHERE f.user_id = :user_id
            ORDER BY p.created_at DESC
        """),
        {"user_id": current_user.id},
    )
    rows = result.fetchall()

    parcelles = [
        {
            "id": row.id,
            "nom": row.nom,
            "farm_id": row.farm_id,
            "farm_name": row.farm_name,
            "geometry": row.geometry,
            "surface_m2": round(row.surface_m2, 2) if row.surface_m2 else 0,
            "surface_ha": round(row.surface_m2 / 10000, 4) if row.surface_m2 else 0,
            "culture_type": row.culture_type,
            "soil_type": row.soil_type,
            "irrigation_type": row.irrigation_type,
            "status": row.status,
            "raster_status": row.raster_status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]

    return {
        "total": len(parcelles),
        "surface_totale_m2": round(sum(p["surface_m2"] for p in parcelles), 2),
        "surface_totale_ha": round(sum(p["surface_ha"] for p in parcelles), 4),
        "parcelles": parcelles,
    }


# ----------------------------------------------------------------------
# GET /dashboard/stats — statistiques globales
# ----------------------------------------------------------------------


@router.get("/stats")
async def get_dashboard_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Statistiques rapides pour le dashboard (toutes fermes confondues).
    """
    result = db.execute(
        text("""
            SELECT
                COUNT(p.*) AS nb_parcelles,
                COUNT(DISTINCT f.id) AS nb_fermes,
                COALESCE(SUM(ST_Area(p.geom::geography)), 0) AS surface_totale_m2
            FROM farm f
            LEFT JOIN parcel p ON p.farm_id = f.id
            WHERE f.user_id = :user_id
        """),
        {"user_id": current_user.id},
    )
    row = result.fetchone()

    return {
        "nb_parcelles": row.nb_parcelles,
        "nb_fermes": row.nb_fermes,
        "surface_totale_m2": round(row.surface_totale_m2, 2),
        "surface_totale_ha": round(row.surface_totale_m2 / 10000, 4),
    }
