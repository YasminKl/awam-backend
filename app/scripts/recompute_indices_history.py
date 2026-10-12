"""
Recalcule l'historique après les corrections du calcul et de
l'interprétation des indices. Deux modes :

1. INDICES (par défaut) — après la correction de réflectance
   (conversion des valeurs brutes Sentinel-2 en réflectance, PSRI corrigé).

Pour chaque parcelle et chaque scène déjà présente dans `indice_reading`,
le script récupère la scène sur Planetary Computer par son identifiant,
recalcule les 15 indices et REMPLACE les anciennes valeurs. Il régénère
ensuite l'image de la carte la plus récente de chaque parcelle traitée.

Sécurité : pour une scène donnée, les anciennes valeurs ne sont supprimées
que si les nouvelles sont bien insérées (même transaction). Une scène en
échec garde donc ses anciennes valeurs, et le script passe à la suivante.

2. DIAGNOSTICS (--diagnoses) — après la correction des interprétations
   (FCOVER non évalué). Pour chaque culture active, les images sont
   reprises dans l'ordre chronologique, et la gravité de chaque diagnostic
   sert de « diagnostic précédent » au suivant : la règle des deux
   observations est rejouée sur des bases saines, au lieu de s'appuyer
   sur les anciennes gravités faussées. Sans --apply, rien n'est écrit :
   le script affiche seulement la comparaison ancien → nouveau.

Usage, depuis la racine du backend :

    python -m app.scripts.recompute_indices_history --dry-run          # liste, sans rien changer
    python -m app.scripts.recompute_indices_history --parcel-id 68     # une parcelle (test)
    python -m app.scripts.recompute_indices_history                    # tout l'historique

    python -m app.scripts.recompute_indices_history --diagnoses           # comparaison seule
    python -m app.scripts.recompute_indices_history --diagnoses --apply   # réécrit l'archive

⚠️  Ne pas lancer entre 5 h et 7 h : les tâches quotidiennes de météo et
    d'indices tourneraient en même temps sur les mêmes parcelles.
"""

import argparse
import logging
import sys
import time
from datetime import timedelta

import planetary_computer
from pystac_client import Client
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.crop import Crop
from app.models.crop_diagnosis import CropDiagnosis
from app.services.crop_monitoring import archive_diagnosis, has_recent_harvest, latest_scene
from app.services.diagnosis_service import diagnose
from app.services.sentinel_service import (
    COLLECTION,
    STAC_API_URL,
    generate_all_indices_for_parcel,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

SEVERITY_RANK = {"normal": 0, "watch": 1, "alert": 2, "strong_alert": 3}
SEVERITY_LABEL = {
    "normal": "normal",
    "watch": "à surveiller",
    "alert": "alerte",
    "strong_alert": "alerte confirmée",
}


def _scenes_to_recompute(db, parcel_id: int | None) -> list:
    """Couples (parcelle, scène) présents en base, du plus ancien au plus récent."""
    return db.execute(
        text("""
            SELECT DISTINCT ir.parcel_id, p.farm_id, ir.scene_id, ir.scene_date
            FROM indice_reading ir
            JOIN parcel p ON p.id = ir.parcel_id
            WHERE ir.scene_id IS NOT NULL
              AND (CAST(:parcel_id AS INTEGER) IS NULL OR ir.parcel_id = :parcel_id)
            ORDER BY ir.parcel_id, ir.scene_date
        """),
        {"parcel_id": parcel_id},
    ).fetchall()


def _fetch_scene(client: Client, scene_id: str):
    """Scène Planetary Computer par son identifiant, signée pour le téléchargement."""
    items = list(client.search(collections=[COLLECTION], ids=[scene_id]).items())
    return planetary_computer.sign(items[0]) if items else None


# ======================================================================
# Mode DIAGNOSTICS
# ======================================================================


def _scene_dates(db, parcel_id: int) -> list:
    """Dates des images exploitables de la parcelle, de la plus ancienne à la plus récente."""
    rows = db.execute(
        text("""
            SELECT DISTINCT scene_date FROM indice_reading
            WHERE parcel_id = :pid AND scene_date IS NOT NULL AND is_valid IS NOT FALSE
            ORDER BY scene_date
        """),
        {"pid": parcel_id},
    ).fetchall()
    return [r.scene_date for r in rows]


def _severity_label(severity: str | None) -> str:
    return SEVERITY_LABEL.get(severity, "—") if severity else "—"


def _recompute_diagnoses(db, parcel_id: int | None, apply: bool) -> int:
    """Rejoue les diagnostics de chaque culture active et les compare à l'archive."""
    query = db.query(Crop).filter(Crop.status == "active")
    if parcel_id is not None:
        query = query.filter(Crop.parcel_id == parcel_id)
    crops = query.order_by(Crop.parcel_id, Crop.id).all()

    counts = {"inchangé": 0, "moins grave": 0, "plus grave": 0, "nouveau": 0, "retiré": 0}
    written = 0

    for crop in crops:
        archived = {
            row.scene_date: row
            for row in db.query(CropDiagnosis).filter(CropDiagnosis.crop_id == crop.id)
        }
        print(f"\n=== Parcelle {crop.parcel_id} · culture {crop.id} ({crop.species}) ===")

        chain_severity = None
        for day in _scene_dates(db, crop.parcel_id):
            current = latest_scene(db, crop.parcel_id, before=day + timedelta(days=1))
            previous = latest_scene(db, crop.parcel_id, before=day)

            result = diagnose(
                species=crop.species,
                scene_date=day,
                values=current.values,
                previous_values=previous.values,
                previous_severity=chain_severity,
                recent_harvest=has_recent_harvest(db, crop, day),
                planting_date=crop.planting_date,
                valid_ratio=current.valid_ratio,
            )

            old = archived.get(day)

            # Comme l'archive : seuls les diagnostics avec indicateurs comptent.
            # Un ancien diagnostic qui n'a plus lieu d'être (image antérieure
            # à la plantation, par exemple) est retiré de l'archive.
            if not result.indicators:
                if old is not None:
                    counts["retiré"] += 1
                    print(f" − {day}  {_severity_label(old.severity):>16} → retiré           {result.reading}")
                    if apply:
                        db.delete(old)
                        db.commit()
                continue
            chain_severity = result.severity

            old_severity = old.severity if old else None
            if old_severity is None:
                status = "nouveau"
            elif old_severity == result.severity:
                status = "inchangé"
            elif SEVERITY_RANK[result.severity] < SEVERITY_RANK.get(old_severity, 0):
                status = "moins grave"
            else:
                status = "plus grave"
            counts[status] += 1

            marker = {"inchangé": " ", "moins grave": "↓", "plus grave": "↑", "nouveau": "+"}[status]
            print(
                f" {marker} {day}  {_severity_label(old_severity):>16} → "
                f"{_severity_label(result.severity):<16} {result.reading}"
            )

            if apply:
                archive_diagnosis(db, crop, result, current)
                written += 1

    print(
        f"\nBilan : {counts['moins grave']} moins grave(s), "
        f"{counts['plus grave']} plus grave(s), {counts['inchangé']} inchangé(s), "
        f"{counts['nouveau']} nouveau(x), {counts['retiré']} retiré(s)."
    )
    if apply:
        print(f"Archive réécrite : {written} diagnostic(s).")
    else:
        print("Aucune écriture (ajouter --apply pour réécrire l'archive).")
    return 0


# ======================================================================
# Point d'entrée
# ======================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="Recalcule l'historique des indices ou des diagnostics.")
    parser.add_argument("--parcel-id", type=int, default=None,
                        help="Ne traiter qu'une parcelle (par défaut : toutes).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Lister les scènes à recalculer sans rien modifier.")
    parser.add_argument("--skip-maps", action="store_true",
                        help="Ne pas régénérer l'image de la carte.")
    parser.add_argument("--diagnoses", action="store_true",
                        help="Recalculer les diagnostics des cultures au lieu des indices.")
    parser.add_argument("--apply", action="store_true",
                        help="Avec --diagnoses : réécrire l'archive des diagnostics.")
    args = parser.parse_args()

    if args.apply and not args.diagnoses:
        parser.error("--apply s'utilise avec --diagnoses")

    db = SessionLocal()
    try:
        if args.diagnoses:
            return _recompute_diagnoses(db, args.parcel_id, args.apply)

        scenes = _scenes_to_recompute(db, args.parcel_id)
        parcels = sorted({s.parcel_id for s in scenes})
        print(f"\n{len(scenes)} scène(s) à recalculer sur {len(parcels)} parcelle(s).")

        if args.dry_run:
            for s in scenes:
                print(f"  parcelle {s.parcel_id:>4}  {s.scene_date}  {s.scene_id}")
            return 0

        client = Client.open(STAC_API_URL)
        ok, failed = 0, []
        started = time.time()

        for i, s in enumerate(scenes, start=1):
            label = f"[{i}/{len(scenes)}] parcelle {s.parcel_id}, {s.scene_date}"
            try:
                item = _fetch_scene(client, s.scene_id)
                if item is None:
                    raise RuntimeError("scène introuvable sur Planetary Computer")

                result = generate_all_indices_for_parcel(
                    db, s.farm_id, s.parcel_id, item=item, force_refresh=True,
                )
                if not result or not result.get("inserted"):
                    # Rien d'inséré : on annule aussi la suppression des anciennes valeurs
                    db.rollback()
                    raise RuntimeError("aucun indice inséré, anciennes valeurs conservées")

                ok += 1
                print(f"✅ {label} : {result['inserted']} indices recalculés")

            except Exception as e:
                db.rollback()
                failed.append((s.parcel_id, s.scene_date, s.scene_id, str(e)))
                print(f"❌ {label} : {e}")

        print(f"\nIndices : {ok} scène(s) recalculée(s), {len(failed)} en échec "
              f"({(time.time() - started) / 60:.1f} min).")

        # Carte la plus récente de chaque parcelle (couche NDVI corrigée)
        if not args.skip_maps and parcels:
            from app.tasks.parcel_tasks import generate_parcel_rasters

            print(f"\nRégénération de la carte de {len(parcels)} parcelle(s)...")
            for pid in parcels:
                try:
                    generate_parcel_rasters(pid)
                    print(f"🗺️  parcelle {pid} : carte régénérée")
                except Exception as e:
                    print(f"❌ parcelle {pid} : carte non régénérée ({e})")

        if failed:
            print("\nScènes en échec (anciennes valeurs conservées) :")
            for pid, day, sid, err in failed:
                print(f"  parcelle {pid}  {day}  {sid}  → {err}")
            return 1
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())