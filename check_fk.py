"""
Vérifie si les bandes Sentinel-2 chargées par AWAM contiennent le décalage
de réflectance introduit par l'ESA avec la version de traitement 04.00
(janvier 2022) : réflectance = (valeur brute - 1000) / 10 000.

LECTURE SEULE : aucune écriture en base ni sur B2.

Usage, depuis la racine du backend :

    python -m app.scripts.check_reflectance_offset                 # 1re parcelle active
    python -m app.scripts.check_reflectance_offset --parcel-id 70
"""

import argparse
import sys

import numpy as np
from sqlalchemy import text

from app.db.database import SessionLocal
from app.services.sentinel_service import (
    _clip_band_to_bbox,
    _get_bbox_for_parcel,
    _select_usable_scene,
)

OFFSET = 1000.0
#: Marge autour de la parcelle (~2,5 km) pour trouver des surfaces sombres
MARGIN_DEG = 0.025


def _first_active_parcel(db) -> int | None:
    row = db.execute(
        text("SELECT id FROM parcel WHERE status = 'active' ORDER BY id LIMIT 1")
    ).fetchone()
    return row.id if row else None


def _raw_band(item, band: str, bbox) -> np.ndarray:
    """Valeurs brutes de la bande, sans aucune conversion (comme le code actuel)."""
    da = _clip_band_to_bbox(item, band, bbox, target_resolution=None)
    arr = da.values[0].astype("float64")
    arr[arr <= 0] = np.nan  # 0 = absence de donnée
    return arr


def _median_ndvi(nir: np.ndarray, red: np.ndarray) -> float:
    with np.errstate(divide="ignore", invalid="ignore"):
        return float(np.nanmedian((nir - red) / (nir + red)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Vérifie le décalage Sentinel-2.")
    parser.add_argument("--parcel-id", type=int, default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        parcel_id = args.parcel_id or _first_active_parcel(db)
        if parcel_id is None:
            print("❌ Aucune parcelle active.")
            return 1

        bbox = _get_bbox_for_parcel(db, parcel_id)
        if bbox is None:
            print(f"❌ Parcelle {parcel_id} sans géométrie.")
            return 1

        item, ratio = _select_usable_scene(db, parcel_id, bbox)
        if item is None:
            print(f"❌ Aucune scène exploitable pour la parcelle {parcel_id}.")
            return 1

        baseline = item.properties.get("s2:processing_baseline")
        print(f"\nParcelle           : {parcel_id}")
        print(f"Scène              : {item.id}")
        print(f"Date               : {str(item.datetime)[:10]}")
        print(f"Version traitement : {baseline}")
        print(f"Métadonnées B04    : {item.assets['B04'].extra_fields.get('raster:bands')}")

        # 1. Valeurs brutes minimales autour de la parcelle
        west, south, east, north = bbox
        wide = (west - MARGIN_DEG, south - MARGIN_DEG, east + MARGIN_DEG, north + MARGIN_DEG)
        blue = _raw_band(item, "B02", wide)
        red_wide = _raw_band(item, "B04", wide)
        min_blue = float(np.nanmin(blue))
        print(f"\nZone de ~5 km autour de la parcelle :")
        print(f"  B02 (bleu)  min = {min_blue:.0f}   1er centile = {np.nanpercentile(blue, 1):.0f}")
        print(f"  B04 (rouge) min = {np.nanmin(red_wide):.0f}   1er centile = {np.nanpercentile(red_wide, 1):.0f}")

        # 2. NDVI de la parcelle avec et sans correction
        red = _raw_band(item, "B04", bbox)
        nir = _raw_band(item, "B08", bbox)
        ndvi_now = _median_ndvi(nir, red)
        ndvi_fixed = _median_ndvi(nir - OFFSET, red - OFFSET)

        stored = db.execute(
            text("""
                SELECT median_value FROM indice_reading
                WHERE parcel_id = :pid AND indice_name = 'NDVI' AND scene_id = :sid
            """),
            {"pid": parcel_id, "sid": item.id},
        ).fetchone()

        print(f"\nNDVI médian (rectangle englobant la parcelle) :")
        print(f"  calcul actuel, valeurs brutes   : {ndvi_now:.3f}")
        print(f"  avec correction du décalage     : {ndvi_fixed:.3f}")
        if stored and stored.median_value is not None:
            print(f"  valeur médiane en base (parcelle seule) : {stored.median_value:.3f}")

        # 3. Verdict
        print()
        try:
            recent = baseline is not None and float(baseline) >= 4.0
        except ValueError:
            recent = False

        if recent and min_blue >= 900:
            print("⚠️  DÉCALAGE PRÉSENT : aucune valeur ne descend sous ~1000.")
            print("    Les indices sont actuellement calculés sur des valeurs décalées.")
        elif min_blue < 900:
            print("✅ Des valeurs descendent nettement sous 1000 : pas de décalage")
            print("    dans les données chargées. Le calcul actuel est correct sur ce point.")
        else:
            print("❓ Résultat ambigu : envoie-moi tout ce qui est affiché ci-dessus.")
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())