"""
Création, mise à jour et fermeture des alertes, à partir du diagnostic
satellite de chaque culture (app/services/diagnosis_service.py).

Appelé juste après l'archivage du diagnostic d'une image, dans la tâche
Celery qui traite les nouvelles images (crop_monitoring.archive_for_parcel),
et à la consultation du diagnostic. Le traitement est IDEMPOTENT : une
image déjà prise en compte ne modifie plus l'alerte.

Une culture a au plus UNE alerte ouverte : un épisode. Son évolution :

  diagnostic de l'image   alerte ouverte ?   effet
  ---------------------   ----------------   -------------------------------
  alert / strong_alert    non                création de l'épisode
  alert / strong_alert    oui                mise à jour (niveau, cause…)
  watch (« à surveiller »)  oui              l'anomalie diminue sans
                                             disparaître : « Anomalie détectée »
  watch                   non                rien : pas d'alerte pour une
                                             simple surveillance
  normal                  oui                fermeture : « résolue
                                             automatiquement »
  période de coupe        —                  rien : la baisse est attendue,
                                             l'image n'apporte pas d'information

Le niveau de chaque image reste lisible dans crop_diagnosis : c'est
l'historique de l'épisode, affiché date par date.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.crop import Crop

logger = logging.getLogger(__name__)

ALERT_TYPE = "diagnostic_satellite"

#: Niveaux qui ouvrent ou maintiennent une alerte
ALERT_LEVELS = ("alert", "strong_alert")

#: Libellés affichés : jamais « forte » ni « critique »
SEVERITY_LABELS = {
    "strong_alert": "Anomalie persistante",
    "alert": "Anomalie détectée",
    "watch": "À surveiller",
    "normal": "Normal",
}

RESOLUTION_LABELS = {
    "treated": "Problème vérifié et traité",
    "false_alarm": "Fausse alerte",
    "auto": "Résolue automatiquement",
}


def _evidence(result) -> str:
    """Les indices sous leur seuil, ex. « NDMI 0,05 (normal 0,15–0,35) »."""
    parts = []
    for ind in result.indicators:
        if ind.level == "low":
            value = f"{ind.value:.2f}".replace(".", ",")
            parts.append(f"{ind.name} {value}{ind.unit} ({ind.threshold_label})")
    return " ; ".join(parts)


def _fill(alert: Alert, result, scene, severity: str) -> None:
    """Met à jour le contenu de l'alerte d'après le diagnostic de l'image."""
    risk = result.risks[0] if result.risks else None
    cause = risk.diagnosis if risk else result.reading
    alert.severity = severity
    alert.cause = (cause or "")[:200]
    alert.evidence = _evidence(result) or None
    alert.action = " · ".join(x for x in (risk.measure, risk.action) if x) if risk else result.recommendation
    alert.valid_ratio = scene.valid_ratio
    alert.last_scene_date = scene.scene_date
    alert.message = f"{SEVERITY_LABELS[severity]} : {alert.cause}"[:500]


def sync_alert(db: Session, crop: Crop, result, scene) -> str | None:
    """
    Fait évoluer l'alerte de la culture d'après le diagnostic d'une image.

    Returns:
        "created", "updated", "resolved", ou None si rien n'a changé.
    """
    if scene.scene_date is None:
        return None
    # Période de coupe ou de récolte : la baisse est attendue
    if result.cultural_drop:
        return None

    open_alert = (
        db.query(Alert)
        .filter(
            Alert.crop_id == crop.id,
            Alert.alert_type == ALERT_TYPE,
            Alert.resolved.is_(False),
        )
        .first()
    )
    # Image déjà prise en compte : rien à refaire
    if open_alert and open_alert.last_scene_date and scene.scene_date <= open_alert.last_scene_date:
        return None

    change = None
    if result.severity in ALERT_LEVELS:
        if open_alert is None:
            open_alert = Alert(
                parcel_id=crop.parcel_id,
                crop_id=crop.id,
                alert_type=ALERT_TYPE,
                first_scene_date=scene.scene_date,
            )
            db.add(open_alert)
            change = "created"
        else:
            change = "updated"
        _fill(open_alert, result, scene, result.severity)

    elif result.severity == "watch" and open_alert is not None:
        # L'anomalie diminue sans disparaître
        _fill(open_alert, result, scene, "alert")
        change = "updated"

    elif result.severity == "normal" and open_alert is not None:
        open_alert.resolved = True
        open_alert.resolution = "auto"
        open_alert.resolved_at = datetime.now(timezone.utc)
        open_alert.last_scene_date = scene.scene_date
        change = "resolved"

    if change:
        open_alert.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("[alertes] Culture %s (%s) : alerte %s", crop.id, crop.species, change)
    return change