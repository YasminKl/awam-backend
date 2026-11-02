"""
Routes des tâches (activités) d'une parcelle : historique, planification,
création, modification, réalisation et suppression.

Règles :
  - tout passe par une parcelle du compte connecté ;
  - une tâche À RÉALISER a une date prévue, et pas de date réelle ;
  - une tâche FAITE (réalisée, ou à entretenir) a une date réelle et au
    moins un employé : ceux qui l'ont vraiment faite ;
  - une NOUVELLE tâche n'est jamais confiée à un employé inactif ;
  - la culture choisie doit être sur la parcelle de la tâche ;
  - une tâche attachée l'est à une tâche principale (un seul niveau) ;
    supprimer la principale supprime ses tâches attachées.
"""

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.activity import DONE_STATUSES, STATUS_DONE, STATUS_FOLLOW_UP, STATUS_PLANNED, Activity
from app.models.crop import Crop
from app.models.employee import Employee
from app.models.farm import Farm
from app.models.parcel import Parcel
from app.models.utilisateur import User
from app.schemas.activity import ActivityComplete, ActivityCreate, ActivityResponse, ActivityUpdate, FarmHistoryItem
from app.services.agronomy.species import SPECIES
from app.services.task_types import canonical_code

router = APIRouter(tags=["activities"])


# ----------------------------------------------------------------------
# Contrôles
# ----------------------------------------------------------------------


def _get_owned_parcel(parcel_id: int, user: User, db: Session) -> Parcel:
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
    activity = (
        db.query(Activity)
        .join(Parcel, Activity.parcel_id == Parcel.id)
        .join(Farm, Parcel.farm_id == Farm.id)
        .filter(Activity.id == activity_id, Farm.user_id == user.id)
        .first()
    )
    if not activity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tâche introuvable.")
    return activity


def _owned_employees(ids: List[int], user: User, db: Session, require_active: bool) -> List[Employee]:
    """Employés du compte ; inactifs refusés pour une tâche à venir."""
    if not ids:
        return []
    employees = db.query(Employee).filter(Employee.id.in_(ids), Employee.user_id == user.id).all()
    if len(employees) != len(set(ids)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Un ou plusieurs employés sont introuvables.")
    if require_active:
        inactive = [e.full_name for e in employees if not e.is_active]
        if inactive:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Employé inactif : {', '.join(inactive)}.",
            )
    return employees


def _check_crop(crop_id: Optional[int], parcel_id: int, db: Session) -> None:
    if crop_id is None:
        return
    if not db.query(Crop).filter(Crop.id == crop_id, Crop.parcel_id == parcel_id).first():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Cette culture n'est pas sur la parcelle de la tâche.")


def _team_code(team: Optional[str], user: User, db: Session) -> Optional[str]:
    """Équipe chargée : nom ramené à son écriture existante, ou aucune."""
    return canonical_code(db, user.id, team) if team else None


def _apply_status(activity: Activity) -> None:
    """Cohérence des dates selon l'état de la tâche."""
    if activity.status == STATUS_PLANNED:
        if activity.planned_for is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Indiquez la date prévue de la tâche.")
        activity.performed_at = None
    elif activity.performed_at is None:
        activity.performed_at = datetime.now(timezone.utc)


def _require_people_if_done(activity: Activity) -> None:
    if activity.status in DONE_STATUSES and not activity.employees:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Indiquez au moins un employé qui a fait la tâche.")


# ----------------------------------------------------------------------
# Lecture
# ----------------------------------------------------------------------


@router.get("/parcels/{parcel_id}/activities", response_model=List[ActivityResponse])
async def list_parcel_activities(
    parcel_id: int,
    days: Optional[int] = Query(
        None,
        ge=1,
        le=365,
        description="Ne renvoyer que les tâches faites des N derniers jours (ex. 5 pour l'historique du dashboard). Omis = tout l'historique.",
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Historique : seulement les tâches FAITES, les plus récentes d'abord."""
    _get_owned_parcel(parcel_id, user, db)

    query = db.query(Activity).filter(Activity.parcel_id == parcel_id, Activity.status.in_(DONE_STATUSES))

    if days is not None:
        # Jours calendaires : "5 jours" = aujourd'hui + les 4 jours précédents,
        # à partir de minuit, et non des dernières 120 heures.
        start_of_today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Activity.performed_at >= start_of_today - timedelta(days=days - 1))

    return query.order_by(Activity.performed_at.desc()).all()


@router.get("/parcels/{parcel_id}/planning", response_model=List[ActivityResponse])
async def list_parcel_planning(
    parcel_id: int,
    start: date = Query(..., description="Premier jour affiché"),
    end: date = Query(..., description="Dernier jour affiché"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Planification d'une parcelle entre deux dates : tâches à réaliser (date
    prévue) et tâches faites (date réelle), tâches attachées comprises.
    """
    _get_owned_parcel(parcel_id, user, db)
    if end < start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="La fin doit suivre le début.")
    day = func.coalesce(Activity.planned_for, func.date(Activity.performed_at))
    return (
        db.query(Activity)
        .filter(Activity.parcel_id == parcel_id, day >= start, day <= end)
        # Dans une journée : par heure prévue, les tâches sans heure en dernier
        .order_by(day, Activity.planned_time.nullslast(), Activity.parent_id.nullsfirst(), Activity.id)
        .all()
    )


@router.get("/farms/{farm_id}/history", response_model=List[FarmHistoryItem])
async def list_farm_history(
    farm_id: int,
    days: Optional[int] = Query(
        30,
        ge=1,
        le=3650,
        description="Tâches faites des N derniers jours (7, 30, 90…). Omis ou vide = tout l'historique.",
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Historique des opérations d'une ferme : les tâches FAITES (réalisées ou
    à entretenir) de toutes ses parcelles, les plus récentes d'abord, avec
    leurs employés, leur parcelle et leur culture. Les filtres par employé,
    équipe ou type se font à l'affichage.
    """
    farm = db.query(Farm).filter(Farm.id == farm_id, Farm.user_id == user.id).first()
    if not farm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ferme introuvable.")

    query = (
        db.query(Activity)
        .join(Parcel, Activity.parcel_id == Parcel.id)
        .filter(Parcel.farm_id == farm_id, Activity.status.in_(DONE_STATUSES))
    )
    if days is not None:
        # Jours calendaires, comme l'historique d'une parcelle
        start_of_today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Activity.performed_at >= start_of_today - timedelta(days=days - 1))

    items: List[FarmHistoryItem] = []
    for activity in query.order_by(Activity.performed_at.desc(), Activity.id.desc()).all():
        crop = db.get(Crop, activity.crop_id) if activity.crop_id else None
        spec = SPECIES.get(crop.species) if crop else None
        items.append(
            FarmHistoryItem(
                **ActivityResponse.model_validate(activity).model_dump(),
                parcel_name=activity.parcel.name,
                crop_label=(spec.label if spec else crop.species) if crop else None,
            )
        )
    return items


@router.get("/employees/{employee_id}/tasks", response_model=List[ActivityResponse])
async def list_employee_tasks(
    employee_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tâches en cours d'un employé : à réaliser, puis à entretenir."""
    employee = db.query(Employee).filter(Employee.id == employee_id, Employee.user_id == user.id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employé introuvable.")
    return (
        db.query(Activity)
        .filter(
            Activity.employees.any(Employee.id == employee_id),
            Activity.status.in_((STATUS_PLANNED, STATUS_FOLLOW_UP)),
        )
        .order_by(Activity.planned_for.nullslast(), Activity.performed_at)
        .all()
    )


# ----------------------------------------------------------------------
# Écriture
# ----------------------------------------------------------------------


@router.post("/activities", response_model=ActivityResponse, status_code=status.HTTP_201_CREATED)
async def create_activity(
    payload: ActivityCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_parcel(payload.parcel_id, user, db)
    _check_crop(payload.crop_id, payload.parcel_id, db)

    if payload.parent_id is not None:
        parent = _get_owned_activity(payload.parent_id, user, db)
        if parent.parent_id is not None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Une tâche attachée ne peut pas en avoir d'autres.")

    # Une nouvelle tâche n'est jamais confiée à un employé inactif
    employees = _owned_employees(payload.employee_ids, user, db, require_active=True)

    activity = Activity(
        parcel_id=payload.parcel_id,
        # Type prédéfini, personnalisé existant, ou nouveau type personnalisé
        activity_type=canonical_code(db, user.id, payload.activity_type),
        status=payload.status,
        planned_for=payload.planned_for,
        planned_time=payload.planned_time,
        performed_at=payload.performed_at,
        crop_id=payload.crop_id,
        team=_team_code(payload.team, user, db),
        parent_id=payload.parent_id,
    )
    activity.employees = employees
    _apply_status(activity)
    _require_people_if_done(activity)

    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


@router.put("/activities/{activity_id}", response_model=ActivityResponse)
async def update_activity(
    activity_id: int,
    payload: ActivityUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    activity = _get_owned_activity(activity_id, user, db)
    update_data = payload.model_dump(exclude_unset=True, exclude={"employee_ids"})

    if payload.parcel_id is not None:
        _get_owned_parcel(payload.parcel_id, user, db)
    if "team" in update_data:
        update_data["team"] = _team_code(update_data["team"], user, db)
    if update_data.get("activity_type"):
        update_data["activity_type"] = canonical_code(db, user.id, update_data["activity_type"])

    for field, value in update_data.items():
        setattr(activity, field, value)
    _check_crop(activity.crop_id, activity.parcel_id, db)

    if payload.employee_ids is not None:
        # Tâche encore à faire : seulement des employés actifs
        activity.employees = _owned_employees(
            payload.employee_ids, user, db, require_active=activity.status == STATUS_PLANNED
        )

    _apply_status(activity)
    _require_people_if_done(activity)
    db.commit()
    db.refresh(activity)
    return activity


@router.post("/activities/{activity_id}/complete", response_model=ActivityResponse)
async def complete_activity(
    activity_id: int,
    payload: ActivityComplete,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Marque une tâche comme faite : date réelle et employés qui l'ont
    vraiment faite. « À entretenir » si elle demande encore un suivi.
    """
    activity = _get_owned_activity(activity_id, user, db)
    activity.status = STATUS_FOLLOW_UP if payload.follow_up else STATUS_DONE
    activity.performed_at = payload.performed_at or datetime.now(timezone.utc)
    activity.employees = _owned_employees(payload.employee_ids, user, db, require_active=False)
    db.commit()
    db.refresh(activity)
    return activity


@router.delete("/activities/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_activity(
    activity_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Supprime la tâche, et ses tâches attachées s'il s'agit d'une principale."""
    activity = _get_owned_activity(activity_id, user, db)
    db.delete(activity)
    db.commit()
    return None