"""
Prépare la projection climatique d'une ferme TOUT DE SUITE, sans passer
par Celery, en affichant l'avancement : pour valider l'installation, ou
pour relancer un lieu à la main.

Le lieu étudié de la ferme est créé s'il n'existe pas encore, puis la même
préparation que la tâche Celery « climate.prepare_site » est lancée. Elle
peut être relancée sans risque : rien n'est écrit en double.

Usage, depuis la racine du backend :

    python -m app.scripts.prepare_climate_site --farm "Makthar"

Pour une première validation plus rapide, réduisez les modèles et les
scénarios le temps du test, dans le terminal (PowerShell) :

    $env:CLIMATE_MODELS="ec_earth3_cc"; $env:CLIMATE_SCENARIOS="ssp2_4_5"
    python -m app.scripts.prepare_climate_site --farm "Makthar"

Copernicus a une file d'attente : comptez de quelques minutes à plusieurs
heures selon l'affluence et le nombre de modèles.
"""

import argparse
import logging
import sys

from app.core import climate_config as cfg
from app.db.database import SessionLocal
from app.models.climate import ClimateSite
from app.models.farm import Farm
from app.services import climate_projection_service as climate
from app.services.climate_lock import climate_lock


def site_for_farm(db, farm: Farm) -> ClimateSite:
    """Le lieu étudié de la ferme, créé s'il n'existe pas encore."""
    site = db.query(ClimateSite).filter(ClimateSite.farm_id == farm.id).first()
    if site:
        return site
    points = climate.weather.farm_coordinates(db, farm.id)
    if not points:
        sys.exit(f"La ferme « {farm.name} » n'a ni position ni parcelle : impossible de situer son climat.")
    site = ClimateSite(
        user_id=farm.user_id, farm_id=farm.id, name=farm.name,
        latitude=points[0]["latitude"], longitude=points[0]["longitude"],
    )
    db.add(site)
    db.commit()
    return site


def run(farm_name: str, client=None) -> dict:
    db = SessionLocal()
    try:
        farm = db.query(Farm).filter(Farm.name.ilike(farm_name)).first()
        if not farm:
            sys.exit(f"Ferme « {farm_name} » introuvable.")
        site = site_for_farm(db, farm)
        print(f"Lieu étudié n° {site.id} · {site.name} · {site.latitude}, {site.longitude}")
        print(f"Période {cfg.START_YEAR}-{cfg.END_YEAR} · référence {cfg.BASELINE_START}-{cfg.BASELINE_END}"
              f" · modèles {', '.join(cfg.MODELS)} · scénarios {', '.join(cfg.SCENARIOS)}")
        # Une seule préparation à la fois, comme pour Celery (limite de Copernicus)
        with climate_lock() as acquired:
            if not acquired:
                sys.exit("Une préparation climatique est déjà en cours (worker ou autre terminal) : réessayez plus tard.")
            summary = climate.prepare_site(db, site.id, client=client)
        db.refresh(site)
        print(f"\n✅ Terminé · état « {site.status} » · {summary}")
        return summary
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ Échec : {exc}")
        raise SystemExit(1)
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(description="Prépare la projection climatique d'une ferme.")
    parser.add_argument("--farm", required=True, help="Nom de la ferme")
    run(parser.parse_args().farm)


if __name__ == "__main__":
    main()