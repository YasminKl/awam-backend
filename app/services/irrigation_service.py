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