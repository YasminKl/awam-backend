"""
Suivi de l'irrigation : les cultures ont-elles reçu assez d'eau ?

Pour une culture et une période, méthode FAO-56 :

    Besoin (mm)          = ET₀ × Kc
    Pluie utile (mm)     = pluie × 0,8   (même coefficient que le bilan hydrique)
    Irrigation (mm)      = litres des arrosages faits ÷ surface (m²)
    Besoin couvert (%)   = (pluie utile + irrigation) ÷ besoin × 100

  - ET₀ et pluie : weather_daily (Open-Meteo), déjà enregistrées chaque matin ;
  - Kc : référentiel agronomique (app/services/agronomy/water.py), selon le
    stade de la culture au milieu de la période ;
  - surface : celle de la parcelle, si elle n'a qu'une culture en cours.
    Une parcelle à plusieurs cultures demandera la surface de chacune
    (à ajouter plus tard) : en attendant, son bilan n'est pas calculé.

1 mm = 1 litre par m² : le besoin et le manque sont aussi donnés en litres,
et par plant si la densité de la culture est connue.

Le besoin est une ESTIMATION : celle d'une culture bien arrosée, en
conditions normales (sans réserve du sol, ni irrigation volontairement
réduite). Quand une donnée manque, aucun pourcentage n'est affiché, et la
raison est dite.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.crop import Crop
from app.models.irrigation_watering import WATERING_TASK_TYPE
from app.models.parcel import Parcel
from app.services.agronomy.calendar import get_stages_at
from app.services.agronomy.species import SPECIES
from app.services.agronomy.water import get_kc_for_stages
from app.services.irrigation_service import active_crops, crop_area_m2
from app.services.water_service import RAIN_EFFICIENCY

#: États du besoin couvert (%). Choix de présentation, pas des seuils de la
#: FAO : réglables ici. Sous LOW : manque d'eau ; sous OK : un peu juste ;
#: jusqu'à HIGH : bien couvert ; au-delà : trop d'eau.
COVERAGE_LOW = 50
COVERAGE_OK = 80
COVERAGE_HIGH = 120

STATUS_LABELS = {
    "manque": "manque d'eau",
    "juste": "un peu juste",
    "bien": "bien couvert",
    "trop": "trop d'eau",
}


def coverage_status(pct: float | None) -> str | None:
    if pct is None:
        return None
    if pct < COVERAGE_LOW:
        return "manque"
    if pct < COVERAGE_OK:
        return "juste"
    if pct <= COVERAGE_HIGH:
        return "bien"
    return "trop"


def _fmt(value: float, digits: int = 0) -> str:
    text_ = f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")
    return text_.replace(" ", "\u202f")


def _weather(db: Session, farm_id: int, start: date, end: date) -> tuple[int, float, float]:
    """Jours connus, ET₀ et pluie cumulées sur la période (weather_daily de la ferme)."""
    row = db.execute(
        text("""
            SELECT COUNT(et0_mm) AS n, COALESCE(SUM(et0_mm), 0) AS et0, COALESCE(SUM(precipitation_mm), 0) AS rain
            FROM weather_daily WHERE farm_id = :f AND day BETWEEN :s AND :e
        """),
        {"f": farm_id, "s": start, "e": end},
    ).fetchone()
    return int(row.n), float(row.et0), float(row.rain)


def _irrigation_liters(db: Session, parcel: Parcel, crop: Crop, single_crop: bool, start: date, end: date) -> float:
    """
    Litres des arrosages FAITS sur la période pour cette culture ; les
    arrosages « toute la parcelle » comptent pour elle si elle est seule.
    """
    since = datetime.combine(start, time.min)
    until = datetime.combine(end + timedelta(days=1), time.min)
    row = db.execute(
        text("""
            SELECT COALESCE(SUM(w.volume_l), 0) AS liters
            FROM activity a JOIN irrigation_watering w ON w.activity_id = a.id
            WHERE a.parcel_id = :p AND a.activity_type = :t AND a.status <> 'a_realiser'
              AND a.performed_at >= :since AND a.performed_at < :until
              AND (a.crop_id = :c OR (a.crop_id IS NULL AND :single))
        """),
        {"p": parcel.id, "t": WATERING_TASK_TYPE, "since": since, "until": until, "c": crop.id, "single": single_crop},
    ).fetchone()
    return float(row.liters)


def unassigned_liters(db: Session, parcel: Parcel, start: date, end: date) -> float:
    """Litres arrosés « sur toute la parcelle », sans culture, sur une parcelle à plusieurs cultures."""
    row = db.execute(
        text("""
            SELECT COALESCE(SUM(w.volume_l), 0) AS liters
            FROM activity a JOIN irrigation_watering w ON w.activity_id = a.id
            WHERE a.parcel_id = :p AND a.activity_type = :t AND a.status <> 'a_realiser' AND a.crop_id IS NULL
              AND a.performed_at >= :since AND a.performed_at < :until
        """),
        {
            "p": parcel.id, "t": WATERING_TASK_TYPE,
            "since": datetime.combine(start, time.min), "until": datetime.combine(end + timedelta(days=1), time.min),
        },
    ).fetchone()
    return float(row.liters)


def period_balance(db: Session, parcel: Parcel, crop: Crop, start: date, end: date, single_crop: bool) -> dict:
    """Le bilan d'eau d'une culture sur une période, et pourquoi il n'est pas calculable si c'est le cas."""
    days = (end - start).days + 1
    known_days, et0, rain = _weather(db, parcel.farm_id, start, end)
    middle = start + timedelta(days=days // 2)
    stages = get_stages_at(crop.species, middle, crop.planting_date)
    kc = get_kc_for_stages(crop.species, stages.codes) if stages else None
    area = crop_area_m2(db, parcel, crop)
    irrigation_l = _irrigation_liters(db, parcel, crop, single_crop, start, end)

    problems = []
    if known_days < days - 1:
        problems.append("météo incomplète sur cette période")
    if kc is None:
        problems.append("coefficient cultural inconnu pour cette espèce ou ce stade")
    if area is None:
        problems.append("la parcelle a plusieurs cultures : la surface de chacune sera à renseigner")

    out = {
        "start": start, "end": end, "days": days,
        "et0_mm": round(et0, 1), "kc": kc, "rain_mm": round(rain, 1),
        "irrigation_l": round(irrigation_l, 0),
        "need_mm": None, "need_l": None, "rain_useful_mm": round(rain * RAIN_EFFICIENCY, 1),
        "irrigation_mm": None, "received_mm": None, "received_l": None,
        "coverage_pct": None, "missing_l": None, "status": None, "problems": problems,
    }
    if problems:
        return out
    need_mm = et0 * kc
    irrigation_mm = irrigation_l / area
    received_mm = rain * RAIN_EFFICIENCY + irrigation_mm
    coverage = received_mm / need_mm * 100 if need_mm > 0 else None
    out.update({
        "need_mm": round(need_mm, 1),
        "need_l": round(need_mm * area, 0),
        "irrigation_mm": round(irrigation_mm, 1),
        "received_mm": round(received_mm, 1),
        "received_l": round(received_mm * area, 0),
        "coverage_pct": round(coverage, 0) if coverage is not None else None,
        "missing_l": round(max(need_mm - received_mm, 0) * area, 0),
        "status": coverage_status(coverage),
    })
    return out


def interpretation(crop_label: str, b: dict, plants: float | None, hose: dict | None) -> list[str]:
    """Les phrases qui expliquent le bilan des derniers jours, en mots simples."""
    if b["problems"]:
        return [f"Calcul impossible pour le moment, car {' et '.join(b['problems'])}."]
    lines = [
        f"{crop_label} a reçu {_fmt(b['received_mm'], 1)} mm d'eau (pluie utile et arrosages) "
        f"pour un besoin estimé de {_fmt(b['need_mm'], 1)} mm."
    ]
    # On ne parle de manque que si le besoin n'est PAS couvert (sous le seuil « bien »)
    if b["status"] in ("manque", "juste") and b["missing_l"]:
        line = f"Il manque environ {_fmt(b['missing_l'])} L"
        if plants:
            line += f", soit {_fmt(b['missing_l'] / plants)} L par plant"
        if hose and hose["flow_l_min"]:
            minutes = b["missing_l"] / hose["flow_l_min"]
            h, m = int(minutes // 60), int(round(minutes % 60))
            duration = f"{h} h {m:02d}" if h else f"{m} min"
            line += f" : à peu près {duration} avec {hose['name']}"
        lines.append(line + ".")
    elif b["status"] == "trop":
        lines.append("La culture a reçu plus d'eau que son besoin estimé : inutile d'arroser pour le moment.")
    else:
        lines.append("Le besoin est couvert : pas d'arrosage nécessaire pour le moment.")
    return lines


def parcel_balance(db: Session, parcel: Parcel, weeks: int, today: date | None = None) -> dict:
    """
    Le Suivi d'une parcelle : pour chaque culture en cours, le bilan des
    7 derniers jours et des `weeks` dernières semaines (du lundi au dimanche).
    """
    from app.models.irrigation_equipment import KIND_HOSE, IrrigationEquipment
    from app.services.irrigation_service import crop_labels

    today = today or date.today()
    crops = active_crops(db, parcel.id)
    single = len(crops) == 1
    hoses = (
        db.query(IrrigationEquipment)
        .filter(IrrigationEquipment.parcel_id == parcel.id, IrrigationEquipment.active.is_(True),
                IrrigationEquipment.kind == KIND_HOSE, IrrigationEquipment.hose_flow_l_min.isnot(None))
        .all()
    )
    this_monday = today - timedelta(days=today.weekday())
    week_starts = [this_monday - timedelta(weeks=i) for i in range(weeks)]

    out_crops = []
    for crop in sorted(crops, key=lambda c: c.id):
        label = crop_labels(db, [crop.id])[0]["label"]
        area = crop_area_m2(db, parcel, crop)
        plants = crop.density * area / 10000 if crop.density and area else None
        hose = next((h for h in hoses if crop.id in (h.crop_ids or [])), hoses[0] if hoses else None)
        hose_info = {"name": hose.name, "flow_l_min": hose.hose_flow_l_min} if hose else None
        last7 = period_balance(db, parcel, crop, today - timedelta(days=6), today, single)
        weeks_out = []
        for monday in week_starts:
            end = min(monday + timedelta(days=6), today)
            b = period_balance(db, parcel, crop, monday, end, single)
            b["current"] = monday == this_monday
            weeks_out.append(b)
        spec = SPECIES.get(crop.species)
        out_crops.append({
            "crop_id": crop.id,
            "label": label,
            "species_label": spec.label if spec else crop.species,
            "area_m2": area,
            "plants": round(plants) if plants else None,
            "last7": last7,
            "interpretation": interpretation(spec.label if spec else label, last7, plants, hose_info),
            "weeks": weeks_out,
        })

    return {
        "parcel_id": parcel.id,
        "parcel_name": parcel.name,
        "crops": out_crops,
        # Arrosages « toute la parcelle » d'une parcelle à plusieurs cultures :
        # on ne sait pas à quelle culture les compter
        "unassigned_l_last7": None if single else round(unassigned_liters(db, parcel, today - timedelta(days=6), today), 0),
        "thresholds": {"low": COVERAGE_LOW, "ok": COVERAGE_OK, "high": COVERAGE_HIGH},
    }