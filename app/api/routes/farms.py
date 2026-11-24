import re
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.utilisateur import User
from app.api.routes.auth import get_current_user
from app.schemas.farm import FarmCreate, FarmUpdate, FarmResponse

router = APIRouter(prefix="/farms", tags=["farms"])

#: Comparaison des noms : sans espaces autour, espaces multiples réduits,
#: sans tenir compte des majuscules (« Kemel 1 » = « kemel  1 »)
_NAME_KEY_SQL = r"lower(regexp_replace(btrim(name), '\s+', ' ', 'g'))"


def _name_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).lower()


def _check_farm_name_free(db: Session, user_id: int, name: str, exclude_id: int | None = None) -> None:
    """Deux fermes d'un même compte ne portent jamais le même nom."""
    taken = db.execute(
        text(f"SELECT 1 FROM farm WHERE user_id = :user_id AND {_NAME_KEY_SQL} = :key"
             " AND (CAST(:exclude_id AS INTEGER) IS NULL OR id <> :exclude_id) LIMIT 1"),
        {"user_id": user_id, "key": _name_key(name), "exclude_id": exclude_id},
    ).fetchone()
    if taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Une ferme nommée « {name.strip()} » existe déjà dans votre compte.",
        )


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
    _check_farm_name_free(db, current_user.id, payload.name)
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

    # Ferme créée avec sa position : son historique météo depuis 2020 est
    # récupéré tout de suite, en arrière-plan (sinon, à sa première parcelle)
    if payload.latitude is not None and payload.longitude is not None:
        _launch_weather_history(row.id)
    return dict(row._mapping)


def _moved(old: float | None, new: float | None) -> bool:
    """Vrai si une coordonnée change (ajoutée, retirée ou différente)."""
    if old is None or new is None:
        return (old is None) != (new is None)
    return abs(old - new) > 1e-7


def _launch_weather_history(farm_id: int) -> None:
    """Historique météo de la ferme en arrière-plan ; une panne ne bloque rien."""
    try:
        from app.tasks.weather_tasks import launch_farm_history

        launch_farm_history(farm_id)
    except Exception:  # noqa: BLE001
        pass


@router.put("/{farm_id}", response_model=FarmResponse)
async def update_farm(
    farm_id: int,
    payload: FarmUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.execute(
        text("SELECT id, name, latitude, longitude FROM farm WHERE id = :farm_id AND user_id = :user_id"),
        {"farm_id": farm_id, "user_id": current_user.id},
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Ferme introuvable ou non autorisée.")

    update_data = payload.model_dump(exclude_unset=True)
    # La position change-t-elle vraiment ? (renommer la ferme ne la change pas)
    new_lat = update_data["latitude"] if "latitude" in update_data else existing.latitude
    new_lon = update_data["longitude"] if "longitude" in update_data else existing.longitude
    position_changed = _moved(existing.latitude, new_lat) or _moved(existing.longitude, new_lon)
    # Nom vérifié seulement s'il change (une ferme déjà en double reste modifiable)
    if update_data.get("name") and _name_key(update_data["name"]) != _name_key(existing.name):
        _check_farm_name_free(db, current_user.id, update_data["name"], exclude_id=farm_id)
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
    row = result.fetchone()

    if position_changed:
        # Nouveau lieu : la météo enregistrée vient de l'ancien. Elle est
        # supprimée, avec le lieu de la projection climatique (qui garde sa
        # propre copie de l'ancienne position ; ses données suivent par
        # cascade). Le tout dans la même transaction que la modification.
        db.execute(text("DELETE FROM weather_daily WHERE farm_id = :farm_id"), {"farm_id": farm_id})
        db.execute(text("DELETE FROM weather_forecast WHERE farm_id = :farm_id"), {"farm_id": farm_id})
        db.execute(text("DELETE FROM climate_site WHERE farm_id = :farm_id"), {"farm_id": farm_id})
    db.commit()

    if position_changed:
        # Historique depuis 2020 au nouveau lieu, en arrière-plan. Si Celery
        # est indisponible, la tâche du matin le reprend (ferme sans météo),
        # et la tâche horaire du climat recrée le lieu à la nouvelle position.
        _launch_weather_history(farm_id)
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