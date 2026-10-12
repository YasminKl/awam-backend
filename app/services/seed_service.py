"""
Import des fermes et parcelles STATIQUES depuis les fichiers GeoJSON
de `app/data/seed/`.

Flux, identique à celui d'une parcelle dessinée sur la carte
(POST /api/parcels), à une étape près :

    fichier GeoJSON (MultiPolygon à 1 polygone)
      → on sort le polygone de la liste          ← seule étape en plus
      → ST_GeomFromGeoJSON  : GeoJSON → geom PostGIS   (identique)
      → ST_Area             : surface réelle en ha     (identique)
      → analyse satellite si la parcelle est active    (identique)

Rien ne distingue ensuite une parcelle statique d'une parcelle dessinée.

Règles :
  - une culture "Sol nu" / "Aucune" ⇒ parcelle inactive, culture vide
    (pas d'analyse satellite tant qu'elle n'est pas activée) ;
  - un fichier contenant plusieurs polygones est REFUSÉ (on ne garde
    jamais un polygone en silence) ;
  - un polygone invalide (bords qui se croisent) est REFUSÉ ;
  - les analyses de laboratoire présentes dans le fichier (sol, eau) sont
    enregistrées dans parcel_analysis ;
  - le script est idempotent : relancé, il ne crée pas de doublons.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.parcel_analysis import ParcelAnalysis

logger = logging.getLogger(__name__)

# Dossier des fichiers statiques : app/data/seed/
SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"

# Nom affiché pour chaque sous-dossier
FARM_LABELS: dict[str, str] = {
    "makther": "Makthar",
    "sidi_mechreg": "Sidi Mechreg",
}

# Cultures qui signifient "rien de planté" → parcelle inactive
EMPTY_CULTURES = {"sol nu", "aucune", "aucun", "none", ""}

# Écart toléré entre la surface du fichier et celle calculée par PostGIS
AREA_TOLERANCE_PCT = 5.0


# ----------------------------------------------------------------------
# Lecture d'un fichier
# ----------------------------------------------------------------------


def _parcel_name_from_filename(path: Path) -> str:
    """
    Déduit le nom de la parcelle du nom de fichier, qui porte le numéro
    permettant de distinguer les homonymes (3 "Parcelle Kemel").

        AWAM_TERRAIN_makthar_P1_Boundaries_...              → "P1"
        AWAM_TERRAIN_sidi-mechreg_Parcelle_Kemel_2_Bound... → "Parcelle Kemel 2"
    """
    stem = path.name
    if "_Boundaries" in stem:
        stem = stem.split("_Boundaries")[0]
    if stem.startswith("AWAM_TERRAIN_"):
        stem = stem[len("AWAM_TERRAIN_"):]
    # Retire le préfixe du site (makthar, sidi-mechreg…)
    parts = stem.split("_", 1)
    if len(parts) == 2:
        stem = parts[1]
    return stem.replace("_", " ").strip()


def _extract_polygon(geometry: dict, source: str) -> dict:
    """
    Renvoie un Polygon GeoJSON, tel qu'en envoie le dessin sur la carte.

    Un MultiPolygon n'est accepté que s'il contient EXACTEMENT un polygone :
    on sort alors ce polygone de la liste, sans rien perdre. Au-delà, on
    refuse plutôt que de n'en garder qu'un en silence.
    """
    geom_type = geometry.get("type")

    if geom_type == "Polygon":
        return geometry

    if geom_type == "MultiPolygon":
        polygons = geometry.get("coordinates") or []
        if len(polygons) != 1:
            raise ValueError(
                f"{source} : le MultiPolygon contient {len(polygons)} polygones. "
                "Un seul est attendu ; corrigez le fichier avant l'import."
            )
        return {"type": "Polygon", "coordinates": polygons[0]}

    raise ValueError(f"{source} : géométrie {geom_type!r} non supportée (Polygon attendu).")


def _read_analysis(props: dict) -> dict | None:
    """
    Analyse de laboratoire éventuellement présente dans le fichier
    (sol et eau). Renvoie None si aucune valeur n'est renseignée.
    """
    analysis = {
        "soil_type": props.get("sol_texture"),
        "soil_ph": props.get("sol_ph"),
        "soil_humidity_pct": props.get("sol_humidite_pct"),
        "soil_organic_matter_pct": props.get("sol_matiere_organique_pct"),
        "soil_salinity_g_l": props.get("sol_salinite_g_L"),
        "water_ph": props.get("eau_ph"),
        "water_nitrates_mg_l": props.get("eau_nitrates_mg_L"),
        "notes": props.get("sol_diagnostic"),
    }
    return analysis if any(v is not None for v in analysis.values()) else None


def _read_seed_file(path: Path) -> dict:
    """Lit un fichier GeoJSON statique et en extrait ce qui va en base."""
    data = json.loads(path.read_text(encoding="utf-8"))

    features = data.get("features") if data.get("type") == "FeatureCollection" else [data]
    if not features:
        raise ValueError(f"{path.name} : aucune entité dans le fichier.")
    if len(features) > 1:
        raise ValueError(
            f"{path.name} : {len(features)} entités trouvées, une seule parcelle est attendue."
        )

    feature = features[0]
    props = feature.get("properties") or {}
    geometry = _extract_polygon(feature.get("geometry") or {}, path.name)

    culture_raw = (props.get("culture") or props.get("cult") or "").strip()
    is_empty = culture_raw.lower() in EMPTY_CULTURES

    return {
        "name": _parcel_name_from_filename(path),
        # "Sol nu" / "Aucune" ne sont pas des cultures : parcelle vide
        "culture_type": None if is_empty else culture_raw,
        "soil_type": props.get("sol_texture"),
        "status": "inactive" if is_empty else "active",
        "geometry": geometry,
        "area_ha_file": props.get("area_ha") or props.get("area_h"),
        "analysis": _read_analysis(props),
    }


# ----------------------------------------------------------------------
# Écriture en base
# ----------------------------------------------------------------------


def _get_or_create_farm(db: Session, user_id: int, name: str, parcels: list[dict]) -> int:
    """Crée la ferme si elle n'existe pas déjà pour cet utilisateur."""
    row = db.execute(
        text("SELECT id FROM farm WHERE user_id = :user_id AND name = :name"),
        {"user_id": user_id, "name": name},
    ).fetchone()
    if row:
        return row.id

    # Position de la ferme = centre de ses parcelles
    lons, lats = [], []
    for p in parcels:
        for ring in p["geometry"]["coordinates"]:
            for lon, lat in ring:
                lons.append(lon)
                lats.append(lat)

    row = db.execute(
        text("""
            INSERT INTO farm (user_id, name, location, latitude, longitude)
            VALUES (:user_id, :name, :location, :latitude, :longitude)
            RETURNING id
        """),
        {
            "user_id": user_id,
            "name": name,
            "location": name,
            "latitude": sum(lats) / len(lats) if lats else None,
            "longitude": sum(lons) / len(lons) if lons else None,
        },
    ).fetchone()
    logger.info("Ferme créée : %s (id=%s)", name, row.id)
    return row.id


def _insert_parcel(db: Session, farm_id: int, parcel: dict) -> int:
    """
    Insère une parcelle — mêmes requêtes SQL que POST /api/parcels :
    ST_GeomFromGeoJSON pour la géométrie, ST_Area pour la surface réelle.
    Le polygone est refusé s'il est invalide (bords qui se croisent).
    """
    geom_json = json.dumps(parcel["geometry"])

    invalid = db.execute(
        text("""
            SELECT ST_IsValidReason(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)) AS reason
            WHERE NOT ST_IsValid(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
        """),
        {"geom": geom_json},
    ).fetchone()
    if invalid:
        raise ValueError(f"{parcel['name']} : polygone invalide ({invalid.reason}).")

    is_active = parcel["status"] == "active"
    row = db.execute(
        text("""
            INSERT INTO parcel (
                farm_id, name, geom, culture_type, soil_type, irrigation_type,
                area_ha, status, raster_status
            )
            VALUES (
                :farm_id, :name,
                ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326),
                :culture_type, :soil_type, NULL,
                ST_Area(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)::geography) / 10000.0,
                :status, :raster_status
            )
            RETURNING id, area_ha
        """),
        {
            "farm_id": farm_id,
            "name": parcel["name"],
            "geom": geom_json,
            "culture_type": parcel["culture_type"],
            "soil_type": parcel["soil_type"],
            "status": parcel["status"],
            "raster_status": "pending" if is_active else "none",
        },
    ).fetchone()

    # Contrôle : la surface calculée doit correspondre à celle du fichier,
    # ce qui confirme qu'aucune délimitation n'a été perdue.
    expected = parcel.get("area_ha_file")
    if expected:
        ecart = abs(row.area_ha - expected) / expected * 100
        if ecart > AREA_TOLERANCE_PCT:
            logger.warning(
                "%s : surface calculée %.4f ha vs fichier %.4f ha (écart %.1f %%)",
                parcel["name"], row.area_ha, expected, ecart,
            )

    return row.id


# ----------------------------------------------------------------------
# Point d'entrée
# ----------------------------------------------------------------------


def seed_static_farms(db: Session, user_id: int, launch_analysis: bool = True) -> dict:
    """
    Importe les fermes et parcelles statiques pour un utilisateur.

    Relançable sans risque : les fermes et parcelles déjà présentes sont
    ignorées, jamais dupliquées ni écrasées.

    Returns:
        dict {farms: [...], parcels_created: n, parcels_skipped: n, errors: [...]}
    """
    summary: dict = {"farms": [], "parcels_created": 0, "parcels_skipped": 0, "errors": []}

    if not SEED_DIR.is_dir():
        summary["errors"].append(f"Dossier introuvable : {SEED_DIR}")
        return summary

    to_analyse: list[int] = []

    for folder in sorted(p for p in SEED_DIR.iterdir() if p.is_dir()):
        farm_name = FARM_LABELS.get(folder.name, folder.name.replace("_", " ").title())

        parcels: list[dict] = []
        for path in sorted(folder.glob("*.geojson")):
            # "_ALL_" regroupe des parcelles déjà présentes dans leurs
            # propres fichiers : l'importer créerait des doublons.
            if "_ALL_" in path.name:
                continue
            try:
                parcels.append(_read_seed_file(path))
            except Exception as e:
                logger.error("Fichier ignoré (%s) : %s", path.name, e)
                summary["errors"].append(str(e))

        if not parcels:
            continue

        farm_id = _get_or_create_farm(db, user_id, farm_name, parcels)
        created = 0

        for parcel in parcels:
            existing = db.execute(
                text("SELECT id FROM parcel WHERE farm_id = :farm_id AND name = :name"),
                {"farm_id": farm_id, "name": parcel["name"]},
            ).fetchone()
            if existing:
                summary["parcels_skipped"] += 1
                continue

            try:
                parcel_id = _insert_parcel(db, farm_id, parcel)
                if parcel["analysis"]:
                    db.add(ParcelAnalysis(parcel_id=parcel_id, **parcel["analysis"]))
            except Exception as e:
                logger.error("Parcelle ignorée (%s) : %s", parcel["name"], e)
                summary["errors"].append(str(e))
                continue

            created += 1
            summary["parcels_created"] += 1
            if parcel["status"] == "active":
                to_analyse.append(parcel_id)

        summary["farms"].append({"id": farm_id, "name": farm_name, "parcels_created": created})

    db.commit()

    # Analyse satellite des parcelles actives, comme pour une parcelle dessinée
    if launch_analysis and to_analyse:
        from app.api.routes.parcels import _launch_ingestion_tasks

        for parcel_id in to_analyse:
            _launch_ingestion_tasks(parcel_id)

    return summary