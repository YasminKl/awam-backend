"""
Remplit l'historique météo (table weather_daily) sur une période.

Usage, depuis la racine du backend :

    python -m app.scripts.backfill_weather                         # depuis le 1er janvier 2026
    python -m app.scripts.backfill_weather --start 2025-09-01      # autre date de début
    python -m app.scripts.backfill_weather --farm-id 8             # une seule ferme

Relançable sans risque : les jours déjà présents sont mis à jour, jamais
dupliqués. La tâche quotidienne prend ensuite le relais.
"""

import argparse
import logging
import sys
from datetime import date, timedelta

from app.db.database import SessionLocal
from app.services.weather_history_service import sync_all_farms

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

DEFAULT_START = date(2026, 1, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Remplit l'historique météo.")
    parser.add_argument(
        "--start", type=date.fromisoformat, default=DEFAULT_START,
        help="Premier jour, au format AAAA-MM-JJ (par défaut : 2026-01-01).",
    )
    parser.add_argument(
        "--end", type=date.fromisoformat, default=None,
        help="Dernier jour, au format AAAA-MM-JJ (par défaut : hier).",
    )
    parser.add_argument(
        "--farm-id", type=int, default=None,
        help="Ne traiter qu'une ferme (par défaut : toutes).",
    )
    args = parser.parse_args()

    end = args.end or (date.today() - timedelta(days=1))

    db = SessionLocal()
    try:
        summary = sync_all_farms(db, args.start, end, farm_id=args.farm_id)
    finally:
        db.close()

    print(
        f"🌦️ Historique météo du {summary['start']} au {summary['end']} : "
        f"{summary['ok']} ferme(s) OK, {summary['failed']} en échec, "
        f"{summary['days_written']} jours enregistrés."
    )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())