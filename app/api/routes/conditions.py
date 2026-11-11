"""
Routes de la page « Climat et cultures ».

    GET /api/farms/{farm_id}/conditions/months   les mois disponibles
    GET /api/farms/{farm_id}/conditions?month=   les chiffres d'un mois

Lecture seule : tout est calculé à partir de la météo réelle de la ferme
(weather_daily). Les calculs sont dans app/services/conditions_service.py.
"""

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.farm import Farm
from app.models.parcel import Parcel
from app.models.utilisateur import User
from app.schemas.conditions import MonthConditions, YearComparison
from app.services import conditions_service as conditions
from app.services import impact_service, vegetation_service

router = APIRouter(tags=["climat et cultures"])

MONTH_PATTERN = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def _own_farm(db: Session, farm_id: int, user: User) -> Farm:
    farm = db.query(Farm).filter(Farm.id == farm_id, Farm.user_id == user.id).first()
    if not farm:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ferme introuvable.")
    return farm


@router.get("/farms/{farm_id}/conditions/months", response_model=list[str])
def available_months(
    farm_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Les mois qui ont de la météo, du plus récent au plus ancien (« AAAA-MM »).
    Appelée à l'ouverture des onglets : la météo de la ferme est d'abord
    complétée jusqu'à hier si des jours manquent.
    """
    farm = _own_farm(db, farm_id, user)
    conditions.ensure_weather_up_to_date(db, farm.id)
    return conditions.available_months(db, farm.id)


@router.get("/farms/{farm_id}/conditions", response_model=MonthConditions)
def month_conditions(
    farm_id: int,
    month: str | None = Query(None, description="Mois au format AAAA-MM ; par défaut, le mois en cours"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Les 9 paramètres d'un mois, leur normale 2020-2025 et leur écart."""
    farm = _own_farm(db, farm_id, user)
    today = date.today()
    if month is None:
        year, number = today.year, today.month
    else:
        match = MONTH_PATTERN.match(month)
        if not match:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Mois attendu au format AAAA-MM, par exemple 2026-10.")
        year, number = int(match.group(1)), int(match.group(2))
        if (year, number) > (today.year, today.month):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Ce mois n'est pas encore commencé.")
    return conditions.month_conditions(db, farm.id, year, number, today)

    

@router.get("/farms/{farm_id}/conditions/years", response_model=YearComparison)
def compare_years(
    farm_id: int,
    month: int = Query(..., ge=1, le=12, description="Numéro du mois, de 1 à 12"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Le même mois pour chaque année depuis 2020, et sa normale : pour comparer les années."""
    farm = _own_farm(db, farm_id, user)
    return conditions.compare_years(db, farm.id, month)

    

@router.get("/parcels/{parcel_id}/impacts")
def parcel_impacts(
    parcel_id: int,
    month: str | None = Query(None, description="Mois au format AAAA-MM ; par défaut, le mois en cours"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Pour chaque culture en cours de la parcelle : ses stades du mois et leur
    climat comparé à la normale, et son bilan d'eau (mois, semaines, saison).
    """
    parcel = (
        db.query(Parcel).join(Farm, Farm.id == Parcel.farm_id)
        .filter(Parcel.id == parcel_id, Farm.user_id == user.id).first()
    )
    if not parcel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parcelle introuvable.")
    today = date.today()
    conditions.ensure_weather_up_to_date(db, parcel.farm_id, today)
    if month is None:
        year, number = today.year, today.month
    else:
        match = MONTH_PATTERN.match(month)
        if not match:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Mois attendu au format AAAA-MM, par exemple 2026-10.")
        year, number = int(match.group(1)), int(match.group(2))
        if (year, number) > (today.year, today.month):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Ce mois n'est pas encore commencé.")
    return impact_service.parcel_impacts(db, parcel, year, number, today)

    

@router.get("/parcels/{parcel_id}/vegetation")
def parcel_vegetation(
    parcel_id: int,
    month: str | None = Query(None, description="Mois au format AAAA-MM ; par défaut, le mois en cours"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Les images valides du mois, leurs indices interprétés selon le référentiel
    AWAM et la plage habituelle de la parcelle, et le climat entre deux images.
    """
    parcel = (
        db.query(Parcel).join(Farm, Farm.id == Parcel.farm_id)
        .filter(Parcel.id == parcel_id, Farm.user_id == user.id).first()
    )
    if not parcel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parcelle introuvable.")
    today = date.today()
    if month is None:
        year, number = today.year, today.month
    else:
        match = MONTH_PATTERN.match(month)
        if not match:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Mois attendu au format AAAA-MM, par exemple 2026-10.")
        year, number = int(match.group(1)), int(match.group(2))
        if (year, number) > (today.year, today.month):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Ce mois n'est pas encore commencé.")
    conditions.ensure_weather_up_to_date(db, parcel.farm_id, today)
    return vegetation_service.parcel_vegetation(db, parcel, year, number, today)