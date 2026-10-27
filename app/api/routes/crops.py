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
from app.models.activity import DONE_STATUSES, Activity
from app.models.crop import Crop
from app.models.crop_observation import CropObservation
from app.models.farm import Farm
from app.models.parcel import Parcel
from app.models.utilisateur import User
from app.schemas.crop import (
    CropCreate,
    ExpectedHarvestPreview,
    CLOSED_STATUSES,
    check_crop_dates,
    check_end,
    check_harvest,
    CropSuggestions,
    CropObservationCreate,
    CropObservationResponse,
    CropResponse,
    CropUpdate,
    SpeciesOption,
    VarietyOption,
)
from app.services.agronomy.calendar import (
    expected_harvest_date,
    get_stage_at,
    next_harvest_after,
)
from app.services.agronomy.species import (
    ORIGIN_LABELS,
    SPECIES,
    get_varieties,
    is_annual,
    is_orchard,
    lifespan_note,
    list_species,
    optimal_production_label,
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


def _last_harvest_activity(crop: Crop, db: Session) -> Optional[date]:
    """Date de la dernière activité de type récolte ou coupe, ou None."""
    # Seules les tâches FAITES comptent : une récolte planifiée n'a pas eu lieu
    activities = (
        db.query(Activity)
        .filter(
            Activity.crop_id == crop.id,
            Activity.status.in_(DONE_STATUSES),
            Activity.performed_at.isnot(None),
        )
        .order_by(Activity.performed_at.desc())
        .all()
    )
    for activity in activities:
        if (activity.activity_type or "").strip().lower() in HARVEST_TYPES:
            return activity.performed_at.date()
    return None


def _sync_harvest_date(crop: Crop, db: Session) -> None:
    """
    Déduit la date de récolte réelle de la dernière activité de récolte.

    Évite la double saisie : l'utilisateur enregistre la récolte comme
    une intervention, la date remonte ici automatiquement.

    La date suit la DERNIÈRE récolte, pas la première : une culture
    coupée plusieurs fois dans la saison (menthe, géranium) ne doit pas
    rester figée sur sa première coupe. Une valeur déjà en place n'est
    remplacée que par une récolte postérieure, ce qui préserve une
    saisie manuelle tant qu'aucune coupe plus récente n'est déclarée.

    Le passage en statut « harvested » est réservé aux ANNUELLES : chez
    une pérenne, la récolte ne termine pas la culture, elle ouvre le
    cycle suivant. La plante reste en place, donc `active`.
    """
    harvested_on = _last_harvest_activity(crop, db)
    if harvested_on is None:
        return

    if crop.actual_harvest_date is None or harvested_on > crop.actual_harvest_date:
        crop.actual_harvest_date = harvested_on

    # Une récolte réelle prouve que le verger produit
    crop.in_production = True

    if is_annual(crop.species) and crop.status == "active":
        crop.status = "harvested"


def _keep_perennial_active(crop: Crop) -> None:
    """
    Une PÉRENNE n'est jamais clôturée par une récolte : la plante reste en
    place, la récolte ouvre le cycle suivant. Si le formulaire envoie le
    statut « harvested » pour une pérenne, la récolte (date, quantité) est
    gardée mais la culture reste `active`, exactement comme lorsqu'une
    récolte est déduite d'une activité (_sync_harvest_date).
    """
    if crop.status == "harvested" and not is_annual(crop.species):
        crop.status = "active"
        # Une récolte réelle prouve que le verger produit (même règle que
        # _sync_harvest_date)
        if is_orchard(crop.species):
            crop.in_production = True


def _apply_end_rules(crop: Crop) -> None:
    """
    « Terminée » est réservé aux PÉRENNES arrivées en fin de vie : une
    annuelle se clôture par sa récolte. Hors « Terminée », la date de fin
    n'a pas de sens et est effacée.
    """
    if crop.status == "ended" and is_annual(crop.species):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Une culture annuelle se clôture par « Récoltée », pas par « Terminée ».",
        )
    if crop.status != "ended":
        crop.end_date = None


def _compute_expected_harvest(crop: Crop) -> None:
    """
    Remplit la date de récolte prévue depuis le calendrier agronomique.

    Trois cas :
      - culture abandonnée : jamais de récolte prévue ;
      - ANNUELLE récoltée : le cycle est fini, la date n'est plus
        recalculée ; seule la date réelle compte ;
      - PÉRENNE : le cycle reboucle. Tant qu'aucune récolte réelle n'est
        enregistrée, la date se calcule depuis la plantation. Dès qu'une
        récolte a eu lieu, la prochaine se calcule À PARTIR D'ELLE, sans
        repasser par la date de plantation.

    Pour un arbre fruitier pas encore en production, la date reste vide :
    le système ne devine pas l'année de première récolte.
    """
    if crop.status in CLOSED_STATUSES:
        crop.expected_harvest_date = None
        return

    orchard = is_orchard(crop.species)

    if is_annual(crop.species):
        if crop.status != "active":
            return
        crop.expected_harvest_date = expected_harvest_date(
            crop.species,
            crop.planting_date,
            orchard=orchard,
            in_production=bool(crop.in_production),
        )
        return

    # Pérenne : la récolte réelle devient le point de départ du cycle suivant
    if crop.actual_harvest_date is not None:
        if orchard and not crop.in_production:
            crop.expected_harvest_date = None
            return
        crop.expected_harvest_date = next_harvest_after(
            crop.species, crop.actual_harvest_date
        )
        return

    crop.expected_harvest_date = expected_harvest_date(
        crop.species,
        crop.planting_date,
        orchard=orchard,
        in_production=bool(crop.in_production),
    )


def _refresh_cycle(crop: Crop, db: Session) -> None:
    """
    Remet la culture à jour sur son cycle : récolte réelle déduite des
    activités, puis récolte prévue recalculée. Appelé à chaque lecture,
    pour que la récolte suivante d'une pérenne apparaisse sans action
    manuelle.

    La récolte prévue n'est JAMAIS saisie à la main : elle est toujours
    calculée, depuis la plantation ou depuis la dernière récolte. Elle est
    donc recalculée à chaque lecture, ce qui la garde à jour (saison
    suivante) et remplace toute ancienne date saisie à la main.
    """
    _sync_harvest_date(crop, db)
    _compute_expected_harvest(crop)


def _age_years(planting_date: Optional[date], at: Optional[date] = None) -> Optional[float]:
    """Âge de la plantation en années, à la date du jour ou à `at`."""
    if planting_date is None:
        return None
    reference = at or date.today()
    return round(max((reference - planting_date).days, 0) / 365.25, 1)


def _to_response(crop: Crop) -> CropResponse:
    spec = SPECIES.get(crop.species)
    # Culture terminée : son âge s'arrête à sa date de fin
    age = _age_years(crop.planting_date, crop.end_date if crop.status == "ended" else None)
    # Message de durée de vie : seulement pour une culture encore en place
    note = lifespan_note(crop.species, age) if crop.status == "active" else None
    return CropResponse(
        id=crop.id,
        parcel_id=crop.parcel_id,
        species=crop.species,
        species_label=spec.label if spec else crop.species,
        variety=crop.variety,
        planting_date=crop.planting_date,
        expected_harvest_date=crop.expected_harvest_date,
        actual_harvest_date=crop.actual_harvest_date,
        actual_yield=crop.actual_yield,
        yield_unit=crop.yield_unit,
        density=crop.density,
        in_production=bool(crop.in_production),
        is_orchard=is_orchard(crop.species),
        is_annual=is_annual(crop.species),
        age_years=age,
        optimal_production=optimal_production_label(crop.species),
        lifespan_level=note[0] if note else None,
        lifespan_note=note[1] if note else None,
        status=crop.status,
        end_date=crop.end_date,
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


@router.get("/crops/expected-harvest", response_model=ExpectedHarvestPreview)
def preview_expected_harvest(
    species: str,
    planting_date: date,
    in_production: bool = False,
    harvest_date: Optional[date] = None,
    user: User = Depends(get_current_user),
):
    """
    Récolte prévue pour une espèce et une date de plantation, AVANT
    l'enregistrement : le formulaire l'affiche dès la saisie. Même calcul
    que celui appliqué à l'enregistrement (_compute_expected_harvest).

    Avec `harvest_date` (statut « Récoltée » dans le formulaire) :
      - pérenne : la PROCHAINE récolte, calculée à partir de cette récolte ;
      - annuelle : aucune, le cycle est terminé.
    """
    code = species.strip().lower()
    if code not in SPECIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Espèce inconnue.")

    orchard = is_orchard(code)

    if harvest_date is not None:
        if is_annual(code):
            return ExpectedHarvestPreview(
                expected_harvest_date=None,
                reason="Culture annuelle : le cycle se termine avec la récolte.",
            )
        # Une récolte réelle prouve que le verger produit : la case
        # « en production » sera cochée à l'enregistrement
        return ExpectedHarvestPreview(
            expected_harvest_date=next_harvest_after(code, harvest_date),
            reason=None,
        )

    result = expected_harvest_date(
        code, planting_date, orchard=orchard, in_production=in_production
    )
    reason = None
    if result is None:
        reason = (
            "Verger pas encore en production : pas de récolte prévue."
            if orchard and not in_production
            else "Pas de période de récolte connue pour cette espèce."
        )
    return ExpectedHarvestPreview(expected_harvest_date=result, reason=reason)


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
    Une culture abandonnée ou terminée libère toujours l'espèce : une
    lavande arrachée en fin de vie peut être replantée.
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
        _refresh_cycle(crop, db)
    db.commit()
    return [_to_response(c) for c in crops]


@router.get("/crops/{crop_id}", response_model=CropResponse)
def get_crop(
    crop_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    crop = _get_owned_crop(crop_id, user, db)
    _refresh_cycle(crop, db)
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
    # La récolte prévue est toujours calculée : une date envoyée est ignorée
    data.pop("expected_harvest_date", None)

    crop = Crop(**data)
    if not is_orchard(crop.species):
        crop.in_production = False
    _keep_perennial_active(crop)
    _apply_end_rules(crop)
    _compute_expected_harvest(crop)
    # Culture abandonnée : elle ne sera jamais récoltée
    if crop.status in CLOSED_STATUSES:
        crop.expected_harvest_date = None

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
    # La récolte prévue est toujours calculée : une date envoyée est ignorée
    changes.pop("expected_harvest_date", None)
    for field, value in changes.items():
        setattr(crop, field, value)

    if not is_orchard(crop.species):
        crop.in_production = False
    _keep_perennial_active(crop)
    try:
        _apply_end_rules(crop)
    except HTTPException:
        db.rollback()
        raise

    # Récolte prévue toujours recalculée, d'après l'état final de la
    # culture : plantation, dernière récolte, statut, verger en production.
    # Chez une pérenne, une nouvelle récolte réelle ouvre le cycle suivant.
    _compute_expected_harvest(crop)

    # Culture abandonnée : elle ne sera jamais récoltée
    if crop.status in CLOSED_STATUSES:
        crop.expected_harvest_date = None

    # Cohérence sur l'état FINAL de la culture, après le recalcul de la
    # récolte prévue : une modification partielle doit rester compatible
    # avec ce qui est déjà enregistré. Une culture récoltée doit porter sa
    # date et sa production réelles.
    try:
        check_crop_dates(crop.planting_date, crop.expected_harvest_date, crop.actual_harvest_date)
        check_harvest(crop.status, crop.actual_harvest_date, crop.actual_yield, crop.yield_unit)
        check_end(crop.status, crop.planting_date, crop.end_date)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))

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