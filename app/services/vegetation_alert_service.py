"""
Alertes « Anomalie de végétation » : la surveillance satellite d'une
PARCELLE, adaptée à la polyculture.

Après chaque nouvelle image (tâche d'ingestion), chaque indice — vigueur
(NDVI), eau des feuilles (NDMI), chlorophylle (NDRE) — est comparé,
SÉPARÉMENT, à la plage habituelle de la parcelle : ses propres valeurs, à
la même période, les années précédentes (baseline_service, la même que la
page Fermes et l'onglet Végétation). Le mélange de cultures de la parcelle
est donc comparé au même mélange.

  - un indice en dessous de sa plage sur les DEUX dernières images
    valides (règle de confirmation du diagnostic existant) : une alerte
    est ouverte pour la parcelle, ou mise à jour ;
  - plus aucun indice en dessous sur la dernière image : l'alerte est
    fermée (« Résolue automatiquement ») ;
  - plage habituelle non calculable (historique insuffisant) : rien n'est
    ouvert ni fermé.

Aucun seuil n'est inventé : la référence est l'historique de la parcelle.
L'ancien diagnostic par culture continue de fonctionner à côté.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.services import vegetation_service as vs
from app.services.baseline_service import compute_baseline, current_plantation_start

logger = logging.getLogger(__name__)

ALERT_TYPE = "anomalie_vegetation"
SEVERITY = "anomalie_vegetation"
MESSAGE = "Anomalie de végétation"

#: Les indices surveillés, et leur nom affiché
INDEX_LABELS = {"NDVI": "Vigueur", "NDMI": "Eau des feuilles", "NDRE": "Chlorophylle"}


def _fmt(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def _two_last_scenes(db: Session, parcel_id: int, until: date | None = None) -> list[tuple[date, dict]]:
    """Les deux dernières images VALIDES de la parcelle (jusqu'à `until`)."""
    scenes = vs._readings(db, parcel_id, None, until or date.today())
    usable = [(d, s) for d, s in sorted(scenes.items()) if vs._is_usable(s)]
    return usable[-2:]


def below_indices(db: Session, parcel_id: int, until: date | None = None) -> dict:
    """
    Pour les deux dernières images valides : chaque indice, sa valeur et sa
    plage habituelle, et s'il est en dessous. Retourne aussi les indices
    confirmés (en dessous sur les DEUX images).
    """
    last_two = _two_last_scenes(db, parcel_id, until)
    series = vs._series(db, parcel_id)
    since = current_plantation_start(db, parcel_id)
    per_scene = []
    for when, scene in last_two:
        status = {}
        for name in vs.INDICES:
            value = scene["values"].get(name)
            if value is None:
                continue
            b = compute_baseline(name, series.get(name, []), when, value, since=since)
            status[name] = {
                "value": value,
                "ok": b.status == "ok",
                "below": b.status == "ok" and b.verdict == "below",
                "low": b.low, "high": b.high,
            }
        per_scene.append((when, status))
    confirmed = []
    if len(per_scene) == 2:
        (_, prev), (_, last) = per_scene
        confirmed = [n for n in vs.INDICES if prev.get(n, {}).get("below") and last.get(n, {}).get("below")]
    return {"scenes": per_scene, "confirmed": confirmed}


def _cause(names: list[str]) -> str:
    labels = [INDEX_LABELS[n] for n in names]
    listed = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " et " + labels[-1]
    return f"{listed} en dessous de la plage habituelle de la parcelle"[:200]


def _evidence(scenes: list, names: list[str]) -> str:
    (d1, s1), (d2, s2) = scenes
    parts = []
    for n in names:
        parts.append(
            f"{INDEX_LABELS[n]} ({n}) : {_fmt(s1[n]['value'])} le {d1:%d/%m} puis {_fmt(s2[n]['value'])} le {d2:%d/%m}, "
            f"plage habituelle {_fmt(s2[n]['low'])} à {_fmt(s2[n]['high'])}"
        )
    return " ; ".join(parts) + "."


def sync_parcel(db: Session, parcel_id: int, until: date | None = None) -> str | None:
    """
    Ouvre, met à jour ou ferme l'alerte « Anomalie de végétation » d'une
    parcelle. Retourne "created", "updated", "resolved", ou None.
    """
    result = below_indices(db, parcel_id, until)
    scenes = result["scenes"]
    if not scenes:
        return None
    last_date, last = scenes[-1]
    open_alert = (
        db.query(Alert)
        .filter(Alert.parcel_id == parcel_id, Alert.alert_type == ALERT_TYPE, Alert.resolved.is_(False))
        .first()
    )
    now = datetime.now(timezone.utc)
    confirmed = result["confirmed"]

    if confirmed:
        change = "updated"
        if open_alert is None:
            open_alert = Alert(
                parcel_id=parcel_id, crop_id=None, alert_type=ALERT_TYPE, severity=SEVERITY, message=MESSAGE,
                first_scene_date=scenes[0][0],
            )
            db.add(open_alert)
            change = "created"
        open_alert.cause = _cause(confirmed)
        open_alert.evidence = _evidence(scenes, confirmed)
        open_alert.last_scene_date = last_date
        open_alert.updated_at = now
        db.commit()
        return change

    # Plus aucun indice en dessous sur la dernière image : retour dans la plage
    any_reference = any(s["ok"] for s in last.values())
    if open_alert is not None and any_reference and not any(s["below"] for s in last.values()):
        open_alert.resolved = True
        open_alert.resolution = "auto"
        open_alert.resolved_at = open_alert.updated_at = now
        open_alert.last_scene_date = last_date
        db.commit()
        return "resolved"
    return None