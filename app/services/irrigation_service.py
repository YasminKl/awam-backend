"""
Irrigation : règles et calculs des équipements.

Chaque type d'équipement a ses paramètres obligatoires ; ceux des autres
types sont vidés, pour qu'un équipement ne garde jamais de valeurs sans
rapport avec lui (ex. un débit de goutteur sur un arrosoir).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.crop import Crop
from app.models.irrigation_equipment import KIND_CAN, KIND_DRIP, KIND_HOSE, IrrigationEquipment
from app.models.parcel import Parcel
from app.services.agronomy.species import SPECIES

#: Paramètres propres à chaque type d'équipement
KIND_FIELDS: dict[str, tuple[str, ...]] = {
    KIND_CAN: ("can_capacity_l",),
    KIND_HOSE: ("hose_flow_l_min", "hose_diameter_in", "hose_length_m"),
    KIND_DRIP: ("emitter_flow_l_h", "emitters_per_plant", "plant_spacing_m", "row_spacing_m"),
}
ALL_KIND_FIELDS = tuple(f for fields in KIND_FIELDS.values() for f in fields)


class EquipmentError(ValueError):
    """Un équipement incomplet ou incohérent : le message est affichable."""


def check_and_clean(values: dict) -> dict:
    """
    Vérifie les paramètres obligatoires du type, et vide ceux des autres
    types. Lève EquipmentError avec un message clair.
    """
    kind = values["kind"]
    for field in ALL_KIND_FIELDS:
        if field not in KIND_FIELDS[kind]:
            values[field] = None
    if kind == KIND_CAN and not values.get("can_capacity_l"):
        raise EquipmentError("Indiquez la capacité de l'arrosoir, en litres.")
    if kind == KIND_HOSE and not values.get("hose_flow_l_min") and not values.get("has_meter"):
        raise EquipmentError(
            "Indiquez le débit du tuyau, en litres par minute, ou cochez « compteur » "
            "si le volume est lu sur un compteur."
        )
    if kind == KIND_DRIP and (not values.get("emitter_flow_l_h") or not values.get("emitters_per_plant")):
        raise EquipmentError("Indiquez le débit d'un goutteur et le nombre de goutteurs par plant.")
    return values


def check_crops(db: Session, parcel_id: int, crop_ids: list[int]) -> list[int]:
    """Les cultures desservies doivent être celles de la parcelle ; doublons retirés."""
    wanted = sorted(set(crop_ids))
    if not wanted:
        return []
    found = {c.id for c in db.query(Crop.id).filter(Crop.parcel_id == parcel_id, Crop.id.in_(wanted)).all()}
    if missing := [c for c in wanted if c not in found]:
        raise EquipmentError(f"Cultures introuvables sur cette parcelle : {', '.join(map(str, missing))}.")
    return wanted


def parcel_area_ha(db: Session, parcel: Parcel) -> float | None:
    """Surface de la parcelle : la valeur saisie, sinon celle de son contour."""
    if parcel.area_ha:
        return parcel.area_ha
    return db.execute(
        text("SELECT ST_Area(geom::geography) / 10000.0 FROM parcel WHERE id = :id"), {"id": parcel.id}
    ).scalar()


def computed(equipment: IrrigationEquipment, area_ha: float | None) -> dict:
    """Valeurs calculées pour l'affichage : plants par hectare, débits."""
    out = {"plants_per_ha": None, "flow_per_plant_l_h": None, "total_flow_l_h": None}
    if equipment.kind == KIND_HOSE and equipment.hose_flow_l_min:
        out["total_flow_l_h"] = round(equipment.hose_flow_l_min * 60, 1)
    if equipment.kind == KIND_DRIP:
        if equipment.emitter_flow_l_h and equipment.emitters_per_plant:
            out["flow_per_plant_l_h"] = round(equipment.emitter_flow_l_h * equipment.emitters_per_plant, 2)
        if equipment.plant_spacing_m and equipment.row_spacing_m:
            out["plants_per_ha"] = round(10000 / (equipment.plant_spacing_m * equipment.row_spacing_m), 1)
        if out["flow_per_plant_l_h"] and out["plants_per_ha"] and area_ha:
            out["total_flow_l_h"] = round(out["flow_per_plant_l_h"] * out["plants_per_ha"] * area_ha, 1)
    return out

def active_crops(db: Session, parcel_id: int) -> list[Crop]:
    return db.query(Crop).filter(Crop.parcel_id == parcel_id, Crop.status == "active").all()


def crop_area_m2(db: Session, parcel: Parcel, crop: Crop | None) -> float | None:
    """
    Surface sur laquelle compter l'eau d'une culture, pour le Suivi (m²) :
      - si la parcelle n'a qu'une culture en cours, la surface de la parcelle ;
      - sinon None : la culture partage la parcelle et AWAM ne connaît pas
        encore sa surface, les calculs ne seraient pas fiables.
    """
    crops = active_crops(db, parcel.id)
    if crop is None or len(crops) <= 1:
        area_ha = parcel_area_ha(db, parcel)
        return round(area_ha * 10000, 1) if area_ha else None
    return None

    
def crop_labels(db: Session, crop_ids: list[int]) -> list[dict]:
    """Nom lisible des cultures desservies : l'espèce, et sa variété si elle est connue."""
    if not crop_ids:
        return []
    crops = db.query(Crop).filter(Crop.id.in_(crop_ids)).all()
    out = []
    for c in sorted(crops, key=lambda c: c.id):
        spec = SPECIES.get(c.species)
        label = spec.label if spec else c.species
        variety = getattr(c, "variety", None)
        out.append({"id": c.id, "label": f"{label} · {variety}" if variety else label})
    return out


def to_response(db: Session, equipment: IrrigationEquipment, parcel: Parcel) -> dict:
    data = {c.name: getattr(equipment, c.name) for c in equipment.__table__.columns}
    data["crop_ids"] = list(equipment.crop_ids or [])
    data["crops"] = crop_labels(db, data["crop_ids"])
    data.update(computed(equipment, parcel_area_ha(db, parcel)))
    return data



# ======================================================================
# Arrosages : volume d'eau
# ======================================================================

from datetime import datetime as _dt  # noqa: E402

from app.models.irrigation_watering import (  # noqa: E402
    METHOD_CANS,
    METHOD_DURATION,
    METHOD_METER,
    METHOD_VOLUME,
)


class WateringError(ValueError):
    """Un arrosage incomplet ou incohérent : le message est affichable."""


def watering_equipment(db: Session, parcel_id: int, equipment_id: int | None) -> IrrigationEquipment | None:
    """L'équipement d'un arrosage : il doit être sur la même parcelle."""
    if equipment_id is None:
        return None
    equipment = db.get(IrrigationEquipment, equipment_id)
    if equipment is None or equipment.parcel_id != parcel_id:
        raise WateringError("Cet équipement n'est pas sur cette parcelle.")
    return equipment


def default_method(equipment: IrrigationEquipment | None) -> str:
    """Façon de mesurer l'eau la plus naturelle pour l'équipement."""
    if equipment is None:
        return METHOD_VOLUME
    if equipment.kind == KIND_CAN:
        return METHOD_CANS
    return METHOD_METER if equipment.has_meter else METHOD_DURATION


def duration_minutes(values: dict) -> float | None:
    """Durée en minutes : saisie directement, ou entre l'heure de début et de fin."""
    if values.get("duration_min"):
        return float(values["duration_min"])
    start, end = values.get("start_time"), values.get("end_time")
    if start and end:
        minutes = (_dt.combine(_dt.min, end) - _dt.combine(_dt.min, start)).total_seconds() / 60
        if minutes <= 0:
            raise WateringError("L'heure de fin doit être après l'heure de début.")
        return minutes
    return None


def compute_volume(
    db: Session, values: dict, equipment: IrrigationEquipment | None, parcel: Parcel, required: bool
) -> float | None:
    """
    Volume d'eau (L) selon la façon de le mesurer. `required` : vrai pour un
    arrosage FAIT, dont la quantité doit être connue ; un arrosage prévu
    peut ne pas l'avoir encore.
    """
    method = values["method"]
    missing = None
    if method == METHOD_CANS:
        if equipment is None or equipment.kind != KIND_CAN:
            raise WateringError("Choisissez l'arrosoir utilisé, pour connaître sa capacité.")
        if values.get("can_count"):
            return round(values["can_count"] * equipment.can_capacity_l, 1)
        missing = "Indiquez le nombre d'arrosoirs."
    elif method == METHOD_DURATION:
        if equipment is None or equipment.kind == KIND_CAN:
            raise WateringError("Choisissez le tuyau ou le goutte-à-goutte utilisé, pour connaître son débit.")
        minutes = duration_minutes(values)
        flow_l_h = computed(equipment, parcel_area_ha(db, parcel))["total_flow_l_h"]
        if minutes and not flow_l_h:
            raise WateringError(
                "Le débit de cet équipement n'est pas connu : complétez-le dans l'onglet Équipements, "
                "ou indiquez le volume lu au compteur."
            )
        if minutes:
            return round(minutes * flow_l_h / 60, 1)
        missing = "Indiquez l'heure de début et de fin, ou la durée en minutes."
    elif method == METHOD_METER:
        if values.get("meter_volume_l"):
            return round(values["meter_volume_l"], 1)
        missing = "Indiquez le volume lu au compteur, en litres."
    elif method == METHOD_VOLUME:
        if values.get("volume_l"):
            return round(values["volume_l"], 1)
        missing = "Indiquez le volume d'eau apporté, en litres."
    if required:
        raise WateringError(missing)
    return None


def clean_quantity(values: dict) -> dict:
    """Ne garde que les champs utiles à la façon de mesurer choisie."""
    keep = {
        METHOD_CANS: ("can_count",),
        METHOD_DURATION: ("start_time", "end_time", "duration_min"),
        METHOD_METER: ("meter_volume_l",),
        METHOD_VOLUME: (),
    }[values["method"]]
    for field in ("can_count", "start_time", "end_time", "duration_min", "meter_volume_l"):
        if field not in keep:
            values[field] = None
    if values["method"] == METHOD_DURATION and values.get("start_time") and values.get("end_time"):
        values["duration_min"] = round(duration_minutes({**values, "duration_min": None}), 1)
    return values


def watering_response(db: Session, activity, watering, parcel: Parcel) -> dict:
    """Une tâche d'arrosage et son eau, avec la lame d'eau sur la parcelle."""
    area_ha = parcel_area_ha(db, parcel)
    equipment = watering.equipment
    crop_label = None
    if activity.crop_id:
        labels = crop_labels(db, [activity.crop_id])
        crop_label = labels[0]["label"] if labels else None
    volume = watering.volume_l
    return {
        "id": activity.id,
        "parcel_id": activity.parcel_id,
        "status": activity.status,
        "planned_for": activity.planned_for,
        "planned_time": activity.planned_time,
        "performed_at": activity.performed_at,
        "crop_id": activity.crop_id,
        "crop_label": crop_label,
        "team": activity.team,
        "employees": activity.employees,
        "equipment_id": watering.equipment_id,
        "equipment_name": equipment.name if equipment else None,
        "equipment_kind": equipment.kind if equipment else None,
        "method": watering.method,
        "can_count": watering.can_count,
        "start_time": watering.start_time,
        "end_time": watering.end_time,
        "duration_min": watering.duration_min,
        "meter_volume_l": watering.meter_volume_l,
        "volume_l": volume,
        # 1 mm = 1 L/m²
        "depth_mm": round(volume / (area_ha * 10000), 2) if volume is not None and area_ha else None,
    }