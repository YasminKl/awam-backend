"""
Historique météo quotidien par ferme, à partir d'Open-Meteo.

Deux sources, selon l'ancienneté des jours demandés :
  - l'archive (réanalyse ERA5), définitive mais publiée avec environ
    5 jours de retard : utilisée pour les jours anciens ;
  - l'API de prévision, qui conserve aussi les jours passés récents :
    utilisée pour les derniers jours, puis remplacée par l'archive.

Les lignes sont mises à jour en place (une ligne par ferme et par jour),
on peut donc relancer une synchronisation sans risque de doublon.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

#: En deçà de ce nombre de jours, l'archive n'est pas encore publiée
ARCHIVE_DELAY_DAYS = 6

HTTP_TIMEOUT_S = 30.0

#: Variable Open-Meteo → colonne de la table weather_daily
DAILY_VARIABLES: dict[str, str] = {
    "temperature_2m_min": "temperature_min_c",
    "temperature_2m_max": "temperature_max_c",
    "precipitation_sum": "precipitation_mm",
    "et0_fao_evapotranspiration": "et0_mm",
    "wind_speed_10m_max": "wind_speed_max_kmh",
    "shortwave_radiation_sum": "radiation_mj_m2",
}


# ======================================================================
# Coordonnées des fermes
# ======================================================================


def farm_coordinates(db: Session, farm_id: int | None = None) -> list[dict]:
    """
    Point à interroger pour chaque ferme : ses coordonnées si elles sont
    renseignées, sinon le centre de l'ensemble de ses parcelles.
    Les fermes sans coordonnées ni parcelle sont ignorées.
    """
    rows = db.execute(
        text("""
            SELECT f.id, f.name,
                   COALESCE(f.latitude,  ST_Y(ST_Centroid(ST_Collect(p.geom)))) AS lat,
                   COALESCE(f.longitude, ST_X(ST_Centroid(ST_Collect(p.geom)))) AS lon
            FROM farm f
            LEFT JOIN parcel p ON p.farm_id = f.id
            WHERE (CAST(:farm_id AS INTEGER) IS NULL OR f.id = :farm_id)
            GROUP BY f.id, f.name, f.latitude, f.longitude
            ORDER BY f.id
        """),
        {"farm_id": farm_id},
    ).fetchall()

    farms = []
    for r in rows:
        if r.lat is None or r.lon is None:
            logger.warning(
                "🌦️ [météo] Ferme %s (%s) ignorée : ni coordonnées ni parcelle",
                r.id, r.name,
            )
            continue
        farms.append({
            "id": r.id,
            "name": r.name,
            "latitude": round(float(r.lat), 4),
            "longitude": round(float(r.lon), 4),
        })
    return farms


# ======================================================================
# Appel Open-Meteo
# ======================================================================


#: Une demande à Open-Meteo couvre au plus une année : une longue période
#: (ex. 20 ans pour la projection climatique) est découpée en morceaux,
#: pour que chaque réponse reste rapide
MAX_DAYS_PER_REQUEST = 366

#: Tentatives par morceau, et pauses (s) entre elles, en cas de lenteur,
#: de coupure ou de surcharge passagère d'Open-Meteo (5xx, 429)
FETCH_ATTEMPTS = 3
RETRY_PAUSES_S = (2.0, 5.0)


def _is_temporary(exc: Exception) -> bool:
    """Erreur passagère : lenteur, coupure réseau, serveur surchargé."""
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


def _fetch_range(url: str, params: dict) -> dict:
    """Une demande à Open-Meteo, avec de nouvelles tentatives si l'erreur est passagère."""
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_S) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
            return response.json().get("daily") or {}
        except Exception as exc:  # noqa: BLE001 — on trie ci-dessous
            if attempt == FETCH_ATTEMPTS - 1 or not _is_temporary(exc):
                raise
            pause = RETRY_PAUSES_S[min(attempt, len(RETRY_PAUSES_S) - 1)]
            logger.warning(
                "🌦️ [météo] Open-Meteo indisponible (%s), nouvelle tentative dans %.0f s",
                exc.__class__.__name__, pause,
            )
            time.sleep(pause)
    return {}


def fetch_daily(
    latitude: float, longitude: float, start: date, end: date, *, archive: bool
) -> list[dict]:
    """
    Récupère la météo jour par jour entre `start` et `end` inclus.

    Une longue période est demandée par morceaux d'une année au plus, et
    chaque morceau est retenté en cas d'erreur passagère d'Open-Meteo.

    Returns:
        une liste de dicts {day, <colonnes de la table>}. Les jours sans
        aucune valeur (pas encore publiés) sont écartés.
    """
    url = ARCHIVE_URL if archive else FORECAST_URL
    rows = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(end, chunk_start + timedelta(days=MAX_DAYS_PER_REQUEST - 1))
        daily = _fetch_range(url, {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": chunk_start.isoformat(),
            "end_date": chunk_end.isoformat(),
            "daily": ",".join(DAILY_VARIABLES),
            "timezone": "auto",
        })
        days = daily.get("time") or []
        for i, day_str in enumerate(days):
            row = {"day": date.fromisoformat(day_str)}
            for variable, column in DAILY_VARIABLES.items():
                values = daily.get(variable) or []
                row[column] = values[i] if i < len(values) else None

            if all(row[c] is None for c in DAILY_VARIABLES.values()):
                continue
            rows.append(row)
        chunk_start = chunk_end + timedelta(days=1)
    return rows


# ======================================================================
# Écriture en base
# ======================================================================


def upsert_days(
    db: Session, farm: dict, rows: list[dict], source: str
) -> int:
    """
    Insère ou met à jour les jours d'une ferme. Une valeur absente dans
    la nouvelle réponse n'efface jamais une valeur déjà connue.
    """
    columns = list(DAILY_VARIABLES.values())
    updates = ",\n".join(
        f"{c} = COALESCE(EXCLUDED.{c}, weather_daily.{c})" for c in columns
    )

    statement = text(f"""
        INSERT INTO weather_daily (
            farm_id, day, {", ".join(columns)},
            latitude, longitude, source
        )
        VALUES (
            :farm_id, :day, {", ".join(":" + c for c in columns)},
            :latitude, :longitude, :source
        )
        ON CONFLICT (farm_id, day) DO UPDATE SET
            {updates},
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            source = EXCLUDED.source,
            updated_at = CURRENT_TIMESTAMP
    """)

    for row in rows:
        db.execute(statement, {
            **row,
            "farm_id": farm["id"],
            "latitude": farm["latitude"],
            "longitude": farm["longitude"],
            "source": source,
        })
    return len(rows)


# ======================================================================
# Synchronisation
# ======================================================================


def sync_farm(db: Session, farm: dict, start: date, end: date) -> int:
    """
    Synchronise une ferme entre `start` et `end` inclus : l'archive pour
    les jours anciens, l'API de prévision pour les jours récents.
    Retourne le nombre de jours écrits.
    """
    archive_end = min(end, date.today() - timedelta(days=ARCHIVE_DELAY_DAYS))
    written = 0

    if start <= archive_end:
        rows = fetch_daily(
            farm["latitude"], farm["longitude"], start, archive_end, archive=True
        )
        written += upsert_days(db, farm, rows, "archive")

    forecast_start = max(start, archive_end + timedelta(days=1))
    if forecast_start <= end:
        rows = fetch_daily(
            farm["latitude"], farm["longitude"], forecast_start, end, archive=False
        )
        written += upsert_days(db, farm, rows, "forecast")

    db.commit()
    return written


def upsert_site_days(db: Session, site: dict, rows: list[dict], source: str) -> int:
    """
    Comme upsert_days, pour un LIEU ÉTUDIÉ par la projection climatique
    (climate_site) : ses jours sont rangés dans la même table, avec site_id
    au lieu de farm_id. Une valeur absente n'efface jamais une valeur connue.
    """
    columns = list(DAILY_VARIABLES.values())
    updates = ",\n".join(
        f"{c} = COALESCE(EXCLUDED.{c}, weather_daily.{c})" for c in columns
    )

    statement = text(f"""
        INSERT INTO weather_daily (
            site_id, day, {", ".join(columns)},
            latitude, longitude, source
        )
        VALUES (
            :site_id, :day, {", ".join(":" + c for c in columns)},
            :latitude, :longitude, :source
        )
        ON CONFLICT (site_id, day) WHERE site_id IS NOT NULL DO UPDATE SET
            {updates},
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            source = EXCLUDED.source,
            updated_at = CURRENT_TIMESTAMP
    """)

    for row in rows:
        db.execute(statement, {
            **row,
            "site_id": site["id"],
            "latitude": site["latitude"],
            "longitude": site["longitude"],
            "source": source,
        })
    return len(rows)


def sync_site(db: Session, site: dict, start: date, end: date) -> int:
    """
    Synchronise un lieu étudié entre `start` et `end` inclus, comme
    sync_farm : l'archive pour les jours anciens, la prévision pour les
    jours récents. Retourne le nombre de jours écrits.
    """
    archive_end = min(end, date.today() - timedelta(days=ARCHIVE_DELAY_DAYS))
    written = 0

    if start <= archive_end:
        rows = fetch_daily(site["latitude"], site["longitude"], start, archive_end, archive=True)
        written += upsert_site_days(db, site, rows, "archive")

    forecast_start = max(start, archive_end + timedelta(days=1))
    if forecast_start <= end:
        rows = fetch_daily(site["latitude"], site["longitude"], forecast_start, end, archive=False)
        written += upsert_site_days(db, site, rows, "forecast")

    db.commit()
    return written


def sync_all_farms(
    db: Session, start: date, end: date, farm_id: int | None = None
) -> dict:
    """
    Synchronise toutes les fermes (ou une seule). Une ferme en erreur
    n'empêche pas de traiter les suivantes.

    Returns:
        dict {start, end, farms, ok, failed, days_written}
    """
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "farms": 0,
        "ok": 0,
        "failed": 0,
        "days_written": 0,
    }

    if start > end:
        logger.warning("🌦️ [météo] Période vide : %s > %s", start, end)
        return summary

    farms = farm_coordinates(db, farm_id)
    summary["farms"] = len(farms)

    for farm in farms:
        try:
            written = sync_farm(db, farm, start, end)
            summary["ok"] += 1
            summary["days_written"] += written
            logger.info(
                "🌦️ [météo] Ferme %s (%s) : %d jours enregistrés",
                farm["id"], farm["name"], written,
            )
        except Exception:
            db.rollback()
            summary["failed"] += 1
            logger.exception(
                "⚠️ [météo] Échec pour la ferme %s (%s)", farm["id"], farm["name"]
            )

    logger.info(
        "🏁 [météo] Terminé (%s → %s) : %d fermes OK, %d en échec, %d jours écrits",
        start, end, summary["ok"], summary["failed"], summary["days_written"],
    )
    return summary