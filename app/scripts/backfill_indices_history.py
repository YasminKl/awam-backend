"""
Télécharge l'historique Sentinel-2 des parcelles sur plusieurs années, et
enregistre les 15 indices de chaque date dans `indice_reading`.

Cet historique sert de RÉFÉRENCE par parcelle : chaque nouvelle mesure
pourra être comparée à ce qui est habituel pour cette parcelle, à la même
période de l'année, plutôt qu'à des seuils généraux.

Règles :
  - UNE image par date : une même prise de vue apparaît dans plusieurs
    tuiles Sentinel-2 qui se chevauchent ; on garde celle où la parcelle
    est la mieux dégagée ;
  - une date n'est retenue que si la parcelle y est dégagée à au moins
    MIN_VALID_RATIO (60 %), comme pour la tâche quotidienne ;
  - rien avant la date de plantation la plus ancienne des cultures de la
    parcelle, quand elle est connue : avant, le sol était nu ou autre ;
  - les dates déjà en base sont ignorées : le script peut être interrompu
    et relancé sans tout recommencer ;
  - seules les VALEURS sont enregistrées, sans image cartographique ni
    envoi sur B2 : la carte n'affiche que la dernière image.

Résistance aux coupures réseau :
  - une recherche coupée par le serveur est relancée jusqu'à 3 fois, avec
    une pause croissante ;
  - l'autorisation d'accès aux images de Planetary Computer expire au bout
    d'environ une heure : elle est demandée juste avant chaque
    téléchargement, et une date refusée est retentée une fois ;
  - une parcelle en échec n'arrête pas les suivantes.

Usage, depuis la racine du backend :

    python -m app.scripts.backfill_indices_history --parcel-id 20 --dry-run
    python -m app.scripts.backfill_indices_history --parcel-id 20
    python -m app.scripts.backfill_indices_history --farm "Makthar" --farm "Sidi Mechreg"

⚠️  Ne pas lancer entre 5 h et 7 h : les tâches quotidiennes de météo et
    d'indices tourneraient en même temps sur les mêmes parcelles.
⚠️  Durée : environ 12 secondes par date retenue. Pour une parcelle sur
    trois ans, compter 30 à 60 minutes.
"""

import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date

import planetary_computer
from pystac_client import Client
from sqlalchemy import text

from app.db.database import SessionLocal
from app.services.sentinel_service import (
    COLLECTION,
    MAX_CLOUD_COVER,
    MIN_VALID_RATIO,
    STAC_API_URL,
    _get_bbox_for_parcel,
    _get_geometry_for_parcel,
    _parcel_valid_ratio,
    generate_all_indices_for_parcel,
)

logging.basicConfig(level=logging.WARNING, format="%(message)s")

#: Relances d'une recherche coupée par le serveur
SEARCH_ATTEMPTS = 3
#: Pause de base entre deux essais, en secondes (doublée au 2e essai)
RETRY_PAUSE_S = 10


# ----------------------------------------------------------------------
# Lecture de la base
# ----------------------------------------------------------------------


def _select_parcels(db, parcel_id: int | None, farms: list[str]) -> list:
    """Parcelles à traiter : une seule, celles de certaines fermes, ou toutes les actives."""
    query = """
        SELECT p.id, p.name, p.farm_id, f.name AS farm
        FROM parcel p JOIN farm f ON f.id = p.farm_id
        WHERE p.geom IS NOT NULL
    """
    params: dict = {}
    if parcel_id is not None:
        query += " AND p.id = :pid"
        params["pid"] = parcel_id
    else:
        query += " AND p.status = 'active'"
        if farms:
            query += " AND f.name = ANY(:farms)"
            params["farms"] = farms
    query += " ORDER BY f.name, p.name"
    return db.execute(text(query), params).fetchall()


def _planting_floor(db, parcel_id: int) -> date | None:
    """Date de plantation la plus ancienne des cultures de la parcelle."""
    return db.execute(
        text("SELECT MIN(planting_date) FROM crop WHERE parcel_id = :pid"),
        {"pid": parcel_id},
    ).scalar()


def _dates_in_base(db, parcel_id: int) -> set[str]:
    """Dates d'image déjà enregistrées pour la parcelle."""
    rows = db.execute(
        text("SELECT DISTINCT scene_date FROM indice_reading WHERE parcel_id = :pid"),
        {"pid": parcel_id},
    ).fetchall()
    return {r.scene_date.isoformat() for r in rows if r.scene_date}


# ----------------------------------------------------------------------
# Recherche des scènes
# ----------------------------------------------------------------------


def _search_with_retry(client: Client, bbox, start: date, end: date) -> list:
    """
    Recherche dans le catalogue, relancée si le serveur coupe la connexion.
    Pauses de 10 s puis 20 s entre les essais.
    """
    for attempt in range(1, SEARCH_ATTEMPTS + 1):
        try:
            search = client.search(
                collections=[COLLECTION],
                bbox=bbox,
                datetime=f"{start.isoformat()}/{end.isoformat()}",
                query={"eo:cloud_cover": {"lt": MAX_CLOUD_COVER}},
            )
            return list(search.items())
        except Exception as e:
            if attempt == SEARCH_ATTEMPTS:
                raise
            pause = RETRY_PAUSE_S * attempt
            print(f"   recherche coupée ({type(e).__name__}), nouvel essai dans {pause} s")
            time.sleep(pause)
    return []


def _candidates_by_date(client: Client, bbox, start: date, end: date) -> dict[str, list]:
    """
    Scènes sous le plafond de nuages, regroupées par date de prise de vue.
    Recherche année par année pour garder des réponses de taille raisonnable.
    """
    by_date: dict[str, list] = defaultdict(list)
    for year in range(start.year, end.year + 1):
        y_start = max(start, date(year, 1, 1))
        y_end = min(end, date(year, 12, 31))
        for item in _search_with_retry(client, bbox, y_start, y_end):
            day = item.properties.get("datetime", "")[:10]
            if day:
                by_date[day].append(item)
    return dict(sorted(by_date.items()))


def _best_item(items: list, bbox, geometry) -> tuple[object | None, float]:
    """
    Parmi les scènes d'une même date, celle où la parcelle est la mieux dégagée.

    Renvoie la scène NON signée : l'autorisation d'accès est demandée juste
    avant le téléchargement des bandes, pour qu'elle ne puisse pas expirer
    entre-temps.
    """
    best, best_ratio = None, -1.0
    for item in items:
        ratio = _parcel_valid_ratio(planetary_computer.sign(item), bbox, geometry)
        if ratio > best_ratio:
            best, best_ratio = item, ratio
    return best, best_ratio


def _ingest_date(db, parcel, item) -> bool:
    """
    Calcule et enregistre les indices d'une date, avec une autorisation
    d'accès fraîche. Si aucune bande n'a pu être lue (autorisation
    refusée, coupure), la date est retentée une fois après une pause.
    """
    for attempt in (1, 2):
        result = generate_all_indices_for_parcel(
            db=db,
            farm_id=parcel.farm_id,
            parcel_id=parcel.id,
            item=planetary_computer.sign(item),
            write_cogs=False,
        )
        if result and result.get("inserted"):
            return True
        if attempt == 1:
            time.sleep(RETRY_PAUSE_S)
    return False


# ----------------------------------------------------------------------
# Traitement d'une parcelle
# ----------------------------------------------------------------------


def _process_parcel(db, client: Client, parcel, start: date, end: date, dry_run: bool) -> dict:
    stats = {"dates": 0, "deja": 0, "nuageuses": 0, "inserees": 0, "echecs": 0}

    bbox = _get_bbox_for_parcel(db, parcel.id)
    if bbox is None:
        print("   pas de géométrie, ignorée")
        return stats
    geometry = _get_geometry_for_parcel(db, parcel.id)

    floor = _planting_floor(db, parcel.id)
    parcel_start = max(start, floor) if floor else start
    if floor:
        print(f"   plantation la plus ancienne : {floor.isoformat()}")
    print(f"   période : {parcel_start.isoformat()} → {end.isoformat()}")

    by_date = _candidates_by_date(client, bbox, parcel_start, end)
    known = _dates_in_base(db, parcel.id)
    todo = {d: items for d, items in by_date.items() if d not in known}

    stats["dates"] = len(by_date)
    stats["deja"] = len(by_date) - len(todo)
    print(f"   {len(by_date)} dates trouvées, {stats['deja']} déjà en base, {len(todo)} à traiter")

    if dry_run:
        return stats

    started = time.time()
    for i, (day, items) in enumerate(todo.items(), start=1):
        try:
            item, ratio = _best_item(items, bbox, geometry)
            if item is None or ratio < MIN_VALID_RATIO:
                stats["nuageuses"] += 1
            elif _ingest_date(db, parcel, item):
                stats["inserees"] += 1
            else:
                stats["echecs"] += 1
                print(f"   {day} : aucun indice calculé, à retenter à la prochaine relance")
        except Exception as e:
            db.rollback()
            stats["echecs"] += 1
            print(f"   {day} : échec ({type(e).__name__} : {e})")

        if i % 10 == 0 or i == len(todo):
            elapsed = (time.time() - started) / 60
            print(
                f"   {i}/{len(todo)} dates · {stats['inserees']} enregistrées · "
                f"{stats['nuageuses']} trop nuageuses · {elapsed:.1f} min"
            )

    return stats


# ----------------------------------------------------------------------
# Point d'entrée
# ----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--parcel-id", type=int, help="Traiter une seule parcelle")
    parser.add_argument(
        "--farm", action="append", default=[],
        help="Limiter aux parcelles de cette ferme (option répétable)",
    )
    parser.add_argument("--years", type=int, default=3, help="Années passées (défaut : 3)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compter les dates à traiter, sans rien télécharger ni enregistrer",
    )
    args = parser.parse_args()

    end = date.today()
    start = date(end.year - args.years, 1, 1)

    db = SessionLocal()
    try:
        parcels = _select_parcels(db, args.parcel_id, args.farm)
        if not parcels:
            print("Aucune parcelle trouvée.")
            return 1

        client = Client.open(STAC_API_URL)
        mode = "ESSAI, rien ne sera enregistré" if args.dry_run else "téléchargement"
        print(f"Historique {start.isoformat()} → {end.isoformat()} · {len(parcels)} parcelle(s) · {mode}\n")

        totals = defaultdict(int)
        failed: list[str] = []
        for parcel in parcels:
            print(f"[{parcel.farm}] {parcel.name} (id {parcel.id})")
            try:
                stats = _process_parcel(db, client, parcel, start, end, args.dry_run)
            except Exception as e:
                # Une parcelle en échec n'arrête pas les suivantes
                db.rollback()
                failed.append(f"{parcel.name} (id {parcel.id})")
                print(f"   ÉCHEC de la parcelle : {type(e).__name__} : {e}")
                print("   les dates déjà enregistrées sont conservées\n")
                continue
            for k, v in stats.items():
                totals[k] += v
            print()

        if args.dry_run:
            todo = totals["dates"] - totals["deja"]
            print(f"Total : {todo} dates à traiter, soit environ {todo * 12 / 3600:.1f} h de calcul.")
            print("Rien n'a été enregistré.")
        else:
            print(
                f"Terminé : {totals['inserees']} dates enregistrées, "
                f"{totals['nuageuses']} trop nuageuses, {totals['echecs']} échecs."
            )
        if failed:
            print(f"Parcelles interrompues : {', '.join(failed)}.")
            print("Relancer la même commande pour les reprendre : les dates déjà en base sont ignorées.")
            return 2
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except KeyboardInterrupt:
        print("\nInterrompu. Relancer la même commande pour reprendre.")
    finally:
        # Sortie immédiate : évite les erreurs de fermeture de rasterio et
        # GDAL sous Windows (« Error in sys.excepthook »), qui surviennent
        # après l'enregistrement et sont sans effet sur les données.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)