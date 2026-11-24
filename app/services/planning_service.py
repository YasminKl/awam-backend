"""
Disponibilité des employés : un employé ne peut pas avoir deux tâches À
RÉALISER au même moment, quelle que soit la parcelle et quel que soit le
type de tâche.

Seuls les horaires DÉJÀ ENREGISTRÉS sont utilisés, rien n'est supposé :
  - une tâche a une date et une heure prévues (sans heure de fin) ;
  - un arrosage a, en plus, une heure de début et une heure de fin, ou
    une heure de début et une durée.

Il y a conflit, pour un même employé et un même jour :
  - tâche et tâche       : même heure ;
  - arrosage et arrosage : leurs horaires se chevauchent ;
  - tâche et arrosage    : l'heure de la tâche tombe pendant l'arrosage.

Une tâche sans heure n'est pas contrôlée : sans heure, on ne peut pas
savoir si elle tombe au même moment qu'une autre.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models.activity import STATUS_PLANNED, Activity
from app.models.employee import Employee
from app.models.irrigation_watering import WATERING_TASK_TYPE, IrrigationWatering
from app.models.parcel import Parcel
from app.services.task_types import PREDEFINED_TASK_TYPES

_TASK_LABELS = dict(PREDEFINED_TASK_TYPES)

#: Un horaire : (début, fin) ; fin = None pour une tâche sans heure de fin
Window = tuple[time, Optional[time]]


def _add_minutes(t: time, minutes: float) -> time:
    end = datetime.combine(date.min, t) + timedelta(minutes=minutes)
    # Un arrosage qui finirait après minuit s'arrête, pour ce jour, à 23:59:59
    return end.time() if end.date() == date.min else time(23, 59, 59)


def window(activity: Activity, watering: Optional[IrrigationWatering]) -> Optional[Window]:
    """L'horaire enregistré d'une tâche, ou None si elle n'a pas d'heure."""
    if watering is not None:
        start = watering.start_time or activity.planned_time
        if start is not None:
            if watering.end_time is not None and watering.end_time > start:
                return start, watering.end_time
            if watering.duration_min:
                return start, _add_minutes(start, watering.duration_min)
            return start, None
    if activity.planned_time is not None:
        return activity.planned_time, None
    return None


def _overlap(a: Window, b: Window) -> bool:
    (s1, e1), (s2, e2) = a, b
    if e1 is None and e2 is None:
        return s1 == s2
    if e1 is None:
        return s2 <= s1 < e2
    if e2 is None:
        return s1 <= s2 < e1
    return s1 < e2 and s2 < e1


def _fmt(w: Window) -> str:
    start, end = w
    return f"à {start:%H:%M}" if end is None else f"de {start:%H:%M} à {end:%H:%M}"


def busy_message(
    db: Session,
    employees: Iterable[Employee],
    day: Optional[date],
    own: Optional[Window],
    exclude_id: Optional[int] = None,
) -> Optional[str]:
    """
    Le message du premier conflit trouvé pour ces employés à cet horaire,
    ou None s'ils sont tous libres. `exclude_id` : la tâche modifiée, qui
    n'entre pas en conflit avec elle-même.
    """
    employees = list(employees)
    if day is None or own is None or not employees:
        return None
    for employee in employees:
        query = (
            db.query(Activity)
            .filter(
                Activity.status == STATUS_PLANNED,
                Activity.planned_for == day,
                Activity.employees.any(Employee.id == employee.id),
            )
        )
        if exclude_id is not None:
            query = query.filter(Activity.id != exclude_id)
        for other in query.order_by(Activity.planned_time).all():
            watering = (
                db.query(IrrigationWatering).filter(IrrigationWatering.activity_id == other.id).first()
                if other.activity_type == WATERING_TASK_TYPE else None
            )
            theirs = window(other, watering)
            if theirs is None or not _overlap(own, theirs):
                continue
            parcel = db.get(Parcel, other.parcel_id)
            label = _TASK_LABELS.get(other.activity_type, other.activity_type).lower()
            return (
                f"{employee.full_name} est déjà prévu le {day:%d/%m/%Y} {_fmt(theirs)} "
                f"sur la parcelle {parcel.name if parcel else ''} ({label})."
            )
    return None




def release_employee(db: Session, employee: Employee, farm_id: Optional[int] = None) -> dict:
    """
    Retire un employé de ses tâches À RÉALISER : toutes (employé rendu
    inactif), ou celles des parcelles d'une ferme (employé retiré de cette
    ferme). Une tâche dont il était le SEUL employé est supprimée, avec ses
    tâches attachées. Les tâches déjà faites ne sont pas touchées : elles
    restent dans l'historique. Ne valide pas la transaction (commit).

    Renvoie {"removed": n, "deleted": n} : tâches gardées sans lui, tâches
    supprimées.
    """
    query = db.query(Activity).filter(
        Activity.status == STATUS_PLANNED,
        Activity.employees.any(Employee.id == employee.id),
    )
    if farm_id is not None:
        query = query.join(Parcel, Activity.parcel_id == Parcel.id).filter(Parcel.farm_id == farm_id)
    removed = deleted = 0
    # Tâches attachées d'abord : supprimer une principale supprime aussi les siennes
    for activity in query.order_by(Activity.parent_id.is_(None)).all():
        if activity in db.deleted:
            continue
        if len(activity.employees) <= 1:
            db.delete(activity)
            deleted += 1
        else:
            activity.employees.remove(employee)
            removed += 1
    db.flush()
    return {"removed": removed, "deleted": deleted}