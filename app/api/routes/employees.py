"""
Routes des employés : annuaire du COMPTE connecté.

Chaque requête ne voit que les employés du compte (Employee.user_id) :
un exploitant ne peut ni lire, ni modifier, ni supprimer les employés
d'un autre. Le rattachement à une ferme, avec le rôle, se gère dans
app/api/routes/farm_employees.py.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.employee import Employee
from app.models.farm_employee import FarmEmployee
from app.models.utilisateur import User
from app.schemas.employee import EmployeeCreate, EmployeeResponse, EmployeeUpdate

router = APIRouter(prefix="/employees", tags=["employees"])


def get_owned_employee(employee_id: int, user: User, db: Session) -> Employee:
    """Employé du compte connecté, sinon 404 (sans révéler qu'il existe ailleurs)."""
    employee = (
        db.query(Employee)
        .filter(Employee.id == employee_id, Employee.user_id == user.id)
        .first()
    )
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employé introuvable.")
    return employee


@router.get("", response_model=List[EmployeeResponse])
async def list_employees(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tous les employés du compte, par ordre alphabétique."""
    return (
        db.query(Employee)
        .filter(Employee.user_id == user.id)
        .order_by(Employee.full_name)
        .all()
    )


@router.get("/{employee_id}", response_model=EmployeeResponse)
async def get_employee(
    employee_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_owned_employee(employee_id, user, db)


@router.post("", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: EmployeeCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    new_employee = Employee(user_id=user.id, full_name=payload.full_name, phone=payload.phone)
    db.add(new_employee)
    db.commit()
    db.refresh(new_employee)
    return new_employee


@router.put("/{employee_id}", response_model=EmployeeResponse)
async def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employee = get_owned_employee(employee_id, user, db)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(employee, field, value)

    # Employé rendu inactif, dans toutes ses fermes : il quitte ses équipes
    # et perd son rôle de coordinateur (la ferme en désignera un autre).
    # Son historique de tâches est conservé.
    if update_data.get("is_active") is False:
        for link in db.query(FarmEmployee).filter(FarmEmployee.employee_id == employee.id).all():
            link.team = None
            if link.role == "coordinateur":
                link.role = "membre"

    db.commit()
    db.refresh(employee)
    return employee


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_employee(
    employee_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Supprime l'employé du compte, et donc de toutes ses fermes."""
    employee = get_owned_employee(employee_id, user, db)
    db.delete(employee)
    db.commit()
    return None