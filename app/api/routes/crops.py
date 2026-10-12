"""
Routes des cultures : CRUD, observations de terrain, référentiel.

Une culture est rattachée à une parcelle et porte une espèce du
référentiel agronomique. C'est elle qui donne le contexte nécessaire au
diagnostic : sans culture déclarée, aucun stade ni seuil ne s'applique.
"""

from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.activity import Activity
from app.models.crop import Crop
from app.models.crop_observation import CropObservation
from app.models.farm import Farm
from app.models.parcel import Parcel
from app.models.utilisateur import User
from app.schemas.crop import (
    CropCreate,
    CropSuggestions,
    CropObservationCreate,
    CropObservationResponse,
    CropResponse,
    CropUpdate,
    SpeciesOption,
    VarietyOption,
)
from app.services.agronomy.calendar import expected_harvest_date, get_stage_at
from app.services.agronomy.species import (
    ORIGIN_LABELS,
    SPECIES,
    get_varieties,
    is_annual,
    is_orchard,
    list_species,
    parse_cultures,
    stage_label,
)

router = APIRouter(tags=["crops"])

#: Types d'activité qui marquent une récolte
HARVEST_TYPES = {"recolte", "récolte", "harvest", "coupe"}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _get_owned_parcel(parcel_id: int, user: User, db: Session) -> Parcel:
    parcel = (
        db.query(Parcel)
        .join(Farm)
        .filter(Parcel.id == parcel_id, Farm.user_id == user.id)
        .first()
    )
    if not parcel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parcelle introuvable.")
    return parcel


def _get_owned_crop(crop_id: int, user: User, db: Session) -> Crop:
    crop = (
        db.query(Crop)
        .join(Parcel, Crop.parcel_id == Parcel.id)
        .join(Farm, Parcel.farm_id == Farm.id)
        .filter(Crop.id == crop_id, Farm.user_id == user.id)
        .first()
    )
    if not crop:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Culture introuvable.")
    return crop


def _sync_harvest_date(crop: Crop, db: Session) -> None:
    """
    Déduit la date de récolte réelle de la dernière activité de récolte.

    Évite la double saisie : l'utilisateur enregistre la récolte comme
    une intervention, la date remonte ici automatiquement. Une valeur
    saisie à la main n'est jamais écrasée.
    """
    if crop.actual_harvest_date is not None:
        return

    last = (
        db.query(Activity)
        .filter(Activity.crop_id == crop.id)
        .order_by(Activity.performed_at.desc())
        .all()
    )
    for activity in last:
        if (activity.activity_type or "").strip().lower() in HARVEST_TYPES:
            crop.actual_harvest_date = activity.performed_at.date()
            # Une récolte réelle prouve que le verger produit
            crop.in_production = True
            if crop.status == "active":
                crop.status = "harvested"
            return


def _compute_expected_harvest(crop: Crop) -> None:
    """
    Remplit la date de récolte prévue depuis le calendrier agronomique.

    Ne s'applique qu'aux cultures en cours, sans date saisie à la main.
    Pour un arbre fruitier pas encore en production, la date reste vide :
    le système ne devine pas l'année de première récolte.
    """
    if crop.status != "active":
        return
    crop.expected_harvest_date = expected_harvest_date(
        crop.species,
        crop.planting_date,
        orchard=is_orchard(crop.species),
        in_production=bool(crop.in_production),
    )


def _to_response(crop: Crop) -> CropResponse:
    spec = SPECIES.get(crop.species)
    return CropResponse(
        id=crop.id,
        parcel_id=crop.parcel_id,
        species=crop.species,
        species_label=spec.label if spec else crop.species,
        variety=crop.variety,
        planting_date=crop.planting_date,
        expected_harvest_date=crop.expected_harvest_date,
        actual_harvest_date=crop.actual_harvest_date,
        density=crop.density,
        in_production=bool(crop.in_production),
        is_orchard=is_orchard(crop.species),
        status=crop.status,
        notes=crop.notes,
        created_at=crop.created_at,
    )


# ----------------------------------------------------------------------
# Référentiel
# ----------------------------------------------------------------------


@router.get("/crops/species", response_model=List[SpeciesOption])
def list_available_species():
    """
    Espèces du référentiel, pour la liste déroulante du formulaire.
    Seules ces espèces ont des stades, des seuils et des Kc.
    """
    return [
        SpeciesOption(
            code=s.code, label=s.label, crop_type=s.crop_type.value,
            is_orchard=s.orchard,
            varieties=[
                VarietyOption(
                    name=v.name,
                    origin=v.origin.value,
                    origin_label=ORIGIN_LABELS[v.origin],
                )
                for v in get_varieties(s.code)
            ],
        )
        for s in list_species()
    ]


@router.get("/parcels/{parcel_id}/crop-suggestions", response_model=CropSuggestions)
def get_crop_suggestions(
    parcel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Espèces à pré-cocher dans le formulaire, lues sur la parcelle.

    Une parcelle peut porter plusieurs cultures : le champ culture est
    découpé et chaque élément retrouvé dans le référentiel.

    Une espèce n'est pas reproposée si elle est déjà suivie :
      - culture en cours ;
      - ou pérenne récoltée : la plante reste en place après la récolte.
    Une annuelle récoltée (basilic) est reproposée : son cycle est fini.
    Une culture abandonnée libère toujours l'espèce.
    """
    parcel = _get_owned_parcel(parcel_id, user, db)
    found, unknown = parse_cultures(parcel.culture_type)

    active = {
        c.species
        for c in db.query(Crop).filter(Crop.parcel_id == parcel_id).all()
        if c.status == "active"
        or (c.status == "harvested" and not is_annual(c.species))
    }

    return CropSuggestions(
        parcel_name=parcel.name,
        suggested=[code for code in found if code not in active],
        already_active=sorted(active),
        unknown=unknown,
    )


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------


@router.get("/parcels/{parcel_id}/crops", response_model=List[CropResponse])
def list_parcel_crops(
    parcel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cultures de la parcelle, de la plus récente à la plus ancienne."""
    _get_owned_parcel(parcel_id, user, db)
    crops = (
        db.query(Crop)
        .filter(Crop.parcel_id == parcel_id)
        .order_by(Crop.planting_date.desc().nullslast(), Crop.id.desc())
        .all()
    )
    for crop in crops:
        _sync_harvest_date(crop, db)
    db.commit()
    return [_to_response(c) for c in crops]


@router.get("/crops/{crop_id}", response_model=CropResponse)
def get_crop(
    crop_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    crop = _get_owned_crop(crop_id, user, db)
    _sync_harvest_date(crop, db)
    db.commit()
    return _to_response(crop)


@router.post("/crops", response_model=CropResponse, status_code=status.HTTP_201_CREATED)
def create_crop(
    payload: CropCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_parcel(payload.parcel_id, user, db)

    data = payload.model_dump()
    manual_expected = data.get("expected_harvest_date") is not None

    crop = Crop(**data)
    if not is_orchard(crop.species):
        crop.in_production = False
    if not manual_expected:
        _compute_expected_harvest(crop)

    db.add(crop)
    db.commit()
    db.refresh(crop)
    return _to_response(crop)


@router.put("/crops/{crop_id}", response_model=CropResponse)
def update_crop(
    crop_id: int,
    payload: CropUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    crop = _get_owned_crop(crop_id, user, db)

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(crop, field, value)

    # Une fois récoltée, seule la date réelle compte : la date prévue
    # n'est plus recalculée ni modifiable.
    if crop.status == "active" and "expected_harvest_date" not in changes:
        if {"species", "planting_date", "in_production", "status"} & changes.keys():
            _compute_expected_harvest(crop)

    if not is_orchard(crop.species):
        crop.in_production = False

    db.commit()
    db.refresh(crop)
    return _to_response(crop)


@router.delete("/crops/{crop_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_crop(
    crop_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    crop = _get_owned_crop(crop_id, user, db)
    db.delete(crop)
    db.commit()
    return None


# ----------------------------------------------------------------------
# Observations de terrain
# ----------------------------------------------------------------------


@router.get("/crops/{crop_id}/observations", response_model=List[CropObservationResponse])
def list_observations(
    crop_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_crop(crop_id, user, db)
    rows = (
        db.query(CropObservation)
        .filter(CropObservation.crop_id == crop_id)
        .order_by(CropObservation.observed_at.desc())
        .all()
    )
    return [
        CropObservationResponse(
            id=o.id, crop_id=o.crop_id, observed_at=o.observed_at,
            stage=o.stage, stage_label=stage_label(o.stage) if o.stage else None,
            content=o.content,
        )
        for o in rows
    ]


@router.post(
    "/crops/{crop_id}/observations",
    response_model=CropObservationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_observation(
    crop_id: int,
    payload: CropObservationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Enregistre une observation terrain. Le stade est figé au moment de la
    saisie : il garde le contexte même si le calendrier évolue ensuite.
    """
    crop = _get_owned_crop(crop_id, user, db)

    observed_at = payload.observed_at or datetime.now(timezone.utc)
    period = get_stage_at(crop.species, observed_at.date(), crop.planting_date)

    observation = CropObservation(
        crop_id=crop_id,
        observed_at=observed_at,
        stage=period.stage if period else None,
        content=payload.content,
    )
    db.add(observation)
    db.commit()
    db.refresh(observation)

    return CropObservationResponse(
        id=observation.id, crop_id=observation.crop_id,
        observed_at=observation.observed_at, stage=observation.stage,
        stage_label=stage_label(observation.stage) if observation.stage else None,
        content=observation.content,
    )


@router.delete("/observations/{observation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_observation(
    observation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    observation = (
        db.query(CropObservation)
        .join(Crop, CropObservation.crop_id == Crop.id)
        .join(Parcel, Crop.parcel_id == Parcel.id)
        .join(Farm, Parcel.farm_id == Farm.id)
        .filter(CropObservation.id == observation_id, Farm.user_id == user.id)
        .first()
    )
    if not observation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Observation introuvable.")

    db.delete(observation)
    db.commit()
    return None