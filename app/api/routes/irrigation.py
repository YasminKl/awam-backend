"""
Routes de l'irrigation : équipements des parcelles.

  GET    /api/parcels/{parcel_id}/equipment   équipements d'une parcelle
  POST   /api/equipment                        ajouter un équipement
  PUT    /api/equipment/{id}                   le modifier
  DELETE /api/equipment/{id}                   le supprimer

Une parcelle peut avoir plusieurs équipements ; chacun dessert une ou
plusieurs cultures de cette parcelle. Les règles par type (paramètres
obligatoires) sont dans app/services/irrigation_service.py.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.farm import Farm
from app.models.irrigation_equipment import IrrigationEquipment
from app.models.parcel import Parcel
from app.models.utilisateur import User
from app.schemas.irrigation import EquipmentCreate, EquipmentResponse, EquipmentUpdate
from app.services import irrigation_service as irrigation
from app.services.irrigation_service import EquipmentError

router = APIRouter(tags=["irrigation"])


def _own_parcel(db: Session, parcel_id: int, user: User) -> Parcel:
    parcel = db.query(Parcel).join(Farm).filter(Parcel.id == parcel_id, Farm.user_id == user.id).first()
    if not parcel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parcelle introuvable.")
    return parcel


def _own_equipment(db: Session, equipment_id: int, user: User) -> tuple[IrrigationEquipment, Parcel]:
    row = (
        db.query(IrrigationEquipment, Parcel)
        .join(Parcel, Parcel.id == IrrigationEquipment.parcel_id)
        .join(Farm, Farm.id == Parcel.farm_id)
        .filter(IrrigationEquipment.id == equipment_id, Farm.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Équipement introuvable.")
    return row


@router.get("/parcels/{parcel_id}/equipment", response_model=list[EquipmentResponse])
async def list_equipment(parcel_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Les équipements d'une parcelle : en service d'abord, puis par nom."""
    parcel = _own_parcel(db, parcel_id, user)
    items = db.query(IrrigationEquipment).filter(IrrigationEquipment.parcel_id == parcel.id).all()
    items.sort(key=lambda e: (not e.active, e.name.lower()))
    return [irrigation.to_response(db, e, parcel) for e in items]


@router.post("/equipment", response_model=EquipmentResponse, status_code=status.HTTP_201_CREATED)
async def create_equipment(
    payload: EquipmentCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    parcel = _own_parcel(db, payload.parcel_id, user)
    values = payload.model_dump(exclude_none=True)
    values.setdefault("crop_ids", [])
    values.setdefault("water_source", "puits")
    values.setdefault("has_meter", False)
    values.setdefault("active", True)
    try:
        values = irrigation.check_and_clean(values)
        values["crop_ids"] = irrigation.check_crops(db, parcel.id, values["crop_ids"])
    except EquipmentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    values["name"] = values["name"].strip()
    equipment = IrrigationEquipment(**values)
    db.add(equipment)
    db.commit()
    db.refresh(equipment)
    return irrigation.to_response(db, equipment, parcel)


@router.put("/equipment/{equipment_id}", response_model=EquipmentResponse)
async def update_equipment(
    equipment_id: int, payload: EquipmentUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    equipment, parcel = _own_equipment(db, equipment_id, user)
    current = {c.name: getattr(equipment, c.name) for c in equipment.__table__.columns}
    changes = payload.model_dump(exclude_unset=True)
    values = {**current, **changes}
    try:
        values = irrigation.check_and_clean(values)
        values["crop_ids"] = irrigation.check_crops(db, parcel.id, values.get("crop_ids") or [])
    except EquipmentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    for field, value in values.items():
        if field in ("id", "parcel_id", "created_at", "updated_at"):
            continue
        setattr(equipment, field, value.strip() if field == "name" and isinstance(value, str) else value)
    db.commit()
    db.refresh(equipment)
    return irrigation.to_response(db, equipment, parcel)


@router.delete("/equipment/{equipment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_equipment(equipment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    equipment, _parcel = _own_equipment(db, equipment_id, user)
    db.delete(equipment)
    db.commit()


# ======================================================================
# Arrosages
#
#   GET  /api/parcels/{parcel_id}/waterings?days=60   à faire, et faits récemment
#   POST /api/waterings                                 planifier ou enregistrer
#   PUT  /api/waterings/{id}                            modifier
#   POST /api/waterings/{id}/complete                   « Marquer réalisé »
#   La suppression passe par DELETE /api/activities/{id} (l'eau part avec la tâche).
# ======================================================================

from datetime import datetime, timedelta, timezone  # noqa: E402

from fastapi import Query  # noqa: E402
from sqlalchemy import or_  # noqa: E402

from app.api.routes.activities import (  # noqa: E402
    _apply_status,
    _check_crop,
    _get_owned_activity,
    _owned_employees,
    _require_people_if_done,
    _team_code,
)
from app.models.activity import DONE_STATUSES, STATUS_DONE, STATUS_PLANNED, Activity  # noqa: E402
from app.models.irrigation_watering import WATERING_TASK_TYPE, IrrigationWatering  # noqa: E402
from app.schemas.irrigation import (  # noqa: E402
    WateringComplete,
    WateringCreate,
    WateringHistoryItem,
    WateringResponse,
    WateringUpdate,
)
from app.services.irrigation_service import WateringError  # noqa: E402

QUANTITY_FIELDS = ("equipment_id", "method", "can_count", "start_time", "end_time", "duration_min", "meter_volume_l", "volume_l")


def _own_watering_task(db: Session, activity_id: int, user: User) -> tuple[Activity, Parcel]:
    activity = _get_owned_activity(activity_id, user, db)
    if activity.activity_type != WATERING_TASK_TYPE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Cette tâche n'est pas un arrosage.")
    return activity, db.get(Parcel, activity.parcel_id)


def _save_quantity(db: Session, activity: Activity, parcel: Parcel, changes: dict, required: bool) -> IrrigationWatering:
    """
    Enregistre l'eau d'un arrosage : l'équipement, la façon de la mesurer,
    et le volume calculé. `required` : la quantité doit être connue (arrosage fait).
    """
    watering = db.query(IrrigationWatering).filter(IrrigationWatering.activity_id == activity.id).first()
    current = {f: getattr(watering, f) for f in QUANTITY_FIELDS} if watering else {f: None for f in QUANTITY_FIELDS}
    values = {**current, **{k: v for k, v in changes.items() if k in QUANTITY_FIELDS}}
    try:
        equipment = irrigation.watering_equipment(db, parcel.id, values.get("equipment_id"))
        values["method"] = values.get("method") or irrigation.default_method(equipment)
        values = irrigation.clean_quantity(values)
        values["volume_l"] = irrigation.compute_volume(db, values, equipment, parcel, required)
    except WateringError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    if watering is None:
        watering = IrrigationWatering(activity_id=activity.id)
        db.add(watering)
    for field, value in values.items():
        setattr(watering, field, value)
    watering.equipment = equipment
    return watering


def _response(db: Session, activity: Activity, parcel: Parcel) -> dict:
    watering = db.query(IrrigationWatering).filter(IrrigationWatering.activity_id == activity.id).first()
    if watering is None:
        # Tâche « irrigation » créée depuis la planification, sans détails d'eau
        watering = IrrigationWatering(activity_id=activity.id, method=None)
    return irrigation.watering_response(db, activity, watering, parcel)


@router.get("/parcels/{parcel_id}/waterings", response_model=list[WateringResponse])
async def list_waterings(
    parcel_id: int,
    days: int = Query(60, ge=1, le=3650, description="Arrosages faits sur les N derniers jours"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Les arrosages à faire (tous), puis ceux faits sur les N derniers jours, du plus récent au plus ancien."""
    parcel = _own_parcel(db, parcel_id, user)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    tasks = (
        db.query(Activity)
        .filter(
            Activity.parcel_id == parcel.id,
            Activity.activity_type == WATERING_TASK_TYPE,
            or_(Activity.status == STATUS_PLANNED, Activity.performed_at >= since),
        )
        .all()
    )
    planned = sorted(
        (a for a in tasks if a.status == STATUS_PLANNED),
        key=lambda a: (a.planned_for, a.planned_time is None, a.planned_time or datetime.min.time()),
    )
    done = sorted((a for a in tasks if a.status != STATUS_PLANNED), key=lambda a: a.performed_at, reverse=True)
    return [_response(db, a, parcel) for a in planned + done]


@router.post("/waterings", response_model=WateringResponse, status_code=status.HTTP_201_CREATED)
async def create_watering(payload: WateringCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Planifier un arrosage, ou enregistrer un arrosage déjà fait (avec sa quantité)."""
    parcel = _own_parcel(db, payload.parcel_id, user)
    _check_crop(payload.crop_id, parcel.id, db)
    activity = Activity(
        parcel_id=parcel.id,
        activity_type=WATERING_TASK_TYPE,
        status=payload.status,
        planned_for=payload.planned_for,
        planned_time=payload.planned_time,
        performed_at=payload.performed_at,
        crop_id=payload.crop_id,
        team=_team_code(payload.team, user, db),
    )
    activity.employees = _owned_employees(payload.employee_ids, user, db, require_active=payload.status == STATUS_PLANNED)
    _apply_status(activity)
    _require_people_if_done(activity)
    db.add(activity)
    db.flush()
    _save_quantity(db, activity, parcel, payload.model_dump(exclude_unset=True), required=activity.status in DONE_STATUSES)
    db.commit()
    db.refresh(activity)
    return _response(db, activity, parcel)


@router.put("/waterings/{activity_id}", response_model=WateringResponse)
async def update_watering(
    activity_id: int, payload: WateringUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    activity, parcel = _own_watering_task(db, activity_id, user)
    changes = payload.model_dump(exclude_unset=True)
    for field in ("crop_id", "planned_for", "planned_time", "performed_at"):
        if field in changes:
            setattr(activity, field, changes[field])
    if "team" in changes:
        activity.team = _team_code(changes["team"], user, db)
    _check_crop(activity.crop_id, parcel.id, db)
    if payload.employee_ids is not None:
        activity.employees = _owned_employees(
            payload.employee_ids, user, db, require_active=activity.status == STATUS_PLANNED
        )
    _apply_status(activity)
    _require_people_if_done(activity)
    _save_quantity(db, activity, parcel, changes, required=activity.status in DONE_STATUSES)
    db.commit()
    db.refresh(activity)
    return _response(db, activity, parcel)


@router.post("/waterings/{activity_id}/complete", response_model=WateringResponse)
async def complete_watering(
    activity_id: int, payload: WateringComplete, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """« Marquer réalisé » : date et heure réelles, employés, et la quantité RÉELLE d'eau."""
    activity, parcel = _own_watering_task(db, activity_id, user)
    activity.status = STATUS_DONE
    activity.performed_at = payload.performed_at or datetime.now(timezone.utc)
    activity.employees = _owned_employees(payload.employee_ids, user, db, require_active=False)
    _save_quantity(db, activity, parcel, payload.model_dump(exclude_unset=True), required=True)
    db.commit()
    db.refresh(activity)
    return _response(db, activity, parcel)

   

@router.get("/farms/{farm_id}/irrigation-history", response_model=list[WateringHistoryItem])
async def irrigation_history(
    farm_id: int,
    days: int = Query(90, ge=1, le=3650, description="Arrosages faits sur les N derniers jours"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Historique des arrosages FAITS de toute la ferme, du plus récent au plus
    ancien, avec la parcelle de chacun : la page filtre ensuite par
    parcelle, équipement et culture, et calcule ses chiffres clés.
    """
    farm = db.query(Farm).filter(Farm.id == farm_id, Farm.user_id == user.id).first()
    if not farm:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ferme introuvable.")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(Activity, Parcel)
        .join(Parcel, Parcel.id == Activity.parcel_id)
        .filter(
            Parcel.farm_id == farm.id,
            Activity.activity_type == WATERING_TASK_TYPE,
            Activity.status.in_(DONE_STATUSES),
            Activity.performed_at >= since,
        )
        .order_by(Activity.performed_at.desc())
        .all()
    )
    out = []
    for activity, parcel in rows:
        item = _response(db, activity, parcel)
        area_ha = irrigation.parcel_area_ha(db, parcel)
        item["parcel_name"] = parcel.name
        item["parcel_area_m2"] = round(area_ha * 10000, 1) if area_ha else None
        out.append(item)
    return out

    

# ======================================================================
# Suivi : les cultures ont-elles reçu assez d'eau ?
# ======================================================================

from app.services import water_balance_service  # noqa: E402


@router.get("/parcels/{parcel_id}/water-balance")
async def water_balance(
    parcel_id: int,
    weeks: int = Query(8, ge=1, le=52, description="Nombre de semaines du tableau"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Pour chaque culture en cours de la parcelle : le bilan d'eau des
    7 derniers jours (besoin, pluie mesurée, arrosages, besoin couvert) et
    celui des dernières semaines. Méthode : app/services/water_balance_service.py.
    """
    parcel = _own_parcel(db, parcel_id, user)
    return water_balance_service.parcel_balance(db, parcel, weeks)