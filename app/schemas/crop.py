from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

from app.services.agronomy.species import SPECIES

CropStatus = Literal["active", "harvested", "abandoned"]


class CropBase(BaseModel):
    species: str
    variety: Optional[str] = None
    planting_date: Optional[date] = None
    expected_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    density: Optional[float] = None
    #: Arbres fruitiers : le verger produit-il déjà ?
    in_production: bool = False
    notes: Optional[str] = None

    @field_validator("species")
    @classmethod
    def species_known(cls, v: str) -> str:
        """
        L'espèce doit exister dans le référentiel : c'est elle qui porte
        les stades, les seuils et les coefficients culturaux.
        """
        v = v.strip().lower()
        if v not in SPECIES:
            raise ValueError(
                f"Espèce inconnue. Valeurs autorisées : {', '.join(sorted(SPECIES))}."
            )
        return v

    @field_validator("density")
    @classmethod
    def density_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("La densité doit être un nombre positif.")
        return v


class CropCreate(CropBase):
    parcel_id: int
    status: CropStatus = "active"


class CropUpdate(BaseModel):
    """Tous les champs facultatifs : seuls ceux envoyés sont modifiés."""

    species: Optional[str] = None
    variety: Optional[str] = None
    planting_date: Optional[date] = None
    expected_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    density: Optional[float] = None
    in_production: Optional[bool] = None
    status: Optional[CropStatus] = None
    notes: Optional[str] = None

    @field_validator("species")
    @classmethod
    def species_known(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in SPECIES:
            raise ValueError(
                f"Espèce inconnue. Valeurs autorisées : {', '.join(sorted(SPECIES))}."
            )
        return v


class CropResponse(CropBase):
    id: int
    parcel_id: int
    status: CropStatus
    #: Libellé lisible de l'espèce, pour éviter au front de le deviner
    species_label: Optional[str] = None
    #: Vrai pour olivier, amandier, caroubier : le front affiche alors
    #: la case « Verger en production »
    is_orchard: bool = False
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class VarietyOption(BaseModel):
    """Une variété proposée, avec son origine pour regrouper la liste."""

    name: str
    #: "tunisian" | "spanish" | "international"
    origin: str
    #: Libellé du groupe affiché dans le formulaire
    origin_label: str


class SpeciesOption(BaseModel):
    """Une espèce du référentiel, pour alimenter la liste déroulante."""

    code: str
    label: str
    crop_type: str
    is_orchard: bool = False
    #: Variétés proposées dans la liste ; vide = saisie libre
    varieties: list[VarietyOption] = []


class CropSuggestions(BaseModel):
    """
    Cultures lues sur la parcelle, pour pré-remplir le formulaire.

    `suggested` : espèces reconnues dans le référentiel et pas encore
                  suivies sur la parcelle (déjà cochées dans le formulaire).
    `already_active` : espèces déjà suivies : en cours, ou pérennes
                  récoltées (la plante reste en place après la récolte).
    `unknown`   : textes non reconnus, affichés pour information.
    """

    parcel_name: str
    suggested: list[str] = []
    already_active: list[str] = []
    unknown: list[str] = []


class CropObservationCreate(BaseModel):
    content: str
    observed_at: Optional[datetime] = None

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("L'observation ne peut pas être vide.")
        if len(v) > 2000:
            raise ValueError("L'observation ne doit pas dépasser 2000 caractères.")
        return v


class CropObservationResponse(BaseModel):
    id: int
    crop_id: int
    observed_at: datetime
    stage: Optional[str] = None
    stage_label: Optional[str] = None
    content: str

    model_config = {"from_attributes": True}