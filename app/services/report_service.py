"""
Rapport mensuel d'une parcelle : il RASSEMBLE, sans rien calculer de
nouveau, les résultats que les autres parties d'AWAM produisent déjà.

  1. Climat du mois      → conditions_service.month_conditions (onglet Paramètres)
  2. Stades des cultures → impact_service.parcel_impacts (onglet Impacts)
  3. Eau                 → impact_service.parcel_impacts, qui utilise le calcul
                           du Suivi de l'irrigation (water_balance_service)
  4. Végétation          → vegetation_service.parcel_vegetation (onglet Végétation)
   5. Alertes du mois     → la table des alertes (ouvertes pendant le mois), avec
                           la fiche de leurs facteurs de risque (risk_factor_service)
  6. Arrosages réalisés  → les arrosages faits pendant le mois

Quand une donnée manque, la section le dit : rien n'est estimé.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.alert import Alert
from app.models.farm import Farm
from app.models.irrigation_watering import WATERING_TASK_TYPE, IrrigationWatering
from app.models.parcel import Parcel
from app.services import conditions_service as cs
from app.services import impact_service as im
from app.services import vegetation_service as vs
from app.services.alert_service import RESOLUTION_LABELS, SEVERITY_LABELS
from app.services.irrigation_service import crop_labels
from app.services.risk_factor_service import alert_factors

#: Les types d'alertes, tels qu'affichés dans la page Alertes
ALERT_TYPE_LABELS = {
    "besoin_eau": "Besoin en eau",
    "diagnostic_satellite": "Diagnostic satellitaire",
    "anomalie_vegetation": "Anomalie de végétation",
}


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _alerts(db: Session, parcel: Parcel, start: date, end: date, today: date) -> list[dict]:
    """Les alertes de la parcelle qui étaient ouvertes pendant le mois."""
    since = datetime.combine(start, time.min, tzinfo=timezone.utc)
    until = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    rows = (
        db.query(Alert)
        .filter(
            Alert.parcel_id == parcel.id,
            Alert.created_at < until,
            or_(Alert.resolved_at.is_(None), Alert.resolved_at >= since),
        )
        .order_by(Alert.created_at)
        .all()
    )
    labels = {c["id"]: c["label"] for c in crop_labels(db, [a.crop_id for a in rows if a.crop_id])}
    return [
        {
            "type": ALERT_TYPE_LABELS.get(a.alert_type, a.message),
            "severity_label": SEVERITY_LABELS.get(a.severity, a.severity),
            "crop_label": labels.get(a.crop_id) if a.crop_id else None,
            "cause": a.cause,
            "evidence": a.evidence,
            "opened": a.created_at.date() if a.created_at else None,
            "closed": a.resolved_at.date() if a.resolved_at else None,
            "resolution_label": RESOLUTION_LABELS.get(a.resolution) if a.resolution else None,
            "factors": alert_factors(db, a, today),
        }
        for a in rows
    ]


def _waterings(db: Session, parcel: Parcel, start: date, end: date) -> list[dict]:
    """Les arrosages faits sur la parcelle pendant le mois."""
    since = datetime.combine(start, time.min)
    until = datetime.combine(end + timedelta(days=1), time.min)
    rows = (
        db.query(Activity, IrrigationWatering)
        .join(IrrigationWatering, IrrigationWatering.activity_id == Activity.id)
        .filter(
            Activity.parcel_id == parcel.id,
            Activity.activity_type == WATERING_TASK_TYPE,
            Activity.status != "a_realiser",
            Activity.performed_at >= since,
            Activity.performed_at < until,
        )
        .order_by(Activity.performed_at)
        .all()
    )
    labels = {c["id"]: c["label"] for c in crop_labels(db, [a.crop_id for a, _ in rows if a.crop_id])}
    return [
        {
            "date": a.performed_at.date() if a.performed_at else None,
            "equipment": w.equipment.name if w.equipment else None,
            "volume_l": w.volume_l,
            "crop_label": labels.get(a.crop_id) if a.crop_id else "Toute la parcelle",
            "employees": [e.full_name for e in a.employees],
        }
        for a, w in rows
    ]


def parcel_month_report(db: Session, parcel: Parcel, year: int, month: int, today: date | None = None) -> dict:
    """Le rapport mensuel d'une parcelle : toutes ses sections, avec leurs sources."""
    today = today or date.today()
    start, end = _month_bounds(year, month)
    farm = db.get(Farm, parcel.farm_id)
    impacts = im.parcel_impacts(db, parcel, year, month, today)
    vegetation = vs.parcel_vegetation(db, parcel, year, month, today)
    return {
        "parcel_id": parcel.id,
        "parcel_name": parcel.name,
        "farm_id": parcel.farm_id,
        "farm_name": farm.name if farm else None,
        "month": f"{year}-{month:02d}",
        "generated_on": today,
        #: Dernier jour de météo enregistré : les calculs du mois s'y arrêtent
        "weather_until": impacts["weather_until"],
        "crops": [{"crop_id": c["crop_id"], "label": c["label"]} for c in impacts["crops"]],
        "polyculture": len(impacts["crops"]) > 1,
        "climate": cs.month_conditions(db, parcel.farm_id, year, month, today),
        "impacts": impacts,
        "vegetation": vegetation,
        "alerts": _alerts(db, parcel, start, end, today),
        "waterings": _waterings(db, parcel, start, end),
    }