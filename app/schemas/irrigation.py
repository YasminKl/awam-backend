"""Schémas de l'irrigation : équipements des parcelles, et arrosages."""

from datetime import date, datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.employee import EmployeeResponse

EquipmentKind = Literal["arrosoir", "tuyau", "goutte_a_goutte"]
WaterSource = Literal["puits", "reseau", "bassin", "autre"]


class EquipmentFields(BaseModel):
    """Champs communs à la création et à la modification."""

    name: Optional[str] = Field(None, min_length=1, max_length=120)
    kind: Optional[EquipmentKind] = None
    crop_ids: Optional[list[int]] = None
    water_source: Optional[WaterSource] = None
    has_meter: Optional[bool] = None
    can_capacity_l: Optional[float] = Field(None, gt=0, le=1000)
    hose_flow_l_min: Optional[float] = Field(None, gt=0, le=10000)
    hose_diameter_in: Optional[float] = Field(None, gt=0, le=20)
    hose_length_m: Optional[float] = Field(None, gt=0, le=5000)
    emitter_flow_l_h: Optional[float] = Field(None, gt=0, le=1000)
    emitters_per_plant: Optional[int] = Field(None, gt=0, le=100)
    plant_spacing_m: Optional[float] = Field(None, gt=0, le=100)
    row_spacing_m: Optional[float] = Field(None, gt=0, le=100)
    efficiency_pct: Optional[float] = Field(None, gt=0, le=100)
    active: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=2000)


class EquipmentCreate(EquipmentFields):
    parcel_id: int
    name: str = Field(..., min_length=1, max_length=120)
    kind: EquipmentKind


class EquipmentUpdate(EquipmentFields):
    """Modification : seuls les champs envoyés changent."""


class EquipmentCrop(BaseModel):
    id: int
    label: str


class EquipmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parcel_id: int
    name: str
    kind: EquipmentKind
    crop_ids: list[int]
    crops: list[EquipmentCrop] = []
    water_source: WaterSource
    has_meter: bool
    can_capacity_l: Optional[float] = None
    hose_flow_l_min: Optional[float] = None
    hose_diameter_in: Optional[float] = None
    hose_length_m: Optional[float] = None
    emitter_flow_l_h: Optional[float] = None
    emitters_per_plant: Optional[int] = None
    plant_spacing_m: Optional[float] = None
    row_spacing_m: Optional[float] = None
    efficiency_pct: Optional[float] = None
    active: bool
    notes: Optional[str] = None

    # ---- Valeurs calculées, pour l'affichage ----
    #: Goutte-à-goutte : plants par hectare, à partir des écartements
    plants_per_ha: Optional[float] = None
    #: Goutte-à-goutte : débit par plant (L/h)
    flow_per_plant_l_h: Optional[float] = None
    #: Débit total de l'équipement sur la parcelle (L/h) : tuyau, ou
    #: goutte-à-goutte si les écartements et la surface sont connus
    total_flow_l_h: Optional[float] = None


# ======================================================================
# Arrosages
# ======================================================================

WateringMethod = Literal["arrosoirs", "duree", "compteur", "volume"]


class WateringQuantity(BaseModel):
    """L'eau d'un arrosage : équipement, façon de la mesurer, quantité."""

    equipment_id: Optional[int] = None
    method: Optional[WateringMethod] = None
    #: Arrosoirs : combien
    can_count: Optional[int] = Field(None, gt=0, le=10000)
    #: Durée : heures de début et de fin, ou minutes
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    duration_min: Optional[float] = Field(None, gt=0, le=24 * 60)
    #: Compteur : volume lu (L)
    meter_volume_l: Optional[float] = Field(None, gt=0, le=10_000_000)
    #: Volume estimé à la main (L), sans équipement
    volume_l: Optional[float] = Field(None, gt=0, le=10_000_000)


class WateringCreate(WateringQuantity):
    parcel_id: int
    crop_id: Optional[int] = None
    #: À faire (planifié) ou déjà fait
    status: Literal["a_realiser", "realisee"] = "a_realiser"
    planned_for: Optional[date] = None
    planned_time: Optional[time] = None
    performed_at: Optional[datetime] = None
    team: Optional[str] = None
    employee_ids: list[int] = []


class WateringUpdate(WateringQuantity):
    """Modification : seuls les champs envoyés changent."""

    crop_id: Optional[int] = None
    planned_for: Optional[date] = None
    planned_time: Optional[time] = None
    performed_at: Optional[datetime] = None
    team: Optional[str] = None
    employee_ids: Optional[list[int]] = None


class WateringComplete(WateringQuantity):
    """« Marquer réalisé » : date et heure réelles, employés, et la quantité réelle."""

    performed_at: Optional[datetime] = None
    employee_ids: list[int]

    @field_validator("employee_ids")
    @classmethod
    def at_least_one(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("Indiquez au moins un employé qui a fait l'arrosage.")
        return v


class WateringResponse(BaseModel):
    # ---- La tâche ----
    id: int
    parcel_id: int
    status: str
    planned_for: Optional[date] = None
    planned_time: Optional[time] = None
    performed_at: Optional[datetime] = None
    crop_id: Optional[int] = None
    crop_label: Optional[str] = None
    team: Optional[str] = None
    employees: list[EmployeeResponse] = []
    # ---- L'eau ----
    equipment_id: Optional[int] = None
    equipment_name: Optional[str] = None
    equipment_kind: Optional[EquipmentKind] = None
    #: Vide pour une tâche « irrigation » créée depuis la planification, sans détails d'eau
    method: Optional[WateringMethod] = None
    can_count: Optional[int] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    duration_min: Optional[float] = None
    meter_volume_l: Optional[float] = None
    #: Volume apporté (L)
    volume_l: Optional[float] = None
    #: Lame d'eau sur la parcelle (mm = L/m²)
    depth_mm: Optional[float] = None



class WateringHistoryItem(WateringResponse):
    """Un arrosage fait, pour l'historique de la ferme."""

    parcel_name: str
    #: Surface de la parcelle (m²), pour calculer une lame d'eau sur plusieurs parcelles
    parcel_area_m2: Optional[float] = None