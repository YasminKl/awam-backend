"""
Identification des facteurs de risque d'une alerte (page « Facteurs de
risque », rapport mensuel).

Pour chaque alerte, deux périodes :
  - la PÉRIODE DE L'ANOMALIE :
      · alerte satellite : de la dernière image valide avant l'épisode
        jusqu'à la première image de l'épisode ;
      · alerte d'eau : les 7 jours de météo enregistrée qui l'ont ouverte ;
  - DEPUIS LE DÉBUT DU STADE : du début du stade en cours (calendrier AWAM)
    jusqu'à la fin de la période de l'anomalie.

Les facteurs, tous calculés par les services existants, et leur état :
  🌍 environnementaux
     · climat : signalé s'il est au-dessus ou en dessous de TOUTES les
       années de la normale, aux mêmes dates (méthode de l'onglet Impacts) ;
     · pluie : signalée si la pluie seule n'a pas couvert le besoin d'une
       culture (calcul du Suivi de l'irrigation) ;
     · sol : la dernière analyse saisie, AFFICHÉE seulement (pas de
       référence validée pour dire qu'elle est anormale).
  🧑‍🌾 pratiques agricoles
     · irrigation : signalée si la pluie ET les arrosages n'ont pas couvert
       le besoin d'une culture ;
     · tâches culturales : signalées si une tâche planifiée pendant la
       période n'a pas été réalisée ;
     · rendement précédent : AFFICHÉ seulement.

La décision (origine probable) : environnementale si seuls des facteurs 🌍
sont signalés, pratique agricole si seuls des facteurs 🧑‍🌾 le sont, les deux
si les deux le sont, aucun facteur identifié sinon. Elle ne prouve pas la
cause : elle rassemble les faits anormaux présents au même moment.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.activity import STATUS_PLANNED, Activity
from app.models.alert import Alert
from app.models.crop import Crop
from app.models.irrigation_watering import WATERING_TASK_TYPE
from app.models.parcel import Parcel
from app.models.parcel_analysis import ParcelAnalysis
from app.services import impact_service as im
from app.services import vegetation_service as vs
from app.services import water_balance_service as wb
from app.services.agronomy.calendar import get_periods
from app.services.agronomy.species import stage_label
from app.services.irrigation_service import active_crops, crop_labels
from app.services.task_types import PREDEFINED_TASK_TYPES

ENVIRONMENT = "environnement"
PRACTICE = "pratique"

DECISIONS = {
    "environnementale": "Environnementale",
    "pratique": "Pratique agricole",
    "les_deux": "Environnementale et pratique agricole",
    "aucune": "Aucun facteur identifié : vérifier sur le terrain",
}

#: Paramètres du climat comparés, et leurs mots
_CLIMATE = {
    "temperature_max_c": ("Température max.", "°C", 1, "plus chaude", "plus fraîche"),
    "temperature_min_c": ("Température min.", "°C", 1, "plus chaude", "plus fraîche"),
    "precipitation_mm": ("Pluie", "mm", 0, "plus de pluie", "moins de pluie"),
    "humidity_mean_pct": ("Humidité de l'air", "%", 0, "plus humide", "plus sec"),
    "et0_mm": ("ET0", "mm", 0, "évaporation plus forte", "évaporation plus faible"),
}

_TASK_LABELS = dict(PREDEFINED_TASK_TYPES)


def _fmt(v, decimals: int = 1) -> str:
    return "—" if v is None else f"{v:,.{decimals}f}".replace(",", " ").replace(".", ",")


# ======================================================================
# Les périodes
# ======================================================================


def anomaly_period(db: Session, alert: Alert) -> tuple[date, date] | None:
    """La période de l'anomalie, selon le type d'alerte."""
    if alert.alert_type == "besoin_eau":
        opened = alert.created_at.date() if alert.created_at else date.today()
        last = wb.last_weather_day(db, db.get(Parcel, alert.parcel_id).farm_id)
        end = min(opened - timedelta(days=1), last) if last else opened - timedelta(days=1)
        return end - timedelta(days=6), end
    first = alert.first_scene_date
    if first is None:
        return None
    before = vs._readings(db, alert.parcel_id, None, first - timedelta(days=1))
    previous = [d for d, s in sorted(before.items()) if vs._is_usable(s)]
    if not previous:
        return None
    return previous[-1] + timedelta(days=1), first


def stage_periods(crops: list[Crop], day: date) -> list[dict]:
    """Le stade en cours de chaque culture à une date, et son début."""
    out = []
    for crop in crops:
        for period in get_periods(crop.species, crop.planting_date):
            if period.covers(day.month):
                start, _ = im.stage_dates(period, day.year, day.month)
                out.append({"crop_id": crop.id, "stage": stage_label(period.stage), "start": start})
    return out


# ======================================================================
# Les facteurs
# ======================================================================


def _climate(db: Session, farm_id: int, start: date, end: date, today: date) -> tuple[str, bool]:
    """Le climat d'une période, comparé aux mêmes dates des années de la normale."""
    c = im.stage_climate(db, farm_id, start, end, today)
    parts, flagged = [], False
    for key, (label, unit, dec, above, below) in _CLIMATE.items():
        p = c["parameters"].get(key)
        if not p or p["value"] is None:
            continue
        if p["position"] in ("above", "below"):
            flagged = True
            parts.append(f"{label} {_fmt(p['value'], dec)} {unit} : {above if p['position'] == 'above' else below} que toutes les années")
    if not parts:
        return ("dans la fourchette des années de la normale" if c["normals_years"] >= 2
                else "pas assez d'années complètes pour comparer"), False
    return " ; ".join(parts), flagged


def _water(db: Session, parcel: Parcel, crops: list[Crop], start: date, end: date) -> dict:
    """Pluie seule et pluie + arrosages, par rapport au besoin de chaque culture."""
    single = len(crops) == 1
    rain_flag = irrigation_flag = False
    rain_parts, irrigation_parts = [], []
    labels = {c["id"]: c["label"] for c in crop_labels(db, [c.id for c in crops])}
    irrigation_l = 0.0
    for crop in crops:
        b = wb.period_balance(db, parcel, crop, start, end, single)
        if b["problems"] or b["need_mm"] is None:
            rain_parts.append(f"{labels[crop.id]} : non calculable")
            continue
        irrigation_l = b["irrigation_l"]
        rain_ok = b["rain_mm"] >= b["need_mm"]
        total_ok = b["received_mm"] >= b["need_mm"]
        rain_flag = rain_flag or not rain_ok
        irrigation_flag = irrigation_flag or not total_ok
        rain_parts.append(f"{labels[crop.id]} : {_fmt(b['rain_mm'])} mm pour un besoin de {_fmt(b['need_mm'])} mm")
        irrigation_parts.append(f"{labels[crop.id]} : {_fmt(b['coverage_pct'], 0)} % du besoin")
    count = (
        db.query(Activity)
        .filter(Activity.parcel_id == parcel.id, Activity.activity_type == WATERING_TASK_TYPE,
                Activity.status != STATUS_PLANNED,
                Activity.performed_at >= start, Activity.performed_at < end + timedelta(days=1))
        .count()
    )
    return {
        "rain": ("; ".join(rain_parts), rain_flag),
        "irrigation": (f"{count} arrosage(s), {_fmt(irrigation_l, 0)} L ; pluie + arrosages : " + "; ".join(irrigation_parts)
                       if irrigation_parts else f"{count} arrosage(s)", irrigation_flag),
    }


def _tasks(db: Session, parcel: Parcel, start: date, end: date) -> tuple[str, bool]:
    """Tâches culturales réalisées, et tâches planifiées non réalisées, pendant la période."""
    done = (
        db.query(Activity)
        .filter(Activity.parcel_id == parcel.id, Activity.activity_type != WATERING_TASK_TYPE,
                Activity.status != STATUS_PLANNED,
                Activity.performed_at >= start, Activity.performed_at < end + timedelta(days=1))
        .order_by(Activity.performed_at).all()
    )
    missed = (
        db.query(Activity)
        .filter(Activity.parcel_id == parcel.id, Activity.activity_type != WATERING_TASK_TYPE,
                Activity.status == STATUS_PLANNED,
                Activity.planned_for >= start, Activity.planned_for <= end)
        .order_by(Activity.planned_for).all()
    )
    name = lambda a: _TASK_LABELS.get(a.activity_type, a.activity_type)  # noqa: E731
    parts = [f"{name(a)} réalisée le {a.performed_at:%d/%m}" for a in done]
    parts += [f"{name(a)} planifiée le {a.planned_for:%d/%m}, non réalisée" for a in missed]
    return ("; ".join(parts) if parts else "aucune tâche enregistrée"), bool(missed)


def _soil(db: Session, parcel: Parcel, end: date) -> str:
    a = (
        db.query(ParcelAnalysis)
        .filter(ParcelAnalysis.parcel_id == parcel.id, ParcelAnalysis.analyzed_at < end + timedelta(days=1))
        .order_by(ParcelAnalysis.analyzed_at.desc()).first()
    )
    if a is None:
        return "aucune analyse de sol saisie"
    parts = [f"type {a.soil_type}" if a.soil_type else None,
             f"pH {_fmt(a.soil_ph)}" if a.soil_ph is not None else None,
             f"humidité {_fmt(a.soil_humidity_pct, 0)} %" if a.soil_humidity_pct is not None else None,
             f"matière organique {_fmt(a.soil_organic_matter_pct)} %" if a.soil_organic_matter_pct is not None else None,
             f"salinité {_fmt(a.soil_salinity_g_l)} g/L" if a.soil_salinity_g_l is not None else None]
    return f"analyse du {a.analyzed_at:%d/%m/%Y} : " + ", ".join(p for p in parts if p)


def _yields(db: Session, crops: list[Crop]) -> str:
    labels = {c["id"]: c["label"] for c in crop_labels(db, [c.id for c in crops])}
    parts = [f"{labels[c.id]} : {_fmt(c.actual_yield)} {c.yield_unit or ''}".strip() + (f", récolte du {c.actual_harvest_date:%d/%m/%Y}" if c.actual_harvest_date else "")
             for c in crops if c.actual_yield is not None]
    return "; ".join(parts) if parts else "aucun rendement enregistré"


def _factor(key, label, category, anomaly, stage, flagged):
    return {"key": key, "label": label, "category": category, "anomaly": anomaly, "stage": stage, "flagged": flagged}


def alert_factors(db: Session, alert: Alert, today: date | None = None) -> dict:
    """La fiche des facteurs de risque d'une alerte."""
    today = today or date.today()
    parcel = db.get(Parcel, alert.parcel_id)
    crops = active_crops(db, parcel.id)
    if alert.crop_id:
        crop = db.get(Crop, alert.crop_id)
        crops = [crop] if crop else crops
    crops = sorted(crops, key=lambda c: c.id)
    period = anomaly_period(db, alert)
    out = {"alert_id": alert.id, "anomaly_period": None, "stage_period": None, "stages": [],
           "factors": [], "decision": None, "decision_label": None}
    if period is None:
        out["decision_label"] = "Période de l'anomalie inconnue : pas d'image valide avant l'épisode."
        return out
    a_start, a_end = period
    stages = stage_periods(crops, a_end)
    s_start = min((s["start"] for s in stages), default=a_start)
    s_start = min(s_start, a_start)
    labels = {c["id"]: c["label"] for c in crop_labels(db, [c.id for c in crops])}
    out.update({
        "anomaly_period": {"start": a_start, "end": a_end},
        "stage_period": {"start": s_start, "end": a_end},
        "stages": [{"crop": labels.get(s["crop_id"]), "stage": s["stage"], "start": s["start"]} for s in stages],
    })

    clim_a, clim_a_flag = _climate(db, parcel.farm_id, a_start, a_end, today)
    clim_s, clim_s_flag = _climate(db, parcel.farm_id, s_start, a_end, today)
    water_a = _water(db, parcel, crops, a_start, a_end)
    water_s = _water(db, parcel, crops, s_start, a_end)
    tasks_a, tasks_a_flag = _tasks(db, parcel, a_start, a_end)
    tasks_s, tasks_s_flag = _tasks(db, parcel, s_start, a_end)

    factors = [
        _factor("climate", "Climat", ENVIRONMENT, clim_a, clim_s, clim_a_flag or clim_s_flag),
        _factor("rain", "Pluie", ENVIRONMENT, water_a["rain"][0], water_s["rain"][0], water_a["rain"][1] or water_s["rain"][1]),
        _factor("soil", "Sol", ENVIRONMENT, _soil(db, parcel, a_end), None, None),
        _factor("irrigation", "Irrigation", PRACTICE, water_a["irrigation"][0], water_s["irrigation"][0],
                water_a["irrigation"][1] or water_s["irrigation"][1]),
        _factor("tasks", "Tâches culturales", PRACTICE, tasks_a, tasks_s, tasks_a_flag or tasks_s_flag),
        _factor("yield", "Rendement précédent", PRACTICE, _yields(db, crops), None, None),
    ]
    env = any(f["flagged"] for f in factors if f["category"] == ENVIRONMENT)
    pra = any(f["flagged"] for f in factors if f["category"] == PRACTICE)
    decision = "les_deux" if env and pra else "environnementale" if env else "pratique" if pra else "aucune"
    out.update({"factors": factors, "decision": decision, "decision_label": DECISIONS[decision]})
    return out