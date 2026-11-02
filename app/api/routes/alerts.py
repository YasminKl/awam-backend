"""
Routes des alertes : liste avec l'historique de chaque épisode, nombre
d'alertes ouvertes (cloche de la barre du haut), fermeture avec sa raison.

Les alertes sont créées et fermées automatiquement par le diagnostic
satellite (app/services/alert_service.py). L'utilisateur, lui, les ferme
en indiquant si le problème était réel (« traité ») ou non (« fausse
alerte ») : ces réponses permettront de mesurer la fiabilité des alertes.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.alert import Alert
from app.models.crop import Crop
from app.models.crop_diagnosis import CropDiagnosis
from app.models.farm import Farm
from app.models.parcel import Parcel
from app.models.utilisateur import User
from app.schemas.alert import AlertCount, AlertHistoryItem, AlertResolve, AlertResponse
from app.services.agronomy.species import SPECIES
from app.services.alert_service import RESOLUTION_LABELS, SEVERITY_LABELS

router = APIRouter(tags=["alerts"])


def _get_owned_parcel(parcel_id: int, user: User, db: Session) -> Parcel:
    """Récupère une parcelle uniquement si elle appartient à l'utilisateur courant."""
    parcel = (
        db.query(Parcel)
        .join(Farm)
        .filter(Parcel.id == parcel_id, Farm.user_id == user.id)
        .first()
    )
    if not parcel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parcelle introuvable.")
    return parcel


def _get_owned_alert(alert_id: int, user: User, db: Session) -> Alert:
    """Récupère une alerte uniquement si sa parcelle appartient à l'utilisateur courant."""
    alert = (
        db.query(Alert)
        .join(Parcel, Alert.parcel_id == Parcel.id)
        .join(Farm, Parcel.farm_id == Farm.id)
        .filter(Alert.id == alert_id, Farm.user_id == user.id)
        .first()
    )
    if not alert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alerte introuvable.")
    return alert


def _owned_alerts(user: User, db: Session):
    return (
        db.query(Alert)
        .join(Parcel, Alert.parcel_id == Parcel.id)
        .join(Farm, Parcel.farm_id == Farm.id)
        .filter(Farm.user_id == user.id)
    )


def _close_orphans(user: User, db: Session) -> None:
    """
    Ferme les alertes dont la culture n'est plus suivie : supprimée,
    terminée, abandonnée, ou annuelle récoltée. Le diagnostic ne la
    traitant plus, son alerte resterait ouverte indéfiniment.
    """
    changed = False
    for alert in _owned_alerts(user, db).filter(Alert.resolved.is_(False)).all():
        crop = db.get(Crop, alert.crop_id) if alert.crop_id else None
        if alert.crop_id is not None and (crop is None or crop.status != "active"):
            alert.resolved = True
            alert.resolution = "auto"
            alert.resolved_at = alert.updated_at = datetime.now(timezone.utc)
            changed = True
    if changed:
        db.commit()


def _to_response(alert: Alert, db: Session) -> AlertResponse:
    parcel = db.get(Parcel, alert.parcel_id)
    farm = db.get(Farm, parcel.farm_id) if parcel else None
    crop = db.get(Crop, alert.crop_id) if alert.crop_id else None
    spec = SPECIES.get(crop.species) if crop else None

    history: list[AlertHistoryItem] = []
    if alert.crop_id and alert.first_scene_date and alert.last_scene_date:
        rows = (
            db.query(CropDiagnosis)
            .filter(
                CropDiagnosis.crop_id == alert.crop_id,
                CropDiagnosis.scene_date >= alert.first_scene_date,
                CropDiagnosis.scene_date <= alert.last_scene_date,
            )
            .order_by(CropDiagnosis.scene_date)
            .all()
        )
        history = [
            AlertHistoryItem(
                scene_date=r.scene_date,
                severity=r.severity,
                label=SEVERITY_LABELS.get(r.severity, r.severity),
            )
            for r in rows
        ]

    return AlertResponse(
        id=alert.id,
        parcel_id=alert.parcel_id,
        alert_type=alert.alert_type,
        severity=alert.severity,
        severity_label=SEVERITY_LABELS.get(alert.severity, alert.severity),
        message=alert.message,
        resolved=alert.resolved,
        created_at=alert.created_at,
        crop_id=alert.crop_id,
        crop_label=(spec.label if spec else crop.species) if crop else None,
        parcel_name=parcel.name if parcel else None,
        farm_id=farm.id if farm else None,
        farm_name=farm.name if farm else None,
        cause=alert.cause,
        evidence=alert.evidence,
        action=alert.action,
        valid_ratio=alert.valid_ratio,
        first_scene_date=alert.first_scene_date,
        last_scene_date=alert.last_scene_date,
        resolution=alert.resolution,
        resolution_label=RESOLUTION_LABELS.get(alert.resolution) if alert.resolution else None,
        resolved_at=alert.resolved_at,
        updated_at=alert.updated_at,
        history=history,
    )


#: Ordre de gravité : la plus grave d'abord ; un niveau inconnu passe en dernier
SEVERITY_ORDER = case(
    (Alert.severity == "strong_alert", 0),
    (Alert.severity == "alert", 1),
    (Alert.severity == "watch", 2),
    else_=3,
)


@router.get("/alerts", response_model=List[AlertResponse])
async def list_alerts(
    state: Literal["open", "closed", "all"] = Query("open"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Alertes de toutes les parcelles de l'utilisateur, dans l'ordre où il faut
    les traiter : la plus GRAVE d'abord, puis, à gravité égale, celle APPARUE
    le plus récemment (date de la première image de l'épisode, la même que
    « il y a N j » dans la cloche).
    """
    _close_orphans(user, db)
    query = _owned_alerts(user, db)
    if state == "open":
        query = query.filter(Alert.resolved.is_(False))
    elif state == "closed":
        query = query.filter(Alert.resolved.is_(True))
    alerts = query.order_by(
        SEVERITY_ORDER,
        func.coalesce(Alert.first_scene_date, Alert.last_scene_date).desc().nullslast(),
        Alert.created_at.desc(),
    ).all()
    return [_to_response(a, db) for a in alerts]


@router.get("/alerts/count", response_model=AlertCount)
async def count_open_alerts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Nombre d'alertes ouvertes, affiché sur la cloche."""
    _close_orphans(user, db)
    return AlertCount(open=_owned_alerts(user, db).filter(Alert.resolved.is_(False)).count())


@router.get("/alerts/recent", response_model=List[AlertResponse])
async def list_recent_alerts(
    days: int = Query(5, ge=1, le=31),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Historique des alertes des `days` derniers jours, sur TOUTES les fermes
    et parcelles de l'utilisateur : ouvertes ou fermées, les plus récentes
    d'abord. Une alerte y figure dès qu'elle a été créée, mise à jour ou
    fermée pendant cette période.
    """
    _close_orphans(user, db)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    alerts = (
        _owned_alerts(user, db)
        .filter(Alert.updated_at >= since)
        .order_by(Alert.updated_at.desc())
        .all()
    )
    return [_to_response(a, db) for a in alerts]


@router.get("/parcels/{parcel_id}/alerts", response_model=List[AlertResponse])
async def list_parcel_alerts(
    parcel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alertes ouvertes d'une parcelle."""
    _get_owned_parcel(parcel_id, user, db)
    _close_orphans(user, db)
    alerts = (
        db.query(Alert)
        .filter(Alert.parcel_id == parcel_id, Alert.resolved.is_(False))
        .order_by(Alert.created_at.desc())
        .all()
    )
    return [_to_response(a, db) for a in alerts]


@router.patch("/alerts/{alert_id}", response_model=AlertResponse)
async def resolve_alert(
    alert_id: int,
    payload: AlertResolve | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Ferme une alerte, en indiquant si le problème était réel (« traité »)
    ou non (« fausse alerte »). Sans précision : « traité ».
    """
    alert = _get_owned_alert(alert_id, user, db)
    alert.resolved = True
    alert.resolution = (payload or AlertResolve()).resolution
    alert.resolved_at = alert.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(alert)
    return _to_response(alert, db)