"""Schémas de l'irrigation : équipements des parcelles."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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