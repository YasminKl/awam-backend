"""
Vérification, en LECTURE SEULE, des sources climatiques Copernicus (ECMWF).
Rien n'est enregistré en base.

Pour un ou plusieurs lieux, le script vérifie :
  1. que la clé Copernicus fonctionne ;
  2. que chaque variable de la configuration est réellement disponible :
       - ERA5, le climat réel ;
       - CMIP6, les projections, pour chaque modèle et chaque scénario ;
  3. la valeur obtenue au lieu, en unités lisibles (°C, mm).

Pour chaque demande : OK, ou ÉCHEC avec la raison (nom de variable ou de
modèle refusé, licence à accepter…).

Aucune valeur n'est écrite en dur : variables, modèles, scénarios, période
et zone viennent de app/core/climate_config.py, réglable dans .env. Les
options ci-dessous permettent de tester autre chose, ponctuellement.

Prérequis :
  - CDSAPI_URL et CDSAPI_KEY dans le fichier .env du back ;
  - les licences ERA5 et CMIP6 acceptées sur le site Copernicus ;
  - pip install cdsapi netCDF4

Usage, depuis la racine du backend :

    python -m app.scripts.check_climate_sources --farm "Makthar"
    python -m app.scripts.check_climate_sources --all-farms --only era5
    python -m app.scripts.check_climate_sources --lat 35.85 --lon 9.20
    python -m app.scripts.check_climate_sources --farm "Makthar" --only cmip6 --models ec_earth3_cc mpi_esm1_2_lr
    python -m app.scripts.check_climate_sources --farm "Makthar" --scenarios ssp5_8_5 --cmip6-year 2040 --months 1 4 7 10

Les demandes passent par une file d'attente chez Copernicus : une demande
peut prendre de quelques secondes à plusieurs minutes, surtout pour CMIP6.
"""

import argparse
import sys
import tempfile
from pathlib import Path

from app.core import climate_config as cfg
from app.services.climate_sources import cmip6_request, era5_request, make_client, retrieve_values

#: Mois testés par défaut : un mois d'hiver et un mois d'été
DEFAULT_MONTHS = [1, 7]


def check(client, label: str, dataset: str, request: dict, file_names: tuple, convert: str, sea: bool,
          unit: str, lat: float, lon: float, workdir: Path) -> bool:
    print(f"\n▶ {label}")
    try:
        values = retrieve_values(client, dataset, request, file_names, convert, sea, lat, lon, workdir)
    except Exception as exc:  # noqa: BLE001 — on veut la raison, quelle qu'elle soit
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        print(f"  ✗ ÉCHEC : {message}")
        if "licence" in message.lower() or "license" in message.lower():
            print("    → Acceptez la licence de ce jeu de données sur le site Copernicus, puis relancez.")
        return False
    for year, month, value in values:
        print(f"  ✓ {year}-{month:02d} : {value:.1f} {unit}")
    return True


def places(args) -> list[tuple[str, float, float]]:
    """Les lieux à vérifier : une ferme, toutes les fermes, ou un point."""
    if args.lat is not None:
        if args.lon is None:
            sys.exit("--lat demande aussi --lon")
        return [("point choisi", args.lat, args.lon)]

    from app.db.database import SessionLocal
    from app.models.farm import Farm

    db = SessionLocal()
    try:
        query = db.query(Farm)
        if args.farm:
            query = query.filter(Farm.name.ilike(args.farm))
        farms = query.order_by(Farm.name).all()
    finally:
        db.close()
    if not farms:
        sys.exit(f"Ferme « {args.farm} » introuvable." if args.farm else "Aucune ferme en base.")
    result = []
    for farm in farms:
        if farm.latitude is None or farm.longitude is None:
            print(f"⚠ Ferme « {farm.name} » ignorée : pas de position. Renseignez son lieu.")
            continue
        result.append((f"ferme « {farm.name} »", farm.latitude, farm.longitude))
    if not result:
        sys.exit("Aucune ferme avec une position : utilisez --lat et --lon.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Vérifie les sources climatiques Copernicus.")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--farm", help="Nom d'une ferme d'AWAM (sa position est lue en base)")
    where.add_argument("--all-farms", action="store_true", help="Toutes les fermes qui ont une position")
    where.add_argument("--lat", type=float, help="Latitude d'un point (avec --lon)")
    parser.add_argument("--lon", type=float, help="Longitude d'un point (avec --lat)")
    parser.add_argument("--only", choices=["era5", "cmip6"], help="Ne vérifier qu'une source")
    parser.add_argument("--models", nargs="+", default=cfg.MODELS, help="Modèles CMIP6 (défaut : configuration)")
    parser.add_argument("--scenarios", nargs="+", default=cfg.SCENARIOS, help="Scénarios (défaut : configuration)")
    parser.add_argument("--era5-year", type=int, default=cfg.START_YEAR, help="Année ERA5 testée (défaut : début de la période)")
    parser.add_argument("--cmip6-year", type=int, default=cfg.middle_year(), help="Année CMIP6 testée (défaut : milieu de la période)")
    parser.add_argument("--months", nargs="+", type=int, default=DEFAULT_MONTHS, help="Mois testés, de 1 à 12")
    args = parser.parse_args()

    if any(m < 1 or m > 12 for m in args.months):
        parser.error("--months : des mois de 1 à 12")

    try:
        client = make_client()
    except RuntimeError as exc:
        sys.exit(str(exc))

    print(f"Configuration : période {cfg.START_YEAR}-{cfg.END_YEAR} · scénarios {', '.join(args.scenarios)} "
          f"· modèles {', '.join(args.models)} · zone ±{cfg.AREA_MARGIN}°")

    results: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for name, lat, lon in places(args):
            print(f"\n{'═' * 60}\nLieu : {name} · latitude {lat:.4f}, longitude {lon:.4f}")
            for var in cfg.CLIMATE_VARIABLES:
                if args.only in (None, "era5") and var.era5:
                    dataset, request = era5_request(var, args.era5_year, args.months, lat, lon)
                    label = f"{name} · ERA5 {args.era5_year} · {var.label}"
                    results.append((label, check(client, label, dataset, request, var.era5_file, var.era5_convert,
                                                 var.sea, var.unit, lat, lon, workdir)))
                if args.only in (None, "cmip6"):
                    for scenario in args.scenarios:
                        for model in args.models:
                            dataset, request = cmip6_request(var, model, scenario, args.cmip6_year, args.months, lat, lon)
                            label = f"{name} · CMIP6 {model} {scenario} {args.cmip6_year} · {var.label}"
                            results.append((label, check(client, label, dataset, request, var.cmip6_file,
                                                         var.cmip6_convert, var.sea, var.unit, lat, lon, workdir)))

    failed = [label for label, ok in results if not ok]
    print(f"\n{'─' * 60}\nDisponibles : {len(results) - len(failed)} / {len(results)}")
    for label in failed:
        print(f"  ✗ {label}")
    if results and not failed:
        print("Toutes les sources sont disponibles : on peut construire la projection dessus.")


if __name__ == "__main__":
    main()