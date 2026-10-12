from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.utilisateur import User
from app.api.routes.auth import get_current_user
from app.schemas.farm import FarmCreate, FarmUpdate, FarmResponse

router = APIRouter(prefix="/farms", tags=["farms"])


@router.get("", response_model=list[FarmResponse])
async def list_farms(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT id, name, location, latitude, longitude
            FROM farm
            WHERE user_id = :user_id
            ORDER BY id
        """),
        {"user_id": current_user.id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("", response_model=FarmResponse, status_code=status.HTTP_201_CREATED)
async def create_farm(
    payload: FarmCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = db.execute(
        text("""
            INSERT INTO farm (user_id, name, location, latitude, longitude)
            VALUES (:user_id, :name, :location, :latitude, :longitude)
            RETURNING id, name, location, latitude, longitude
        """),
        {
            "user_id": current_user.id,
            "name": payload.name,
            "location": payload.location,
            "latitude": payload.latitude,
            "longitude": payload.longitude,
        },
    )
    db.commit()
    row = result.fetchone()
    return dict(row._mapping)


@router.put("/{farm_id}", response_model=FarmResponse)
async def update_farm(
    farm_id: int,
    payload: FarmUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.execute(
        text("SELECT id FROM farm WHERE id = :farm_id AND user_id = :user_id"),
        {"farm_id": farm_id, "user_id": current_user.id},
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Ferme introuvable ou non autorisée.")

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        # Aucun champ fourni : on renvoie la ferme telle quelle, sans écrire en base.
        row = db.execute(
            text("""
                SELECT id, name, location, latitude, longitude
                FROM farm
                WHERE id = :farm_id
            """),
            {"farm_id": farm_id},
        ).fetchone()
        return dict(row._mapping)

    updates: list[str] = []
    params: dict[str, Any] = {"farm_id": farm_id}

    for field, value in update_data.items():
        updates.append(f"{field} = :{field}")
        params[field] = value

    result = db.execute(
        text(f"""
            UPDATE farm SET {', '.join(updates)}
            WHERE id = :farm_id
            RETURNING id, name, location, latitude, longitude
        """),
        params,
    )
    db.commit()
    row = result.fetchone()
    return dict(row._mapping)


@router.delete("/{farm_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_farm(
    farm_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.execute(
        text("SELECT id FROM farm WHERE id = :farm_id AND user_id = :user_id"),
        {"farm_id": farm_id, "user_id": current_user.id},
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Ferme introuvable ou non autorisée.")

    # La clé étrangère activity.parcel_id n'a pas de ON DELETE CASCADE :
    # on supprime d'abord les affectations d'employés et les activités des
    # parcelles de la ferme, puis la ferme (parcelles, affectations
    # ferme-employé, alertes, indices et rasters suivent par cascade).
    # Le tout dans une seule transaction.
    db.execute(
        text("""
            DELETE FROM activity_employee
            WHERE activity_id IN (
                SELECT a.id FROM activity a
                JOIN parcel p ON p.id = a.parcel_id
                WHERE p.farm_id = :farm_id
            )
        """),
        {"farm_id": farm_id},
    )
    db.execute(
        text("""
            DELETE FROM activity
            WHERE parcel_id IN (SELECT id FROM parcel WHERE farm_id = :farm_id)
        """),
        {"farm_id": farm_id},
    )
    db.execute(
        text("DELETE FROM farm WHERE id = :farm_id"),
        {"farm_id": farm_id},
    )
    db.commit()