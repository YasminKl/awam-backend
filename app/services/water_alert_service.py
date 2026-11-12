"""
Alertes « Besoin en eau » : ouvertes et fermées chaque matin, après la mise
à jour de la météo, par la tâche du matin (weather.sync_daily).

Pour chaque culture en cours de chaque parcelle, le besoin des 7 derniers
jours de météo enregistrée est calculé avec le calcul du Suivi de
l'irrigation (water_balance_service.period_balance), sans autre seuil que
le besoin lui-même :

  - besoin NON couvert, aucune alerte ouverte : une alerte est ouverte ;
  - besoin non couvert, alerte déjà ouverte : ses chiffres sont mis à jour ;
  - besoin couvert, alerte ouverte : elle est fermée (« Résolue
    automatiquement ») ;
  - calcul impossible (météo manquante, surface inconnue…) : rien ne
    change, aucune alerte n'est ouverte ni fermée.

L'utilisateur peut aussi fermer l'alerte lui-même (« Traitée » ou « Fausse
alerte ») : c'est la route existante PATCH /alerts/{id}.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.parcel import Parcel
from app.services import water_balance_service as wb
from app.services.irrigation_service import active_crops

logger = logging.getLogger(__name__)

#: Type des alertes d'eau (les alertes du satellite : « diagnostic_satellite »)
ALERT_TYPE = "besoin_eau"

#: Niveau des alertes d'eau : pas de gravité, seulement le constat
SEVERITY = "besoin_non_couvert"

#: Textes de l'alerte : les explications validées du Suivi de l'irrigation
MESSAGE = "Besoin en eau non couvert"
CAUSE = "La culture a reçu moins d'eau que son besoin."


def _fmt(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _evidence(b: dict) -> str:
    missing = max(b["need_mm"] - b["received_mm"], 0)
    return (
        f"Du {b['start']:%d/%m} au {b['end']:%d/%m} : {_fmt(b['received_mm'])} mm reçus "
        f"(pluie mesurée et arrosages) pour un besoin de {_fmt(b['need_mm'])} mm ; "
        f"il manque {_fmt(missing)} mm."
    )


def sync_parcel(db: Session, parcel: Parcel, today: date | None = None) -> dict:
    """Ouvre, met à jour ou ferme les alertes d'eau des cultures en cours d'une parcelle."""
    today = today or date.today()
    summary = {"created": 0, "updated": 0, "resolved": 0, "skipped": 0}
    last_day = wb.last_weather_day(db, parcel.farm_id)
    if last_day is None:
        return summary
    reference_day = min(last_day, today)
    crops = active_crops(db, parcel.id)
    single = len(crops) == 1
    now = datetime.now(timezone.utc)

    for crop in crops:
        b = wb.period_balance(db, parcel, crop, reference_day - timedelta(days=6), reference_day, single, last_day)
        open_alert = (
            db.query(Alert)
            .filter(Alert.crop_id == crop.id, Alert.alert_type == ALERT_TYPE, Alert.resolved.is_(False))
            .first()
        )
        if b["problems"] or b["status"] is None:
            # Calcul impossible : rien n'est ouvert ni fermé
            summary["skipped"] += 1
            continue
        if b["status"] == wb.STATUS_NOT_COVERED:
            if open_alert is None:
                open_alert = Alert(
                    parcel_id=parcel.id, crop_id=crop.id, alert_type=ALERT_TYPE,
                    severity=SEVERITY, message=MESSAGE, cause=CAUSE,
                )
                db.add(open_alert)
                summary["created"] += 1
            else:
                summary["updated"] += 1
            open_alert.evidence = _evidence(b)
            open_alert.updated_at = now
        elif open_alert is not None:
            open_alert.evidence = _evidence(b)
            open_alert.resolved = True
            open_alert.resolution = "auto"
            open_alert.resolved_at = open_alert.updated_at = now
            summary["resolved"] += 1
    db.commit()
    return summary


def sync_all(db: Session, today: date | None = None) -> dict:
    """Les alertes d'eau de toutes les parcelles : appelée chaque matin, après la météo."""
    total = {"created": 0, "updated": 0, "resolved": 0, "skipped": 0}
    for parcel in db.query(Parcel).all():
        try:
            for k, v in sync_parcel(db, parcel, today).items():
                total[k] += v
        except Exception:  # noqa: BLE001 — une parcelle en erreur n'empêche pas les autres
            db.rollback()
            logger.exception("💧 [alertes] Parcelle %s : alertes d'eau non mises à jour", parcel.id)
    logger.info(
        "💧 [alertes] Besoin en eau : %d ouverte(s), %d mise(s) à jour, %d fermée(s), %d non calculée(s)",
        total["created"], total["updated"], total["resolved"], total["skipped"],
    )
    return total