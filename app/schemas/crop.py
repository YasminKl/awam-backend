from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, field_validator, model_validator

from app.services.agronomy.species import SPECIES

#: active    : en place, dans son cycle
#: harvested : annuelle récoltée, cycle terminé (une pérenne reste active)
#: abandoned : culture qui a échoué avant terme
#: ended     : pérenne arrivée en fin de vie et arrachée, avec end_date
CropStatus = Literal["active", "harvested", "abandoned", "ended"]

#: Statuts qui clôturent une culture : plus de récolte, plus de diagnostic,
#: et l'espèce est libérée pour une nouvelle plantation sur la parcelle
CLOSED_STATUSES = ("abandoned", "ended")
#: Unité de la production réelle
YieldUnit = Literal["kg", "t"]


def check_crop_dates(
    planting_date: Optional[date],
    expected_harvest_date: Optional[date],
    actual_harvest_date: Optional[date],
    today: Optional[date] = None,
) -> None:
    """
    Cohérence des dates d'une culture. Lève ValueError avec un message
    lisible par l'utilisateur. Utilisée à la création (schéma) et à la
    modification (route, sur l'état final de la culture).
    """
    today = today or date.today()
    if planting_date and planting_date > today:
        raise ValueError("La date de plantation ne peut pas être dans le futur.")
    if planting_date and expected_harvest_date and expected_harvest_date < planting_date:
        raise ValueError("La récolte prévue ne peut pas précéder la plantation.")
    if actual_harvest_date:
        if actual_harvest_date > today:
            raise ValueError("La récolte réelle ne peut pas être dans le futur.")
        if planting_date and actual_harvest_date < planting_date:
            raise ValueError("La récolte réelle ne peut pas précéder la plantation.")


def check_harvest(
    status: Optional[str],
    actual_harvest_date: Optional[date],
    actual_yield: Optional[float],
    yield_unit: Optional[str],
) -> None:
    """
    Une culture récoltée doit porter la date de récolte et la production
    réelles, saisies par l'utilisateur pour la traçabilité.
    """
    if status != "harvested":
        return
    if actual_harvest_date is None:
        raise ValueError("Indiquez la date de récolte.")
    if actual_yield is None:
        raise ValueError("Indiquez la production récoltée.")
    if yield_unit is None:
        raise ValueError("Indiquez l'unité de la production (kg ou t).")


def check_end(
    status: Optional[str],
    planting_date: Optional[date],
    end_date: Optional[date],
    today: Optional[date] = None,
) -> None:
    """
    Une culture « Terminée » porte sa date de fin (arrachage) : pas dans le
    futur, et pas avant la plantation.
    """
    if status != "ended":
        return
    today = today or date.today()
    if end_date is None:
        raise ValueError("Indiquez la date de fin de la culture.")
    if end_date > today:
        raise ValueError("La date de fin ne peut pas être dans le futur.")
    if planting_date and end_date < planting_date:
        raise ValueError("La date de fin ne peut pas précéder la plantation.")


class CropBase(BaseModel):
    species: str
    variety: Optional[str] = None
    planting_date: Optional[date] = None
    expected_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    #: Production réelle, quand la culture est récoltée
    actual_yield: Optional[float] = None
    yield_unit: Optional[YieldUnit] = None
    density: Optional[float] = None
    #: Arbres fruitiers : le verger produit-il déjà ?
    in_production: bool = False
    notes: Optional[str] = None
    #: Date de fin, pour une culture « Terminée »
    end_date: Optional[date] = None

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

    @field_validator("actual_yield")
    @classmethod
    def yield_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("La production doit être un nombre positif.")
        return v


class CropCreate(CropBase):
    parcel_id: int
    status: CropStatus = "active"
    #: Obligatoire à la création : elle donne l'âge (donc les seuils de
    #: l'agronome), le stade des cultures annuelles, la récolte prévue et
    #: le point de départ de l'historique. Une date approximative suffit.
    planting_date: date

    @model_validator(mode="after")
    def dates_coherent(self) -> "CropCreate":
        check_crop_dates(self.planting_date, self.expected_harvest_date, self.actual_harvest_date)
        check_harvest(self.status, self.actual_harvest_date, self.actual_yield, self.yield_unit)
        check_end(self.status, self.planting_date, self.end_date)
        return self


class CropUpdate(BaseModel):
    """Tous les champs facultatifs : seuls ceux envoyés sont modifiés."""

    species: Optional[str] = None
    variety: Optional[str] = None
    planting_date: Optional[date] = None
    expected_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    actual_yield: Optional[float] = None
    yield_unit: Optional[YieldUnit] = None
    density: Optional[float] = None
    in_production: Optional[bool] = None
    status: Optional[CropStatus] = None
    notes: Optional[str] = None
    end_date: Optional[date] = None

    @model_validator(mode="after")
    def planting_not_erased(self) -> "CropUpdate":
        """La date de plantation peut être corrigée, mais pas effacée."""
        if "planting_date" in self.model_fields_set and self.planting_date is None:
            raise ValueError("La date de plantation est obligatoire.")
        return self

    @field_validator("actual_yield")
    @classmethod
    def yield_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("La production doit être un nombre positif.")
        return v

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
    #: Date de fin, pour une culture « Terminée »
    end_date: Optional[date] = None
    #: Libellé lisible de l'espèce, pour éviter au front de le deviner
    species_label: Optional[str] = None
    #: Vrai pour olivier, amandier, caroubier : le front affiche alors
    #: la case « Verger en production »
    is_orchard: bool = False
    #: Vrai pour le basilic : une annuelle récoltée termine son cycle. Une
    #: pérenne récoltée en ouvre un nouveau (le front affiche « Récoltée »
    #: le jour de la récolte seulement)
    is_annual: bool = False
    #: Âge de la plantation en années (depuis la date de plantation)
    age_years: Optional[float] = None
    #: Production optimale de l'espèce, ex. « de 3 à 6 ans »
    optimal_production: Optional[str] = None
    #: Message selon l'âge : niveau "info" ou "warning", et texte
    lifespan_level: Optional[str] = None
    lifespan_note: Optional[str] = None
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


class ExpectedHarvestPreview(BaseModel):
    """
    Récolte prévue calculée pour une espèce et une date de plantation, avant
    l'enregistrement : le formulaire l'affiche dès la saisie.
    """

    expected_harvest_date: Optional[date] = None
    #: Pourquoi aucune date, le cas échéant
    reason: Optional[str] = None


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