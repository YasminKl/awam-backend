"""
Routes de diagnostic et de besoins.

Le front n'applique aucune règle agronomique : il affiche ce que ces
routes décident. La même logique servira donc aux alertes par e-mail et
à toute autre interface.
"""

from datetime import date
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.crop import Crop
from app.models.farm import Farm
from app.models.parcel import Parcel
from app.models.parcel_analysis import ParcelAnalysis
from app.models.utilisateur import User
from app.schemas.diagnosis import (
    DiagnosisResponse,
    IndicatorResponse,
    NeedsResponse,
    RiskResponse,
    SoilRowResponse,
    StageInfo,
    StageTimelineItem,
    WaterNeedResponse,
)
from app.services.agronomy.calendar import get_periods, get_stage_at, get_stages_at
from app.services.agronomy.species import is_annual, stage_label
from app.services.agronomy.water import evaluate_soil
from app.services.crop_monitoring import archive_diagnosis, run_diagnosis
from app.services.water_service import get_water_need

router = APIRouter(tags=["diagnosis"])

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


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


# ----------------------------------------------------------------------
# Diagnostic
# ----------------------------------------------------------------------


def _fmt(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _closed_reason(crop: Crop) -> str | None:
    """
    Raison pour laquelle une culture n'est plus suivie, ou None si elle est
    en place. Une culture clôturée n'a plus de diagnostic, plus de stade
    courant et plus de besoin en eau : la plante n'est plus sur la parcelle
    ou son cycle est fini. Même règle que le diagnostic automatique
    (crop_monitoring), qui ne traite que les cultures « active ».
    """
    if crop.status == "ended":
        return f"Culture terminée le {_fmt(crop.end_date)} : plus de suivi satellite."
    if crop.status == "abandoned":
        return "Culture abandonnée : plus de suivi satellite."
    if crop.status == "harvested" and is_annual(crop.species):
        return (
            f"Culture annuelle récoltée le {_fmt(crop.actual_harvest_date)} : "
            "cycle terminé, plus de suivi satellite."
        )
    return None


@router.get("/crops/{crop_id}/diagnosis", response_model=DiagnosisResponse)
def get_crop_diagnosis(
    crop_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Diagnostic de la culture pour la dernière image satellite : stade,
    verdicts par indice selon l'âge, lecture croisée, risques, actions,
    explication et recommandation du référentiel agronomique.

    Le résultat est archivé : il sert de base à la confirmation de la
    prochaine anomalie et à l'historique de la saison.
    """
    crop = _get_owned_crop(crop_id, user, db)

    # Culture clôturée : ni diagnostic, ni archivage
    closed = _closed_reason(crop)
    if closed:
        return DiagnosisResponse(
            crop_id=crop_id,
            stage=StageInfo(stage=None, label="Suivi terminé"),
            reading=closed,
            severity="normal",
        )

    result, scene = run_diagnosis(db, crop)
    archive_diagnosis(db, crop, result, scene)

    reference_date = scene.scene_date or date.today()
    days = (
        (reference_date - crop.planting_date).days
        if crop.planting_date else None
    )

    return DiagnosisResponse(
        crop_id=crop_id,
        scene_date=scene.scene_date,
        stage=StageInfo(
            stage=result.stage,
            label=result.stage_label,
            period=result.stage_period,
            days_since_planting=days,
            age_years=result.age_years,
        ),
        indicators=[
            IndicatorResponse(
                name=i.name, value=round(i.value, 4),
                delta=round(i.delta, 4) if i.delta is not None else None,
                level=i.level, threshold_label=i.threshold_label, unit=i.unit,
            )
            for i in result.indicators
        ],
        risks=[
            RiskResponse(
                severity=r.severity, diagnosis=r.diagnosis,
                measure=r.measure, action=r.action,
            )
            for r in result.risks
        ],
        reading=result.reading,
        explanation=result.explanation,
        recommendation=result.recommendation,
        cultural_drop=result.cultural_drop,
        age_required=result.age_required,
        severity=result.severity,
    )


@router.get("/crops/{crop_id}/stages", response_model=List[StageTimelineItem])
def get_crop_stages(
    crop_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Frise des stades de l'espèce, avec le stade courant mis en avant."""
    crop = _get_owned_crop(crop_id, user, db)

    today = date.today()
    current = get_stage_at(crop.species, today, crop.planting_date)
    # Culture clôturée : la frise reste consultable, sans stade courant
    current_stage = current.stage if current and not _closed_reason(crop) else None

    return [
        StageTimelineItem(
            stage=p.stage,
            label=stage_label(p.stage),
            period=p.label,
            is_current=(p.stage == current_stage),
            cultural_drop=p.cultural_drop,
        )
        for p in get_periods(crop.species, crop.planting_date)
    ]


# ----------------------------------------------------------------------
# Besoins
# ----------------------------------------------------------------------


@router.get("/crops/{crop_id}/needs", response_model=NeedsResponse)
async def get_crop_needs(
    crop_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Besoins de la culture : irrigation calculée depuis la météo et le
    coefficient cultural, fertilisation déduite de la dernière analyse
    de sol comparée aux références de l'espèce.
    """
    crop = _get_owned_crop(crop_id, user, db)

    # Culture clôturée : aucune irrigation ni fertilisation à conseiller
    closed = _closed_reason(crop)
    if closed:
        return NeedsResponse(
            crop_id=crop_id,
            closed_reason=closed.replace("plus de suivi satellite", "aucun besoin en eau ni en fertilisation"),
        )

    row = db.execute(
        text("""
            SELECT f.latitude, f.longitude,
                   ST_Area(p.geom::geography) / 10000.0 AS area_ha
            FROM parcel p JOIN farm f ON f.id = p.farm_id
            WHERE p.id = :pid
        """),
        {"pid": crop.parcel_id},
    ).fetchone()

    # Tous les stades actifs aujourd'hui : le Kc retenu est le plus élevé
    # (voir water.get_kc_for_stages), et non celui du seul stade principal
    state = get_stages_at(crop.species, date.today(), crop.planting_date)
    stages = state.codes if state else ()

    # --- Irrigation ---------------------------------------------------
    water = None
    if row and row.latitude is not None and row.longitude is not None:
        need = await get_water_need(
            species=crop.species, stage=stages,
            latitude=row.latitude, longitude=row.longitude,
            area_ha=row.area_ha,
        )
        if need:
            water = WaterNeedResponse(**need.__dict__)

    # --- Fertilisation ------------------------------------------------
    analysis = (
        db.query(ParcelAnalysis)
        .filter(ParcelAnalysis.parcel_id == crop.parcel_id)
        .order_by(ParcelAnalysis.analyzed_at.desc())
        .first()
    )

    soil_rows: list[SoilRowResponse] = []
    analysis_date = None
    if analysis:
        analysis_date = analysis.analyzed_at.date()
        soil_rows = [
            SoilRowResponse(**r)
            for r in evaluate_soil(
                crop.species,
                ph=analysis.soil_ph,
                salinity=analysis.soil_salinity_g_l,
                organic_matter=analysis.soil_organic_matter_pct,
            )
        ]

    return NeedsResponse(
        crop_id=crop_id,
        water=water,
        soil=soil_rows,
        soil_analysis_date=analysis_date,
    )