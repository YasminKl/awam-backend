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