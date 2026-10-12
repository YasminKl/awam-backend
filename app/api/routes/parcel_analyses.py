"""
Routes des analyses de laboratoire d'une parcelle (sol et eau).

Une parcelle peut avoir plusieurs analyses, chacune datée : la plus récente
est celle affichée sur le tableau de bord, les précédentes constituent
l'historique.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.farm import Farm
from app.models.parcel import Parcel
from app.models.parcel_analysis import ParcelAnalysis
from app.models.utilisateur import User
from app.schemas.parcel_analysis import (
    ParcelAnalysisCreate,
    ParcelAnalysisResponse,
    ParcelAnalysisUpdate,
)

router = APIRouter(tags=["parcel-analyses"])


def _get_owned_parcel(parcel_id: int, user: User, db: Session) -> Parcel:
    parcel = (
        db.query(Parcel)
        .join(Farm)
        .filter(Parcel.id == parcel_id, Farm.user_id == user.id)
        .first()
    )
    if not parcel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parcelle introuvable."
        )
    return parcel


def _get_owned_analysis(analysis_id: int, user: User, db: Session) -> ParcelAnalysis:
    analysis = (
        db.query(ParcelAnalysis)
        .join(Parcel, ParcelAnalysis.parcel_id == Parcel.id)
        .join(Farm, Parcel.farm_id == Farm.id)
        .filter(ParcelAnalysis.id == analysis_id, Farm.user_id == user.id)
        .first()
    )
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Analyse introuvable."
        )
    return analysis


@router.get("/parcels/{parcel_id}/analyses", response_model=List[ParcelAnalysisResponse])
def list_parcel_analyses(
    parcel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Analyses de la parcelle, de la plus récente à la plus ancienne."""
    _get_owned_parcel(parcel_id, user, db)
    return (
        db.query(ParcelAnalysis)
        .filter(ParcelAnalysis.parcel_id == parcel_id)
        .order_by(ParcelAnalysis.analyzed_at.desc())
        .all()
    )


@router.post(
    "/parcels/{parcel_id}/analyses",
    response_model=ParcelAnalysisResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_parcel_analysis(
    parcel_id: int,
    payload: ParcelAnalysisCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_parcel(parcel_id, user, db)

    data = payload.model_dump(exclude_unset=True)
    # analyzed_at non fourni : la date du jour est appliquée par la base
    if payload.analyzed_at is None:
        data.pop("analyzed_at", None)

    analysis = ParcelAnalysis(parcel_id=parcel_id, **data)
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


@router.put("/analyses/{analysis_id}", response_model=ParcelAnalysisResponse)
def update_parcel_analysis(
    analysis_id: int,
    payload: ParcelAnalysisUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    analysis = _get_owned_analysis(analysis_id, user, db)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(analysis, field, value)

    db.commit()
    db.refresh(analysis)
    return analysis


@router.delete("/analyses/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_parcel_analysis(
    analysis_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    analysis = _get_owned_analysis(analysis_id, user, db)
    db.delete(analysis)
    db.commit()
    return None