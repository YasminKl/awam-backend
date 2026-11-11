"""
Suivi de l'irrigation : les cultures ont-elles reçu assez d'eau ?

Pour une culture et une période, méthode FAO-56, uniquement avec des
données de la plateforme :

    Besoin (mm)          = somme, jour par jour, de ET₀ du jour × Kc du stade de ce jour
    Irrigation (mm)      = litres des arrosages faits sur la parcelle ÷ surface de la parcelle (m²)
    Eau reçue (mm)       = pluie mesurée (comptée en entier) + irrigation
    Besoin couvert (%)   = eau reçue ÷ besoin × 100

  - ET₀ et pluie : weather_daily (Open-Meteo), enregistrées chaque matin ;
  - Kc : référentiel agronomique AWAM (app/services/agronomy/water.py),
    selon le stade de la culture CHAQUE JOUR ;
  - arrosages : chaque arrosage fait est compté comme un arrosage de TOUTE
    la parcelle (c'est la pratique sur le terrain) : son eau est répartie
    sur la surface de la parcelle, et chaque culture reçoit la même hauteur
    d'eau. La culture choisie dans la fenêtre « Nouvel arrosage » n'est pas
    utilisée pour le moment : elle servira quand la surface de chaque
    culture sera connue.

Chaque période s'arrête au dernier jour de météo enregistré (en général
hier) : le besoin, la pluie et les arrosages portent sur exactement les
mêmes jours.

Deux états, sans seuil inventé : la seule limite est le besoin lui-même.
  - « besoin couvert »     : l'eau reçue atteint le besoin ;
  - « besoin non couvert » : l'eau reçue est inférieure au besoin.

1 mm = 1 litre par m² : le besoin et le manque sont aussi donnés en litres
pour la parcelle (l'eau à répandre sur toute sa surface), et par plant si
la parcelle n'a qu'une culture et que sa densité est connue.

Le besoin est celui d'une culture bien arrosée, en conditions normales
(conditions de référence de la méthode FAO-56). Quand une donnée manque,
aucun pourcentage n'est affiché, et la raison est dite.
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
from app.services.irrigation_service import active_crops, parcel_area_ha

#: Les deux états du besoin couvert. La seule limite est le besoin lui-même :
#: aucun seuil inventé.
STATUS_COVERED = "couvert"
STATUS_NOT_COVERED = "non_couvert"

STATUS_LABELS = {
    STATUS_COVERED: "besoin couvert",
    STATUS_NOT_COVERED: "besoin non couvert",
}


def coverage_status(received_mm: float | None, need_mm: float | None) -> str | None:
    """Besoin couvert si l'eau reçue atteint le besoin, sinon non couvert."""
    if received_mm is None or need_mm is None or need_mm <= 0:
        return None
    return STATUS_COVERED if received_mm >= need_mm else STATUS_NOT_COVERED


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


def _daily_weather(db: Session, farm_id: int, start: date, end: date) -> list[tuple[date, float, float]]:
    """La météo de chaque jour enregistré de la période : (jour, ET₀, pluie)."""
    rows = db.execute(
        text("""
            SELECT day, et0_mm, COALESCE(precipitation_mm, 0) AS rain
            FROM weather_daily
            WHERE farm_id = :f AND day BETWEEN :s AND :e AND et0_mm IS NOT NULL
            ORDER BY day
        """),
        {"f": farm_id, "s": start, "e": end},
    ).fetchall()
    return [(r.day, float(r.et0_mm), float(r.rain)) for r in rows]


def last_weather_day(db: Session, farm_id: int) -> date | None:
    """Le dernier jour de météo enregistré pour la ferme."""
    return db.execute(
        text("SELECT MAX(day) FROM weather_daily WHERE farm_id = :f AND et0_mm IS NOT NULL"), {"f": farm_id},
    ).scalar()


def parcel_irrigation_liters(db: Session, parcel: Parcel, start: date, end: date) -> float:
    """
    Litres de TOUS les arrosages faits sur la parcelle pendant la période,
    quelle que soit la culture choisie : chaque arrosage est compté comme
    un arrosage de toute la parcelle.
    """
    row = db.execute(
        text("""
            SELECT COALESCE(SUM(w.volume_l), 0) AS liters
            FROM activity a JOIN irrigation_watering w ON w.activity_id = a.id
            WHERE a.parcel_id = :p AND a.activity_type = :t AND a.status <> 'a_realiser'
              AND a.performed_at >= :since AND a.performed_at < :until
        """),
        {
            "p": parcel.id, "t": WATERING_TASK_TYPE,
            "since": datetime.combine(start, time.min), "until": datetime.combine(end + timedelta(days=1), time.min),
        },
    ).fetchone()
    return float(row.liters)


def _parcel_area_m2(db: Session, parcel: Parcel) -> float | None:
    area_ha = parcel_area_ha(db, parcel)
    return round(area_ha * 10000, 1) if area_ha else None


def period_balance(
    db: Session, parcel: Parcel, crop: Crop, start: date, end: date, single_crop: bool,
    last_day: date | None = None,
) -> dict:
    """
    Le bilan d'eau d'une culture sur une période, et pourquoi il n'est pas
    calculable si c'est le cas. La période s'arrête au dernier jour de
    météo enregistré (`last_day`) : besoin, pluie et arrosages portent sur
    les mêmes jours. Le besoin est calculé jour par jour, avec le Kc du
    stade de chaque jour. Les arrosages sont ceux de toute la parcelle,
    répartis sur sa surface (`single_crop` n'est plus utilisé).
    """
    if last_day is None:
        last_day = last_weather_day(db, parcel.farm_id)
    effective_end = min(end, last_day) if last_day else start - timedelta(days=1)
    days = (effective_end - start).days + 1 if effective_end >= start else 0
    weather = _daily_weather(db, parcel.farm_id, start, effective_end) if days else []
    area = _parcel_area_m2(db, parcel)

    problems = []
    if days == 0:
        problems.append("la météo de cette période n'est pas encore enregistrée")
    elif len(weather) < days:
        problems.append(f"la météo manque pour {days - len(weather)} jour(s) de la période")

    # Besoin jour par jour : ET₀ du jour × Kc du stade de ce jour
    need_mm = 0.0
    kcs = []
    for day, et0_day, _rain in weather:
        stages = get_stages_at(crop.species, day, crop.planting_date)
        kc_day = get_kc_for_stages(crop.species, stages.codes) if stages else None
        if kc_day is None:
            problems.append("coefficient cultural inconnu pour cette espèce ou ce stade")
            break
        kcs.append(kc_day)
        need_mm += et0_day * kc_day
    if area is None:
        problems.append("la surface de la parcelle est inconnue")

    et0 = sum(w[1] for w in weather)
    rain = sum(w[2] for w in weather)
    irrigation_l = parcel_irrigation_liters(db, parcel, start, effective_end) if days else 0.0

    out = {
        "start": start, "end": effective_end if days else end, "days": days,
        "et0_mm": round(et0, 1),
        # Kc moyen de la période : besoin ÷ ET₀ (égal au Kc du stade s'il ne change pas)
        "kc": round(need_mm / et0, 2) if kcs and et0 > 0 else None,
        "kc_varies": len(set(kcs)) > 1,
        "rain_mm": round(rain, 1),
        "irrigation_l": round(irrigation_l, 0),
        "need_mm": None, "need_l": None,
        "irrigation_mm": None, "received_mm": None, "received_l": None,
        "coverage_pct": None, "missing_l": None, "status": None, "problems": problems,
    }
    if problems:
        return out
    irrigation_mm = irrigation_l / area
    received_mm = rain + irrigation_mm
    coverage = received_mm / need_mm * 100 if need_mm > 0 else None
    out.update({
        "need_mm": round(need_mm, 1),
        "need_l": round(need_mm * area, 0),
        "irrigation_mm": round(irrigation_mm, 1),
        "received_mm": round(received_mm, 1),
        "received_l": round(received_mm * area, 0),
        "coverage_pct": round(coverage, 0) if coverage is not None else None,
        "missing_l": round(max(need_mm - received_mm, 0) * area, 0),
        "status": coverage_status(received_mm, need_mm),
    })
    return out


def interpretation(crop_label: str, b: dict, plants: float | None, hose: dict | None) -> list[str]:
    """Les phrases qui expliquent le bilan des derniers jours, en mots simples."""
    if b["problems"]:
        return [f"Calcul impossible pour le moment, car {' et '.join(b['problems'])}."]
    lines = [
        f"{crop_label} a reçu {_fmt(b['received_mm'], 1)} mm d'eau (pluie mesurée et arrosages) "
        f"pour un besoin de {_fmt(b['need_mm'], 1)} mm."
    ]
    if b["status"] == STATUS_NOT_COVERED:
        line = f"La culture a reçu moins d'eau que son besoin : il manque {_fmt(b['missing_l'])} litres"
        if plants:
            line += f", soit {_fmt(b['missing_l'] / plants)} litres par plant"
        if hose and hose["flow_l_min"]:
            # Durée calculée avec le débit réel de l'équipement enregistré
            minutes = b["missing_l"] / hose["flow_l_min"]
            h, m = int(minutes // 60), int(round(minutes % 60))
            duration = f"{h} h {m:02d}" if h else f"{m} min"
            line += f", soit {duration} avec {hose['name']} à son débit de {_fmt(hose['flow_l_min'], 1)} L/min"
        lines.append(line + ".")
    elif b["status"] == STATUS_COVERED:
        lines.append("La pluie et les arrosages ont apporté au moins l'eau dont la culture avait besoin.")
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
    # Dernier jour de météo enregistré (en général hier) : les périodes s'y arrêtent
    last_day = last_weather_day(db, parcel.farm_id)
    reference_day = min(last_day, today) if last_day else today - timedelta(days=1)
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
        area = _parcel_area_m2(db, parcel)
        # Plants de la culture : seulement si elle occupe toute la parcelle
        plants = crop.density * area / 10000 if single and crop.density and area else None
        hose = next((h for h in hoses if crop.id in (h.crop_ids or [])), hoses[0] if hoses else None)
        hose_info = {"name": hose.name, "flow_l_min": hose.hose_flow_l_min} if hose else None
        last7 = period_balance(db, parcel, crop, reference_day - timedelta(days=6), reference_day, single, last_day)
        weeks_out = []
        for monday in week_starts:
            end = min(monday + timedelta(days=6), today)
            b = period_balance(db, parcel, crop, monday, end, single, last_day)
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
        # Dernier jour de météo enregistré : les périodes s'y arrêtent
        "weather_until": last_day,
    }