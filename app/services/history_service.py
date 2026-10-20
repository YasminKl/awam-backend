"""
Constitution de l'historique Sentinel-2 d'une parcelle, sur plusieurs années.

Code COMMUN à la tâche Celery (création d'une parcelle) et au script en
ligne de commande : une seule implémentation des règles.

Règles :
  - UNE image par date : une même prise de vue apparaît dans plusieurs
    tuiles Sentinel-2 qui se chevauchent ; on garde celle où la parcelle
    est la mieux dégagée ;
  - une date n'est retenue que si la parcelle y est dégagée à au moins
    MIN_VALID_RATIO (60 %), comme pour la tâche quotidienne ;
  - rien avant la date de plantation la plus ancienne, quand elle est
    connue ;
  - les dates déjà en base sont ignorées : relancer ne crée pas de doublon ;
  - seules les VALEURS sont enregistrées (`write_cogs=False`), sans image
    cartographique ni envoi sur B2.

Robustesse réseau :
  - recherche dans le catalogue relancée jusqu'à SEARCH_ATTEMPTS fois ;
  - autorisation d'accès aux images demandée juste avant chaque
    téléchargement (elle expire au bout d'environ une heure) ;
  - une date dont les bandes ont été refusées est retentée une fois.

Aucun suivi n'est stocké : la constitution est silencieuse. Les relances
sont sûres car les dates déjà en base sont toujours ignorées.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import planetary_computer
from pystac_client import Client
from sqlalchemy import text
from sqlalchemy.orm import Session

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

logger = logging.getLogger(__name__)

#: Profondeur d'historique par défaut, en années (+ l'année en cours)
HISTORY_YEARS = 3
#: Nombre de dates par lot Celery : environ 4 à 5 minutes de traitement
BATCH_SIZE = 20
#: Relances d'une recherche coupée par le serveur
SEARCH_ATTEMPTS = 3
#: Pause de base entre deux essais, en secondes (doublée au 2e essai)
RETRY_PAUSE_S = 10


# ======================================================================
# Lecture de la base
# ======================================================================


def _planting_floor(db: Session, parcel_id: int) -> date | None:
    """Date de plantation la plus ancienne des cultures de la parcelle."""
    return db.execute(
        text("SELECT MIN(planting_date) FROM crop WHERE parcel_id = :pid"),
        {"pid": parcel_id},
    ).scalar()


def _dates_in_base(db: Session, parcel_id: int) -> set[str]:
    """Dates d'image déjà enregistrées pour la parcelle."""
    rows = db.execute(
        text("SELECT DISTINCT scene_date FROM indice_reading WHERE parcel_id = :pid"),
        {"pid": parcel_id},
    ).fetchall()
    return {r.scene_date.isoformat() for r in rows if r.scene_date}


def _farm_id(db: Session, parcel_id: int) -> int | None:
    return db.execute(
        text("SELECT farm_id FROM parcel WHERE id = :pid"), {"pid": parcel_id}
    ).scalar()


# ======================================================================
# Catalogue Sentinel-2
# ======================================================================


def _search_with_retry(client: Client, bbox, start: date, end: date) -> list:
    """Recherche dans le catalogue, relancée si le serveur coupe la connexion."""
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
            logger.warning(
                "[Historique] Recherche coupée (%s), nouvel essai dans %s s",
                type(e).__name__, pause,
            )
            time.sleep(pause)
    return []


def candidates_by_date(client: Client, bbox, start: date, end: date) -> dict[str, list]:
    """Scènes sous le plafond de nuages, regroupées par date, recherche année par année."""
    by_date: dict[str, list] = defaultdict(list)
    for year in range(start.year, end.year + 1):
        y_start = max(start, date(year, 1, 1))
        y_end = min(end, date(year, 12, 31))
        if y_start > y_end:
            continue
        for item in _search_with_retry(client, bbox, y_start, y_end):
            day = item.properties.get("datetime", "")[:10]
            if day:
                by_date[day].append(item)
    return dict(sorted(by_date.items()))


def best_item(items: list, bbox, geometry) -> tuple[object | None, float]:
    """
    Parmi les scènes d'une même date, celle où la parcelle est la mieux dégagée.
    Renvoie la scène NON signée : l'autorisation est demandée au téléchargement.
    """
    best, best_ratio = None, -1.0
    for item in items:
        ratio = _parcel_valid_ratio(planetary_computer.sign(item), bbox, geometry)
        if ratio > best_ratio:
            best, best_ratio = item, ratio
    return best, best_ratio


def ingest_date(db: Session, parcel_id: int, farm_id: int, item) -> bool:
    """
    Calcule et enregistre les indices d'une date, avec une autorisation
    fraîche. Si aucune bande n'a pu être lue, la date est retentée une fois.
    """
    for attempt in (1, 2):
        result = generate_all_indices_for_parcel(
            db=db,
            farm_id=farm_id,
            parcel_id=parcel_id,
            item=planetary_computer.sign(item),
            write_cogs=False,
        )
        if result and (result.get("inserted") or result.get("cached")):
            return True
        if attempt == 1:
            time.sleep(RETRY_PAUSE_S)
    return False


# ======================================================================
# Planification et traitement
# ======================================================================


@dataclass
class Plan:
    """Dates à traiter pour une parcelle."""

    start: date
    end: date
    found: int = 0
    already: int = 0
    todo: list[str] = field(default_factory=list)


def plan_parcel(
    db: Session,
    parcel_id: int,
    years: int = HISTORY_YEARS,
    client: Client | None = None,
) -> Plan | None:
    """
    Liste les dates à traiter : toutes les dates de passage du satellite
    sur la période, moins celles déjà en base. None si la parcelle n'a pas
    de géométrie.
    """
    bbox = _get_bbox_for_parcel(db, parcel_id)
    if bbox is None:
        return None

    end = date.today()
    start = date(end.year - years, 1, 1)
    floor = _planting_floor(db, parcel_id)
    if floor:
        start = max(start, floor)

    client = client or Client.open(STAC_API_URL)
    by_date = candidates_by_date(client, bbox, start, end)
    known = _dates_in_base(db, parcel_id)
    todo = [d for d in by_date if d not in known]

    return Plan(start=start, end=end, found=len(by_date), already=len(by_date) - len(todo), todo=todo)


@dataclass
class BatchResult:
    inserted: int = 0
    cloudy: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def processed(self) -> int:
        return self.inserted + self.cloudy + self.failed + self.skipped


def process_dates(
    db: Session,
    parcel_id: int,
    dates: list[str],
    client: Client | None = None,
    on_progress=None,
) -> BatchResult:
    """
    Traite une liste de dates : recherche les scènes de ces dates, garde la
    mieux dégagée de chacune, et enregistre ses indices.

    `on_progress(i, total, result)` est appelé après chaque date : le
    script s'en sert pour son affichage.
    """
    result = BatchResult()
    if not dates:
        return result

    bbox = _get_bbox_for_parcel(db, parcel_id)
    farm_id = _farm_id(db, parcel_id)
    if bbox is None or farm_id is None:
        # Parcelle supprimée entre-temps
        result.skipped = len(dates)
        return result
    geometry = _get_geometry_for_parcel(db, parcel_id)

    wanted = sorted(dates)
    client = client or Client.open(STAC_API_URL)
    by_date = candidates_by_date(
        client, bbox, date.fromisoformat(wanted[0]), date.fromisoformat(wanted[-1])
    )
    known = _dates_in_base(db, parcel_id)

    for i, day in enumerate(wanted, start=1):
        try:
            items = by_date.get(day)
            if day in known or not items:
                result.skipped += 1
            else:
                item, ratio = best_item(items, bbox, geometry)
                if item is None or ratio < MIN_VALID_RATIO:
                    result.cloudy += 1
                elif ingest_date(db, parcel_id, farm_id, item):
                    result.inserted += 1
                else:
                    result.failed += 1
                    logger.warning("[Historique] %s : aucun indice calculé", day)
        except Exception as e:
            db.rollback()
            result.failed += 1
            logger.warning("[Historique] %s : échec (%s : %s)", day, type(e).__name__, e)

        if on_progress:
            on_progress(i, len(wanted), result)

    return result


def batches(dates: list[str], size: int = BATCH_SIZE) -> list[list[str]]:
    """Découpe la liste des dates en lots consécutifs."""
    return [dates[i:i + size] for i in range(0, len(dates), size)]