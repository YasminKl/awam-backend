"""
Vérifie l'historique Sentinel-2 disponible pour les parcelles, AVANT de
lancer un téléchargement de plusieurs années. Ne modifie rien en base.

Pour chaque parcelle et chaque année, le script interroge Planetary
Computer — le même catalogue que la tâche quotidienne — et affiche :

  - le nombre de scènes trouvées sous le plafond de nuages ;
  - avec --parcel-check : le nombre de scènes où la PARCELLE elle-même
    est dégagée à au moins MIN_VALID_RATIO (60 %), c'est-à-dire celles
    qui seraient réellement retenues. Ce contrôle télécharge la bande
    SCL de chaque scène : il est plus lent, à réserver à une parcelle.

Usage, depuis la racine du backend :

    python -m app.scripts.check_history_availability --parcel-id 13
    python -m app.scripts.check_history_availability --parcel-id 13 --parcel-check
    python -m app.scripts.check_history_availability --years 3      # toutes les parcelles actives
"""

import argparse
import sys
from collections import Counter
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
)


def _active_parcels(db, parcel_id: int | None) -> list:
    """Parcelles à vérifier : une seule, ou toutes les parcelles actives."""
    query = """
        SELECT p.id, p.name, f.name AS farm
        FROM parcel p JOIN farm f ON f.id = p.farm_id
        WHERE p.geom IS NOT NULL
    """
    params: dict = {}
    if parcel_id is not None:
        query += " AND p.id = :pid"
        params["pid"] = parcel_id
    else:
        query += " AND p.status = 'active'"
    query += " ORDER BY f.name, p.name"
    return db.execute(text(query), params).fetchall()


def _search_year(client: Client, bbox, year: int) -> list:
    """Scènes d'une année civile sous le plafond de nuages, la plus ancienne d'abord."""
    end = min(date(year, 12, 31), date.today())
    search = client.search(
        collections=[COLLECTION],
        bbox=bbox,
        datetime=f"{year}-01-01/{end.isoformat()}",
        query={"eo:cloud_cover": {"lt": MAX_CLOUD_COVER}},
    )
    items = list(search.items())
    items.sort(key=lambda it: it.properties.get("datetime", ""))
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--parcel-id", type=int, help="Vérifier une seule parcelle")
    parser.add_argument("--years", type=int, default=3, help="Nombre d'années passées (défaut : 3)")
    parser.add_argument(
        "--parcel-check",
        action="store_true",
        help="Compter aussi les scènes où la parcelle est dégagée (plus lent)",
    )
    args = parser.parse_args()

    current = date.today().year
    years = list(range(current - args.years, current + 1))

    db = SessionLocal()
    try:
        parcels = _active_parcels(db, args.parcel_id)
        if not parcels:
            print("Aucune parcelle trouvée.")
            return 1

        client = Client.open(STAC_API_URL)
        print(f"Catalogue : {STAC_API_URL} ({COLLECTION})")
        print(f"Années : {years[0]} à {years[-1]} · plafond de nuages : {MAX_CLOUD_COVER} %")
        if args.parcel_check:
            print(f"Parcelle exploitable : au moins {MIN_VALID_RATIO:.0%} dégagée\n")
        else:
            print()

        grand_total = 0
        for p in parcels:
            bbox = _get_bbox_for_parcel(db, p.id)
            if bbox is None:
                print(f"[{p.farm}] {p.name} : pas de géométrie, ignorée")
                continue
            geometry = _get_geometry_for_parcel(db, p.id) if args.parcel_check else None

            print(f"[{p.farm}] {p.name} (id {p.id})")
            for year in years:
                try:
                    items = _search_year(client, bbox, year)
                except Exception as e:  # réseau, catalogue indisponible
                    print(f"   {year} : erreur de recherche ({e})")
                    continue

                line = f"   {year} : {len(items):3d} scènes"
                if items:
                    first = items[0].properties.get("datetime", "")[:10]
                    last = items[-1].properties.get("datetime", "")[:10]
                    months = Counter(it.properties.get("datetime", "")[5:7] for it in items)
                    line += f"  ({first} → {last}, {len(months)} mois couverts)"

                if args.parcel_check and items:
                    usable = 0
                    for it in items:
                        ratio = _parcel_valid_ratio(planetary_computer.sign(it), bbox, geometry)
                        if ratio >= MIN_VALID_RATIO:
                            usable += 1
                    line += f"  → {usable} exploitables sur la parcelle"
                    grand_total += usable
                else:
                    grand_total += len(items)

                print(line)
            print()

        label = "scènes exploitables" if args.parcel_check else "scènes trouvées"
        print(f"Total : {grand_total} {label}.")
        print("Rien n'a été modifié en base.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())