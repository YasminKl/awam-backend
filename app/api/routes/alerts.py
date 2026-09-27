from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.utilisateur import User
from app.models.farm import Farm
from app.models.parcel import Parcel
from app.models.alert import Alert
from app.schemas.alert import AlertResponse

router = APIRouter(tags=["alerts"])


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


def _get_owned_alert(alert_id: int, user: User, db: Session) -> Alert:
    """Récupère une alerte uniquement si sa parcelle appartient à l'utilisateur courant."""
    alert = (
        db.query(Alert)
        .join(Parcel, Alert.parcel_id == Parcel.id)
        .join(Farm, Parcel.farm_id == Farm.id)
        .filter(Alert.id == alert_id, Farm.user_id == user.id)
        .first()
    )
    if not alert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alerte introuvable.")
    return alert


@router.get("/parcels/{parcel_id}/alerts", response_model=List[AlertResponse])
async def list_parcel_alerts(
    parcel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_parcel(parcel_id, user, db)

    return (
        db.query(Alert)
        .filter(Alert.parcel_id == parcel_id, Alert.resolved == False)
        .order_by(Alert.created_at.desc())
        .all()
    )


@router.patch("/alerts/{alert_id}", response_model=AlertResponse)
async def mark_alert_resolved(
    alert_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    alert = _get_owned_alert(alert_id, user, db)

    alert.resolved = True
    db.commit()
    db.refresh(alert)
    return alert