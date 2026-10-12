"""
Script d'import des fermes et parcelles statiques.

Usage, depuis la racine du backend :

    python -m app.scripts.seed_static_farms                  # compte admin
    python -m app.scripts.seed_static_farms --email a@b.tn   # autre compte
    python -m app.scripts.seed_static_farms --no-analysis    # sans analyse satellite

Relançable sans risque : les fermes et parcelles déjà importées sont ignorées.
"""

import argparse
import logging
import sys

from sqlalchemy import func

from app.core.config import ADMIN_EMAIL
from app.db.database import SessionLocal
from app.models.utilisateur import User
from app.services.seed_service import seed_static_farms

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Importe les fermes et parcelles statiques.")
    parser.add_argument(
        "--email",
        help="E-mail du compte propriétaire (par défaut : ADMIN_EMAIL du .env).",
    )
    parser.add_argument(
        "--no-analysis",
        action="store_true",
        help="N'enclenche pas l'analyse satellite des parcelles actives.",
    )
    args = parser.parse_args()

    email = (args.email or ADMIN_EMAIL or "").strip().lower()
    if not email:
        print("❌ Aucun e-mail fourni et ADMIN_EMAIL n'est pas défini dans .env.")
        return 1

    db = SessionLocal()
    try:
        user = db.query(User).filter(func.lower(User.email) == email).first()
        if not user:
            print(f"❌ Aucun compte trouvé pour {email}. Créez-le avant l'import.")
            return 1

        print(f"🌱 Import des fermes statiques pour {user.email}...")
        summary = seed_static_farms(db, user.id, launch_analysis=not args.no_analysis)

        for farm in summary["farms"]:
            print(f"   • {farm['name']} : {farm['parcels_created']} parcelle(s) créée(s)")

        print(
            f"✅ {summary['parcels_created']} parcelle(s) créée(s), "
            f"{summary['parcels_skipped']} déjà présente(s)."
        )

        if summary["errors"]:
            print(f"⚠️  {len(summary['errors'])} fichier(s) ignoré(s) :")
            for err in summary["errors"]:
                print(f"   - {err}")
            return 1

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())