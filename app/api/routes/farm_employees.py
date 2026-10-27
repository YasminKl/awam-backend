"""
Rattachement des employés aux fermes : QUI travaille sur QUELLE ferme,
avec QUEL rôle (« coordinateur » ou « membre »).

Règles :
  - la ferme doit appartenir au compte connecté ;
  - l'employé aussi : on ne rattache jamais l'employé d'un autre compte ;
  - UN SEUL coordinateur par ferme : en désigner un nouveau fait
    automatiquement repasser l'ancien en « membre ».
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.utilisateur import User
from app.models.farm import Farm
from app.models.farm_employee import FarmEmployee
from app.models.employee import Employee
from app.services.task_types import canonical_code
from app.schemas.farm_employee import (
    TeamAssignment,
    FarmEmployeeCreate,
    FarmEmployeeUpdate,
    FarmEmployeeResponse,
)

router = APIRouter(prefix="/farms", tags=["farm-employees"])

COORDINATOR = "coordinateur"
MEMBER = "membre"


def _get_owned_farm(farm_id: int, user: User, db: Session) -> Farm:
    farm = db.query(Farm).filter(Farm.id == farm_id, Farm.user_id == user.id).first()
    if not farm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ferme introuvable.")
    return farm


def _keep_single_coordinator(db: Session, farm_id: int, coordinator_link_id: int) -> None:
    """Un seul coordinateur par ferme : les autres repassent « membre »."""
    (
        db.query(FarmEmployee)
        .filter(
            FarmEmployee.farm_id == farm_id,
            FarmEmployee.role == COORDINATOR,
            FarmEmployee.id != coordinator_link_id,
        )
        .update({FarmEmployee.role: MEMBER}, synchronize_session=False)
    )


@router.get("/{farm_id}/employees", response_model=List[FarmEmployeeResponse])
async def list_farm_employees(
    farm_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_farm(farm_id, user, db)
    return (
        db.query(FarmEmployee)
        .filter(FarmEmployee.farm_id == farm_id)
        .order_by(FarmEmployee.hired_at)
        .all()
    )


@router.post("/{farm_id}/employees", response_model=FarmEmployeeResponse, status_code=status.HTTP_201_CREATED)
async def add_farm_employee(
    farm_id: int,
    payload: FarmEmployeeCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_farm(farm_id, user, db)

    # Seuls les employés du compte peuvent être rattachés à ses fermes
    employee = (
        db.query(Employee)
        .filter(Employee.id == payload.employee_id, Employee.user_id == user.id)
        .first()
    )
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employé introuvable.")

    existing = (
        db.query(FarmEmployee)
        .filter(FarmEmployee.farm_id == farm_id, FarmEmployee.employee_id == payload.employee_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cet employé est déjà affecté à cette ferme.")

    link = FarmEmployee(farm_id=farm_id, employee_id=payload.employee_id, role=payload.role)
    db.add(link)
    db.flush()
    if link.role == COORDINATOR:
        _keep_single_coordinator(db, farm_id, link.id)
    db.commit()
    db.refresh(link)
    return link


@router.post("/{farm_id}/employees/assign-team", response_model=List[FarmEmployeeResponse])
async def assign_team(
    farm_id: int,
    payload: TeamAssignment,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Place plusieurs employés de la ferme dans une équipe en une seule fois
    (ou les retire de leur équipe si `team` est vide). Un employé qui était
    dans une autre équipe la quitte automatiquement : une seule équipe par
    employé et par ferme. Renvoie tous les employés de la ferme, à jour.
    """
    _get_owned_farm(farm_id, user, db)
    # Nom d'équipe : prédéfini, personnalisé existant, ou nouveau
    team = canonical_code(db, user.id, payload.team) if payload.team else None

    links = (
        db.query(FarmEmployee)
        .filter(FarmEmployee.farm_id == farm_id, FarmEmployee.id.in_(payload.link_ids))
        .all()
    )
    if len(links) != len(payload.link_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Un ou plusieurs employés sont introuvables dans cette ferme.")

    # Un employé inactif ne rejoint aucune équipe
    if payload.team:
        inactive = [l.employee.full_name for l in links if not l.employee.is_active]
        if inactive:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Employé inactif : {', '.join(inactive)}. Réactivez-le avant de l'affecter à une équipe.",
            )

    for link in links:
        link.team = payload.team or None
    db.commit()

    return (
        db.query(FarmEmployee)
        .filter(FarmEmployee.farm_id == farm_id)
        .order_by(FarmEmployee.hired_at)
        .all()
    )


@router.put("/{farm_id}/employees/{link_id}", response_model=FarmEmployeeResponse)
async def update_farm_employee(
    farm_id: int,
    link_id: int,
    payload: FarmEmployeeUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_farm(farm_id, user, db)

    link = db.query(FarmEmployee).filter(FarmEmployee.id == link_id, FarmEmployee.farm_id == farm_id).first()
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Affectation introuvable.")

    update_data = payload.model_dump(exclude_unset=True)
       # Équipe : nom ramené à son écriture existante, ou vide (sans équipe)
    if "team" in update_data:
        update_data["team"] = canonical_code(db, user.id, update_data["team"]) if update_data["team"] else None
    # Un employé inactif ne rejoint aucune équipe et n'est pas coordinateur
    if not link.employee.is_active and (update_data.get("team") or update_data.get("role") == "coordinateur"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Employé inactif : réactivez-le d'abord.",
        )
    for field, value in update_data.items():
        setattr(link, field, value)

    if link.role == COORDINATOR:
        _keep_single_coordinator(db, farm_id, link.id)
    db.commit()
    db.refresh(link)
    return link


@router.delete("/{farm_id}/employees/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_farm_employee(
    farm_id: int,
    link_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_farm(farm_id, user, db)

    link = db.query(FarmEmployee).filter(FarmEmployee.id == link_id, FarmEmployee.farm_id == farm_id).first()
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Affectation introuvable.")

    db.delete(link)
    db.commit()
    return None