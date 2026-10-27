"""
Plage habituelle d'un indice pour une parcelle, à une période de l'année.

Principe : comparer une parcelle à ELLE-MÊME. Pour une mesure donnée, on
regarde ce que valait le même indice sur la même parcelle, à la même
période, les années précédentes. Cette référence intègre tout ce que les
seuils généraux ignorent : âge réel, densité, sol visible entre les rangs,
irrigation ou non, climat local, mélange de cultures.

Méthode :
  - fenêtre de ± WINDOW_DAYS jours autour du même jour de l'année ;
  - seules les années PRÉCÉDENTES comptent : les mesures de moins de
    EXCLUDE_RECENT_DAYS jours sont écartées, pour ne pas comparer une
    saison à elle-même ;
  - plage habituelle = 10e à 90e centile : elle contient 80 % des valeurs
    passées à cette période ;
  - au moins MIN_VALUES mesures sur au moins MIN_YEARS années, sinon la
    plage n'est pas calculée.

Limites, à garder en tête dans l'interprétation :
  - « habituel » ne veut pas dire « sain » : une parcelle toujours en
    mauvais état a une plage habituelle basse ;
  - un changement de pratique (plantation, arrachage, irrigation) fausse
    la référence tant que les années anciennes y figurent.

Ce module ne modifie rien : il lit `indice_reading` et calcule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

#: Demi-largeur de la fenêtre autour du même jour de l'année
WINDOW_DAYS = 15
#: Mesures trop récentes pour servir de référence (même saison)
EXCLUDE_RECENT_DAYS = 180
#: Minimum de mesures et d'années pour calculer une plage
MIN_VALUES = 6
MIN_YEARS = 2
#: Centiles bornant la plage habituelle
LOW_PERCENTILE = 10
HIGH_PERCENTILE = 90


@dataclass
class Baseline:
    indice_name: str
    #: Date et valeur de la mesure comparée (la plus récente)
    reference_date: date | None = None
    value: float | None = None
    #: "ok" si la plage est calculée, "insufficient" sinon, "no_data"
    #: si l'indice n'a aucune mesure
    status: str = "no_data"
    low: float | None = None
    median: float | None = None
    high: float | None = None
    values_count: int = 0
    years: list[int] = field(default_factory=list)
    #: Position de la valeur : "below" | "within" | "above" | None
    verdict: str | None = None
    message: str = ""
    window_days: int = WINDOW_DAYS
    #: Début de la plantation actuelle : les mesures antérieures, prises
    #: sur une autre végétation, ne servent pas de référence
    since: date | None = None


def _day_distance(a: date, b: date) -> int:
    """Écart en jours entre deux dates sur le cercle de l'année (ignore l'année)."""
    da = a.timetuple().tm_yday
    db_ = b.timetuple().tm_yday
    diff = abs(da - db_)
    return min(diff, 365 - diff)


def compute_baseline(
    indice_name: str,
    series: list[tuple[date, float]],
    reference_date: date,
    value: float,
    since: date | None = None,
) -> Baseline:
    """
    Calcule la plage habituelle autour de `reference_date` et situe `value`.

    `series` : toutes les mesures de la parcelle pour cet indice (date, valeur).
    `since`  : début de la plantation actuelle. Les mesures antérieures
               décrivent une autre végétation (l'ancienne lavande adulte
               qu'une jeune plantation remplace) : elles sont écartées.
    Fonction pure, sans accès à la base : facile à tester.
    """
    result = Baseline(
        indice_name=indice_name,
        reference_date=reference_date,
        value=value,
        since=since,
    )

    past = [
        (d, v)
        for d, v in series
        if (reference_date - d).days >= EXCLUDE_RECENT_DAYS
        and _day_distance(d, reference_date) <= WINDOW_DAYS
        and (since is None or d >= since)
    ]
    years = sorted({d.year for d, _ in past})
    result.values_count = len(past)
    result.years = years

    if len(past) < MIN_VALUES or len(years) < MIN_YEARS:
        result.status = "insufficient"
        if since is not None:
            result.message = (
                f"Plantation depuis le {since.strftime('%d/%m/%Y')} : pas encore assez "
                f"d'historique depuis cette plantation ({len(past)} mesure(s) sur "
                f"{len(years)} année(s), il en faut au moins {MIN_VALUES} sur "
                f"{MIN_YEARS} années). Les mesures antérieures, prises sur une autre "
                "végétation, ne sont pas utilisées."
            )
            return result
        result.message = (
            f"Historique insuffisant à cette période ({len(past)} mesure(s) "
            f"sur {len(years)} année(s), il en faut au moins {MIN_VALUES} "
            f"sur {MIN_YEARS} années) : la plage habituelle n'est pas encore calculable."
        )
        return result

    values = np.array([v for _, v in past], dtype=float)
    result.low = float(np.percentile(values, LOW_PERCENTILE))
    result.median = float(np.percentile(values, 50))
    result.high = float(np.percentile(values, HIGH_PERCENTILE))
    result.status = "ok"

    if value < result.low:
        result.verdict = "below"
        result.message = "Inhabituellement bas pour cette parcelle à cette période."
    elif value > result.high:
        result.verdict = "above"
        result.message = "Inhabituellement haut pour cette parcelle à cette période."
    else:
        result.verdict = "within"
        result.message = "Dans la plage habituelle de cette parcelle à cette période."

    return result


def current_plantation_start(db: Session, parcel_id: int) -> date | None:
    """
    Début de la végétation actuelle de la parcelle : la plantation LA PLUS
    RÉCENTE parmi les cultures en place (statut « active »).

    Une parcelle peut porter plusieurs cultures (oliviers et aromatiques
    entre les rangs) : le signal satellite est celui de leur mélange, qui
    change dès qu'une culture est ajoutée ou remplacée. Seules les mesures
    postérieures à ce changement décrivent la végétation actuelle.

    None si aucune culture en place n'est déclarée : tout l'historique est
    alors utilisé, comme avant.
    """
    return db.execute(
        text("""
            SELECT MAX(planting_date) FROM crop
            WHERE parcel_id = :pid AND status = 'active'
        """),
        {"pid": parcel_id},
    ).scalar()


def baseline_for_latest(db: Session, parcel_id: int, indice_name: str) -> Baseline:
    """
    Plage habituelle pour la mesure LA PLUS RÉCENTE d'un indice sur une
    parcelle. Seules les mesures valides, et postérieures au début de la
    plantation actuelle, sont prises en compte.
    """
    rows = db.execute(
        text("""
            SELECT scene_date, value
            FROM indice_reading
            WHERE parcel_id = :pid
              AND indice_name = :name
              AND scene_date IS NOT NULL
              AND value IS NOT NULL
              AND COALESCE(is_valid, TRUE)
            ORDER BY scene_date
        """),
        {"pid": parcel_id, "name": indice_name},
    ).fetchall()

    if not rows:
        return Baseline(
            indice_name=indice_name,
            message="Aucune mesure enregistrée pour cet indice.",
        )

    series = [(r.scene_date, float(r.value)) for r in rows]
    reference_date, value = series[-1]
    return compute_baseline(
        indice_name, series, reference_date, value,
        since=current_plantation_start(db, parcel_id),
    )