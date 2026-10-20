"""
Remet en cycle les cultures pérennes bloquées en statut `harvested`.

L'ancien code passait TOUTE culture récoltée en `harvested`, y compris
les pérennes. Or une pérenne récoltée reste en place : son statut doit
rester `active` pour que la récolte suivante soit calculée. Les lignes
déjà écrites en base gardent l'ancien statut et ne reboucleront pas
toutes seules : ce script les corrige, une fois.

Les cultures ANNUELLES sont laissées telles quelles : chez elles, le
statut `harvested` est correct, le cycle est bien terminé.

La date de récolte prévue n'est pas écrite ici : une fois le statut
remis à `active`, elle est recalculée automatiquement à la première
lecture de la culture (_refresh_cycle dans app/api/routes/crops.py).

Par défaut le script n'écrit RIEN : il affiche ce qu'il ferait.
Ajouter --apply pour appliquer.

Usage, depuis la racine du backend :

    python -m app.scripts.unstick_perennial_crops           # simulation
    python -m app.scripts.unstick_perennial_crops --apply   # applique
"""

import argparse
import sys

from sqlalchemy import text

from app.db.database import SessionLocal
from app.services.agronomy.calendar import next_harvest_after
from app.services.agronomy.species import is_annual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="applique la correction (sans cette option, simulation seule)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT id, species, actual_harvest_date, expected_harvest_date "
                "FROM crop WHERE status = 'harvested' ORDER BY species, id"
            )
        ).fetchall()

        targets = [r for r in rows if not is_annual(r.species)]

        print(f"Cultures en statut 'harvested' : {len(rows)}")
        print(f"  dont perennes a corriger     : {len(targets)}\n")

        if not targets:
            print("Rien a faire.")
            return 0

        for row in targets:
            if row.actual_harvest_date:
                suivante = next_harvest_after(row.species, row.actual_harvest_date)
                apercu = suivante or "aucune periode de recolte connue"
            else:
                apercu = "pas de date de recolte reelle : recalcul a la lecture"
            print(
                f"  #{row.id:<5} {row.species:<14} "
                f"recoltee le {row.actual_harvest_date} "
                f"-> harvested devient active, prochaine recolte : {apercu}"
            )

        if not args.apply:
            print(
                "\nSimulation : aucune ecriture effectuee."
                "\nRelancer avec --apply pour appliquer."
            )
            return 0

        ids = [row.id for row in targets]
        db.execute(
            text("UPDATE crop SET status = 'active' WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        db.commit()
        print(f"\n{len(ids)} culture(s) remise(s) en cycle.")
        print(
            "La date de recolte prevue sera recalculee a la premiere "
            "lecture de chaque culture."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())