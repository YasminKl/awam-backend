from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.utilisateur import User
from app.core.security import get_current_user

router = APIRouter(prefix="/vector", tags=["vector"])

# Sécurité : seules ces couches peuvent être exposées via cette route
ALLOWED_LAYERS = {"parcelles", "points_interet"}


def _bbox_clause(bbox: str | None, params: dict, geom_column: str = "geom") -> str:
    if not bbox:
        return ""
    try:
        min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox invalide. Format attendu: minLon,minLat,maxLon,maxLat")
    params.update({"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})
    return f"AND {geom_column} && ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)"


@router.get("/layers/{layer_id}")
async def get_vector_layer(
    layer_id: str,
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if layer_id not in ALLOWED_LAYERS:
        raise HTTPException(status_code=404, detail=f"Couche '{layer_id}' introuvable.")

    params: dict = {}

    if layer_id == "parcelles":
        # Table parcel liée à farm : on ne renvoie que les parcelles des
        # fermes de l'utilisateur courant (avant, cette route n'était pas
        # authentifiée et renvoyait les parcelles de tout le monde).
        params["user_id"] = current_user.id
        where_bbox = _bbox_clause(bbox, params, geom_column="p.geom")

        query = text(f"""
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(
                    json_build_object(
                        'type', 'Feature',
                        'geometry', ST_AsGeoJSON(p.geom)::json,
                        'properties', json_build_object(
                            'id', p.id,
                            'nom', p.name,
                            'farm_id', p.farm_id
                        )
                    )
                ), '[]'::json)
            ) AS geojson
            FROM parcel p
            JOIN farm f ON f.id = p.farm_id
            WHERE f.user_id = :user_id
            {where_bbox}
        """)
    else:
        # points_interet : table indépendante, sans lien utilisateur pour
        # l'instant (données considérées publiques/partagées)
        where_bbox = _bbox_clause(bbox, params, geom_column="geom")
        query = text(f"""
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(
                    json_build_object(
                        'type', 'Feature',
                        'geometry', ST_AsGeoJSON(geom)::json,
                        'properties', json_build_object(
                            'id', id,
                            'nom', nom
                        )
                    )
                ), '[]'::json)
            ) AS geojson
            FROM point_interet
            WHERE true {where_bbox}
        """)

    result = db.execute(query, params)
    return result.scalar()