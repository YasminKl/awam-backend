from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.utilisateur import User
from app.models.farm import Farm
from app.models.parcel import Parcel
from app.models.employee import Employee
from app.models.activity import Activity
from app.schemas.activity import ActivityCreate, ActivityUpdate, ActivityResponse
from datetime import datetime, timezone

router = APIRouter(tags=["activities"])


def _get_owned_parcel(parcel_id: int, user: User, db: Session) -> Parcel:
    """Récupère une parcelle uniquement si elle appartient à l'utilisateur courant."""
    parcel = (
        db.query(Parcel)
        .join(Farm)
        .filter(Parcel.id == parcel_id, Farm.user_id == user.id)
        .first()
    )
    if not parcel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcelle introuvable.")
    return parcel


def _get_owned_activity(activity_id: int, user: User, db: Session) -> Activity:
    """Récupère une activité uniquement si sa parcelle appartient à l'utilisateur courant."""
    activity = (
        db.query(Activity)
        .join(Parcel, Activity.parcel_id == Parcel.id)
        .join(Farm, Parcel.farm_id == Farm.id)
        .filter(Activity.id == activity_id, Farm.user_id == user.id)
        .first()
    )
    if not activity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activité introuvable.")
    return activity


@router.get("/parcels/{parcel_id}/activities", response_model=List[ActivityResponse])
async def list_parcel_activities(
    parcel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_parcel(parcel_id, user, db)

    return (
        db.query(Activity)
        .filter(Activity.parcel_id == parcel_id)
        .order_by(Activity.performed_at.desc())
        .all()
    )


@router.post("/activities", response_model=ActivityResponse, status_code=status.HTTP_201_CREATED)
async def create_activity(
    payload: ActivityCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_parcel(payload.parcel_id, user, db)

    employees = db.query(Employee).filter(Employee.id.in_(payload.employee_ids)).all()
    if len(employees) != len(set(payload.employee_ids)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Un ou plusieurs employés sont introuvables.",
        )

    new_activity = Activity(
        parcel_id=payload.parcel_id,
        activity_type=payload.activity_type,
        performed_at=payload.performed_at or datetime.now(timezone.utc),
    )
    new_activity.employees = employees

    db.add(new_activity)
    db.commit()
    db.refresh(new_activity)
    return new_activity


@router.put("/activities/{activity_id}", response_model=ActivityResponse)
async def update_activity(
    activity_id: int,
    payload: ActivityUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    activity = _get_owned_activity(activity_id, user, db)

    update_data = payload.model_dump(exclude_unset=True, exclude={"employee_ids"})

    if "parcel_id" in update_data:
        _get_owned_parcel(update_data["parcel_id"], user, db)

    for field, value in update_data.items():
        setattr(activity, field, value)

    if payload.employee_ids is not None:
        employees = db.query(Employee).filter(Employee.id.in_(payload.employee_ids)).all()
        if len(employees) != len(set(payload.employee_ids)):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Un ou plusieurs employés sont introuvables.",
            )
        activity.employees = employees

    db.commit()
    db.refresh(activity)
    return activity


@router.delete("/activities/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_activity(
    activity_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    activity = _get_owned_activity(activity_id, user, db)

    db.delete(activity)
    db.commit()
    return None