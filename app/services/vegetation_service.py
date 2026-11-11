"""
Onglet « Végétation » de la page Climat et cultures : suivre les plantes
d'image en image pendant un mois, et le climat entre deux images.

Uniquement des données de la plateforme, sans seuil inventé :
  - les indices des images Sentinel-2 VALIDES de la parcelle
    (indice_reading) : NDVI (vigueur), NDMI (eau des feuilles), NDRE
    (chlorophylle) ;
  - leur INTERPRÉTATION selon le référentiel AWAM (agronomy/thresholds.py) :
    pour l'espèce, la date et l'âge de chaque culture, l'intervalle attendu
    de chaque indice surveillé, l'explication et la recommandation du
    document. Un indice sans seuil à cette date n'est pas interprété ;
  - leur place dans la PLAGE HABITUELLE de la parcelle (baseline_service,
    la même que la page Fermes) ;
  - le CLIMAT entre deux images : pluie, ET₀ et eau reçue par rapport au
    besoin, du même calcul que l'onglet Impacts.

AWAM met les faits côte à côte, sans conclure à une cause.

L'évolution d'un indice est donnée par son ÉCART exact ; le pourcentage
n'est donné que si les deux valeurs sont positives : un indice proche de
zéro (le NDMI) donnerait des pourcentages trompeurs.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.crop import Crop
from app.models.parcel import Parcel
from app.services import impact_service as im
from app.services.agronomy.thresholds import get_rules_at
from app.services.baseline_service import compute_baseline, current_plantation_start
from app.services.irrigation_service import active_crops, crop_labels

#: Indices suivis dans l'onglet, dans l'ordre d'affichage
INDICES = ("NDVI", "NDMI", "NDRE")


# ======================================================================
# Les images
# ======================================================================


def _readings(db: Session, parcel_id: int, start: date | None, end: date) -> dict[date, dict]:
    """Les images de la parcelle entre deux dates : indices, nuages, validité."""
    rows = db.execute(
        text("""
            SELECT scene_date, indice_name, value, cloud_cover, valid_ratio, COALESCE(is_valid, TRUE) AS valid
            FROM indice_reading
            WHERE parcel_id = :p AND scene_date IS NOT NULL AND scene_date <= :e
              AND (CAST(:s AS DATE) IS NULL OR scene_date >= :s)
              AND indice_name = ANY(:names)
            ORDER BY scene_date
        """),
        {"p": parcel_id, "s": start, "e": end, "names": list(INDICES)},
    ).fetchall()
    scenes: dict[date, dict] = defaultdict(lambda: {"values": {}, "cloud_cover": None, "valid_ratio": None, "valid": True})
    for r in rows:
        s = scenes[r.scene_date]
        if r.value is not None and r.valid:
            s["values"][r.indice_name] = float(r.value)
        s["valid"] = s["valid"] and bool(r.valid)
        if r.cloud_cover is not None:
            s["cloud_cover"] = float(r.cloud_cover)
        if r.valid_ratio is not None:
            s["valid_ratio"] = float(r.valid_ratio)
    return dict(scenes)


def _is_usable(scene: dict) -> bool:
    """Une image compte si elle est valide et donne au moins la vigueur (NDVI)."""
    return scene["valid"] and "NDVI" in scene["values"]


def _change(previous: float | None, current: float | None) -> dict | None:
    """Écart exact ; pourcentage seulement si les deux valeurs sont positives."""
    if previous is None or current is None:
        return None
    diff = current - previous
    pct = diff / previous * 100 if previous > 0 and current > 0 else None
    return {
        "diff": round(diff, 3),
        "pct": round(pct, 0) if pct is not None else None,
        "direction": "up" if diff > 0 else "down" if diff < 0 else "same",
    }


# ======================================================================
# L'interprétation : référentiel AWAM et plage habituelle
# ======================================================================


def _age_years(crop: Crop, when: date) -> float | None:
    if not crop.planting_date:
        return None
    return max((when - crop.planting_date).days / 365.25, 0.0)


def interpret(crop: Crop, when: date, values: dict[str, float]) -> list[dict]:
    """
    Chaque indice de l'image lu avec le référentiel AWAM, pour l'espèce, la
    date et l'âge de la culture. Un indice sans seuil à cette date est
    signalé comme tel, sans interprétation.
    """
    selection = get_rules_at(crop.species, when, _age_years(crop, when))
    by_index = {}
    for rule in selection.rules:
        for threshold in rule.thresholds:
            by_index.setdefault(threshold.indice.upper(), (threshold, rule))
    out = []
    for name in INDICES:
        value = values.get(name)
        if value is None:
            continue
        found = by_index.get(name)
        if not found:
            out.append({"indice": name, "value": round(value, 3), "level": None, "low": None, "high": None,
                        "explanation": None, "recommendation": None})
            continue
        threshold, rule = found
        level = threshold.evaluate(value)
        out.append({
            "indice": name,
            "value": round(value, 3),
            "level": level,
            "low": threshold.low,
            "high": threshold.high,
            "inverted": threshold.inverted,
            "explanation": rule.explanation or None,
            "recommendation": rule.recommendation if level == "low" else None,
        })
    return out


def _series(db: Session, parcel_id: int) -> dict[str, list[tuple[date, float]]]:
    """Toutes les mesures valides de la parcelle, par indice : lues une seule fois."""
    rows = db.execute(
        text("""
            SELECT indice_name, scene_date, value FROM indice_reading
            WHERE parcel_id = :p AND indice_name = ANY(:names) AND scene_date IS NOT NULL
              AND value IS NOT NULL AND COALESCE(is_valid, TRUE)
            ORDER BY scene_date
        """),
        {"p": parcel_id, "names": list(INDICES)},
    ).fetchall()
    out: dict[str, list[tuple[date, float]]] = {k: [] for k in INDICES}
    for r in rows:
        out[r.indice_name].append((r.scene_date, float(r.value)))
    return out


def _baselines(series: dict[str, list[tuple[date, float]]], since: date | None, when: date, values: dict[str, float]) -> dict[str, dict]:
    """Place de chaque valeur dans la plage habituelle de la parcelle (même calcul que la page Fermes)."""
    out = {}
    for name in INDICES:
        value = values.get(name)
        if value is None:
            continue
        b = compute_baseline(name, series.get(name, []), when, value, since=since)
        out[name] = {
            "status": b.status, "verdict": b.verdict,
            "low": round(b.low, 3) if b.low is not None else None,
            "high": round(b.high, 3) if b.high is not None else None,
            "years": b.years, "message": b.message,
        }
    return out


# ======================================================================
# Le climat entre deux images
# ======================================================================


def _climate_between(db: Session, parcel: Parcel, crops: list[Crop], start: date, end: date) -> dict:
    """Pluie, ET₀ et eau reçue par rapport au besoin de chaque culture, du même calcul que l'onglet Impacts."""
    single = len(crops) == 1
    known, et0, rain = im.wb._weather(db, parcel.farm_id, start, end)
    days = (end - start).days + 1
    per_crop = []
    for crop in crops:
        w = im.water_period(db, parcel, crop, start, end, single)
        per_crop.append({
            "crop_id": crop.id, "need_mm": w["need_mm"], "received_mm": w["received_mm"],
            "coverage_pct": w["coverage_pct"], "covered": w["covered"], "problems": w["problems"],
        })
    return {
        "start": start, "end": end, "days": days,
        "weather_complete": known >= days,
        "rain_mm": round(rain, 1), "et0_mm": round(et0, 1),
        "crops": per_crop,
    }


# ======================================================================
# Le mois
# ======================================================================


def parcel_vegetation(db: Session, parcel: Parcel, year: int, month: int, today: date | None = None) -> dict:
    """Tout ce qu'affiche l'onglet Végétation pour une parcelle et un mois."""
    today = today or date.today()
    start = date(year, month, 1)
    end = min(date(year, month, calendar.monthrange(year, month)[1]), today)
    crops = sorted(active_crops(db, parcel.id), key=lambda c: c.id)
    labels = {c["id"]: c["label"] for c in crop_labels(db, [c.id for c in crops])} if crops else {}

    month_scenes = _readings(db, parcel.id, start, end)
    usable = [(d, s) for d, s in sorted(month_scenes.items()) if _is_usable(s)]
    excluded = len(month_scenes) - len(usable)

    # Le dernier passage valide avant le mois : point de comparaison du premier passage
    before = _readings(db, parcel.id, None, start - timedelta(days=1))
    previous = next(((d, s) for d, s in sorted(before.items(), reverse=True) if _is_usable(s)), None)

    # Historique de chaque indice, pour situer chaque passage dans la plage habituelle
    series = _series(db, parcel.id)
    since = current_plantation_start(db, parcel.id)

    scenes_out = []
    prev = previous
    for when, scene in usable:
        values = scene["values"]
        item = {
            "date": when,
            "cloud_cover": scene["cloud_cover"],
            "valid_ratio": scene["valid_ratio"],
            "values": {k: round(v, 3) for k, v in values.items()},
            "previous_date": prev[0] if prev else None,
            "changes": {k: _change(prev[1]["values"].get(k), values.get(k)) for k in INDICES} if prev else {},
            "interpretation": [{"crop_id": c.id, "readings": interpret(c, when, values)} for c in crops],
            "baselines": _baselines(series, since, when, values),
            "climate_before": _climate_between(db, parcel, crops, prev[0] + timedelta(days=1), when) if prev and crops else None,
        }
        scenes_out.append(item)
        prev = (when, scene)

    summary = None
    if usable:
        first_date, first = usable[0]
        last_date, last = usable[-1]
        summary = {
            "first_date": first_date,
            "last_date": last_date,
            "changes": {k: _change(first["values"].get(k), last["values"].get(k)) for k in INDICES} if len(usable) > 1 else {},
            "latest": {k: round(v, 3) for k, v in last["values"].items()},
            "baselines": _baselines(series, since, last_date, last["values"]),
            "water": _climate_between(db, parcel, crops, first_date, last_date) if len(usable) > 1 and crops else None,
        }

    first_scene = db.execute(
        text("SELECT MIN(scene_date) FROM indice_reading WHERE parcel_id = :p AND COALESCE(is_valid, TRUE)"),
        {"p": parcel.id},
    ).scalar()
    return {
        "parcel_id": parcel.id,
        "parcel_name": parcel.name,
        "month": f"{year}-{month:02d}",
        "crops": [
            {
                "crop_id": c.id,
                "label": labels.get(c.id, c.species),
                "age_years": round(_age_years(c, end), 1) if _age_years(c, end) is not None else None,
                "stages": [s["label"] for s in im.crop_stages(c, year, month)],
            }
            for c in crops
        ],
        "multi_crop": len(crops) > 1,
        "satellite_since": first_scene,
        "excluded_scenes": excluded,
        "scenes": scenes_out,
        "summary": summary,
    }