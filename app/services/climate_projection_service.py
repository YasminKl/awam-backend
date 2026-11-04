"""
Projection climatique d'un lieu : téléchargement, correction des biais,
évapotranspiration et enregistrement.

Aucune donnée en double :
  - la météo RÉELLE quotidienne (pluie, températures min et max, ET₀) est
    dans weather_daily (Open-Meteo) : elle est synchronisée là, et relue
    là, jamais recopiée ;
  - climate_monthly reçoit seulement :
      · les températures de surface réelles de la terre et de la mer (ERA5) ;
      · les projections CMIP6 BRUTES, depuis le début de la période de
        référence (pour pouvoir recalculer la correction sans tout
        retélécharger) ;
      · les projections CORRIGÉES, sur la période affichée (2020-2040) ;
  - le climat réel de la période de référence (1995-2014) n'est pas
    stocké : il sert une fois au calcul de la correction.

Correction des biais : méthode « delta », mois par mois. Pour chaque mois
de l'année, on compare la moyenne du modèle à celle du climat réel sur la
période de référence :
  - températures : on ajoute l'écart (réel − modèle) ;
  - pluie : on multiplie par le rapport (réel ÷ modèle).

Évapotranspiration des projections : méthode de Hargreaves (FAO-56,
équation 52), à partir des températures moyenne, maximale et minimale et
du rayonnement extraterrestre (FAO-56, équation 21).

Tous les réglages viennent de app/core/climate_config.py.
"""

from __future__ import annotations

import calendar
import logging
import math
import tempfile
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import climate_config as cfg
from app.models.climate import (
    SITE_FAILED,
    SITE_READY,
    SITE_RUNNING,
    SOURCE_CMIP6,
    SOURCE_ERA5,
    ClimateSite,
)
from app.services import climate_sources as src
from app.services import weather_history_service as weather

logger = logging.getLogger(__name__)

#: Une série mensuelle : (année, mois) → valeur
Series = dict[tuple[int, int], float]

#: Expérience CMIP6 qui simule le passé (jusqu'en 2014)
HISTORICAL = "historical"

#: En dessous de ce cumul moyen (mm/mois), le modèle est presque sec ce
#: mois-là : un rapport réel ÷ modèle exploserait, on ne corrige pas la pluie
MIN_PRECIP_FOR_RATIO_MM = 1.0

#: Un mois réel n'est retenu que s'il manque au plus ce nombre de jours
MAX_MISSING_DAYS = 3

VARIABLES_BY_KEY = {v.key: v for v in cfg.CLIMATE_VARIABLES}


# ======================================================================
# Avancement, affiché dans le terminal (worker ou script)
# ======================================================================


def _duration(seconds: float) -> str:
    """« 42 s », « 3 min 12 s », « 2 h 41 min »."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


class _Progress:
    """Compteur des demandes à Copernicus pendant la préparation d'un lieu."""

    def __init__(self, name: str, total: int):
        self.name, self.total, self.done = name, total, 0
        self.started = time.monotonic()

    def step(self, label: str) -> float:
        self.done += 1
        logger.info("🌍 [climat] %s · %d/%d · %s", self.name, self.done, self.total, label)
        return time.monotonic()

    def finished(self, since: float) -> None:
        logger.info(
            "🌍 [climat] %s · %d/%d ✓ reçue en %s", self.name, self.done, self.total, _duration(time.monotonic() - since)
        )


#: Avancement de la préparation en cours (aucun hors d'une préparation)
_progress: _Progress | None = None


def _retrieve(label: str, *args, **kwargs):
    """Une demande à Copernicus, comptée et chronométrée pendant une préparation."""
    since = _progress.step(label) if _progress else None
    values = src.retrieve_values(*args, **kwargs)
    if _progress and since is not None:
        _progress.finished(since)
    return values

# ======================================================================
# Évapotranspiration (FAO-56)
# ======================================================================

#: Constante solaire, MJ m-2 min-1 (FAO-56)
SOLAR_CONSTANT = 0.0820


def extraterrestrial_radiation(latitude_deg: float, day_of_year: int) -> float:
    """
    Rayonnement extraterrestre Ra, en MJ m-2 jour-1 (FAO-56, équations 21
    à 25). Exemple 8 de FAO-56 : 20° sud, 3 septembre → 32,2.
    """
    phi = math.radians(latitude_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * day_of_year / 365)
    delta = 0.409 * math.sin(2 * math.pi * day_of_year / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    return (24 * 60 / math.pi) * SOLAR_CONSTANT * dr * (
        ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws)
    )


def hargreaves_et0_month(
    tmean: float | None, tmax: float | None, tmin: float | None, latitude: float, year: int, month: int
) -> float | None:
    """
    ET₀ d'un mois, en mm, méthode de Hargreaves (FAO-56, équation 52) :
    ET₀ = 0,0023 × (Tmoy + 17,8) × √(Tmax − Tmin) × Ra, avec Ra en mm/jour
    (MJ × 0,408), calculé au milieu du mois.
    """
    if tmax is None or tmin is None:
        return None
    if tmean is None:
        tmean = (tmax + tmin) / 2
    days = calendar.monthrange(year, month)[1]
    mid_day = date(year, month, (days + 1) // 2).timetuple().tm_yday
    ra_mm = 0.408 * extraterrestrial_radiation(latitude, mid_day)
    daily = 0.0023 * (tmean + 17.8) * math.sqrt(max(tmax - tmin, 0.0)) * ra_mm
    return round(daily * days, 2)


# ======================================================================
# Outils de séries
# ======================================================================


def climatology(series: Series, years: range) -> dict[int, float]:
    """Moyenne de chaque mois de l'année (1 à 12) sur les années données."""
    by_month: dict[int, list[float]] = defaultdict(list)
    for (y, m), v in series.items():
        if y in years and v is not None:
            by_month[m].append(v)
    return {m: sum(vals) / len(vals) for m, vals in by_month.items() if vals}


def correction_factors(observed: dict[int, float], modelled: dict[int, float], mode: str) -> dict[int, float]:
    """
    Facteur de correction de chaque mois : écart (« add ») pour les
    températures, rapport (« mul ») pour la pluie. Un mois sans valeur
    réelle ou modélisée n'a pas de facteur.
    """
    factors: dict[int, float] = {}
    for m in range(1, 13):
        if m not in observed or m not in modelled:
            continue
        if mode == "mul":
            factors[m] = observed[m] / modelled[m] if modelled[m] >= MIN_PRECIP_FOR_RATIO_MM else 1.0
        else:
            factors[m] = observed[m] - modelled[m]
    return factors


def apply_factor(value: float | None, factor: float | None, mode: str) -> float | None:
    if value is None or factor is None:
        return None
    return round(value * factor if mode == "mul" else value + factor, 3)


def last_complete_month(today: date | None = None) -> tuple[int, int]:
    first = (today or date.today()).replace(day=1)
    previous = first - timedelta(days=1)
    return previous.year, previous.month


# ======================================================================
# Climat réel : weather_daily (pluie, températures de l'air)
# ======================================================================


def _site_point(db: Session, site: ClimateSite) -> dict:
    """Le point météo du lieu : celui de la ferme (même calcul que la météo), ou le sien."""
    if site.farm_id:
        farms = weather.farm_coordinates(db, site.farm_id)
        if farms:
            return farms[0]
    return {"id": site.id, "latitude": round(site.latitude, 4), "longitude": round(site.longitude, 4)}


def ensure_real_weather(db: Session, site: ClimateSite, start: date, end: date) -> int:
    """
    Complète weather_daily pour le lieu entre `start` et `end` : la ferme
    elle-même si le lieu est une ferme (ses jours restent les siens), sinon
    le lieu étudié. Seuls les jours MANQUANTS au début sont demandés.
    """
    owner = "farm_id" if site.farm_id else "site_id"
    owner_id = site.farm_id or site.id
    first = db.execute(
        text(f"SELECT MIN(day) FROM weather_daily WHERE {owner} = :id"), {"id": owner_id}
    ).scalar()
    sync_end = end if first is None else min(end, first - timedelta(days=1))
    if sync_end < start:
        return 0
    point = _site_point(db, site)
    if site.farm_id:
        return weather.sync_farm(db, point, start, sync_end)
    return weather.sync_site(db, point, start, sync_end)


def real_monthly_from_weather(db: Session, site: ClimateSite) -> dict[str, Series]:
    """
    Le climat réel mensuel, RÉSUMÉ depuis weather_daily (sans copie) :
    pluie totale, moyennes des températures maximale, minimale et moyenne
    ((max + min) ÷ 2, comme FAO-56). Les mois incomplets sont écartés.
    """
    owner = "farm_id" if site.farm_id else "site_id"
    rows = db.execute(
        text(f"""
            SELECT EXTRACT(YEAR FROM day)::int AS y, EXTRACT(MONTH FROM day)::int AS m,
                   COUNT(*) AS n,
                   SUM(precipitation_mm) AS rain,
                   AVG(temperature_max_c) AS tmax,
                   AVG(temperature_min_c) AS tmin,
                   AVG((temperature_max_c + temperature_min_c) / 2) AS tmean
            FROM weather_daily
            WHERE {owner} = :id
            GROUP BY 1, 2
        """),
        {"id": site.farm_id or site.id},
    ).fetchall()
    return _monthly_from_rows(rows)


def _monthly_from_rows(rows) -> dict[str, Series]:
    out: dict[str, Series] = {
        "precipitation_mm": {}, "air_temperature_max_c": {}, "air_temperature_min_c": {}, "air_temperature_c": {},
    }
    for r in rows:
        if r.n < calendar.monthrange(r.y, r.m)[1] - MAX_MISSING_DAYS:
            continue
        for key, value in (
            ("precipitation_mm", r.rain), ("air_temperature_max_c", r.tmax),
            ("air_temperature_min_c", r.tmin), ("air_temperature_c", r.tmean),
        ):
            if value is not None:
                out[key][(r.y, r.m)] = float(value)
    return out


class _Row:
    """Une ligne de résumé mensuel, construite en mémoire."""

    def __init__(self, y, m, n, rain, tmax, tmin, tmean):
        self.y, self.m, self.n, self.rain, self.tmax, self.tmin, self.tmean = y, m, n, rain, tmax, tmin, tmean


def real_monthly_from_archive(latitude: float, longitude: float, years: range) -> dict[str, Series]:
    """
    Le climat réel mensuel d'une PÉRIODE DE RÉFÉRENCE, lu dans l'archive
    Open-Meteo et résumé en mémoire : il sert au calcul de la correction,
    et n'est pas enregistré.
    """
    days = weather.fetch_daily(
        latitude, longitude, date(years.start, 1, 1), date(years.stop - 1, 12, 31), archive=True
    )
    groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for d in days:
        groups[(d["day"].year, d["day"].month)].append(d)

    def mean(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None

    rows = []
    for (y, m), items in groups.items():
        tmax = [i.get("temperature_max_c") for i in items]
        tmin = [i.get("temperature_min_c") for i in items]
        rain = [i.get("precipitation_mm") for i in items if i.get("precipitation_mm") is not None]
        tmean = [(a + b) / 2 for a, b in zip(tmax, tmin) if a is not None and b is not None]
        rows.append(_Row(y, m, len(items), sum(rain) if rain else None, mean(tmax), mean(tmin), mean(tmean)))
    return _monthly_from_rows(rows)


# ======================================================================
# Téléchargements Copernicus
# ======================================================================


_MONTHS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _period_label(years: list[int], months: list[int]) -> str:
    """« 2020-2025 », « 2026, janvier à octobre », « 2014 »."""
    if len(years) > 1:
        return f"{years[0]}-{years[-1]}"
    if len(months) == 12:
        return str(years[0])
    if len(months) == 1:
        return f"{years[0]}, {_MONTHS_FR[months[0]]}"
    return f"{years[0]}, {_MONTHS_FR[months[0]]} à {_MONTHS_FR[months[-1]]}"


def _era5_batches(years: list[int], until: tuple[int, int] | None) -> list[tuple[list[int], list[int]]]:
    """
    Découpe une demande ERA5 en (années, mois) pour ne demander QUE des mois
    publiés : les années complètes d'un coup, puis la dernière année jusqu'au
    mois `until` seulement (Copernicus avertit sinon d'une demande de mois
    qui n'existent pas encore).
    """
    if until is None or not years or max(years) < until[0]:
        return [(sorted(years), src.ALL_MONTHS)] if years else []
    full = sorted(y for y in years if y < until[0])
    batches = [(full, src.ALL_MONTHS)] if full else []
    batches.append(([until[0]], list(range(1, until[1] + 1))))
    return batches


def download_era5_surfaces(
    client, lat: float, lon: float, years: list[int], workdir: Path, site: ClimateSite | None = None,
    until: tuple[int, int] | None = None, purpose: str = "réel",
) -> dict[str, Series]:
    """
    Températures de surface réelles (terre et mer), ERA5 mensuel, jusqu'au
    mois `until` inclus s'il est donné. Si le lieu est donné et que sa
    distance à la mer n'est pas encore connue, elle est mesurée au passage
    (maille de mer la plus proche).
    """
    out: dict[str, Series] = {}
    for var in cfg.ERA5_STORED_VARIABLES:
        out[var.key] = {}
        for batch_years, batch_months in _era5_batches(years, until):
            dataset, request = src.era5_monthly_request(var, batch_years, batch_months, lat, lon)
            meta: dict = {}
            label = f"ERA5 {purpose} · {var.label} ({_period_label(batch_years, batch_months)})"
            values = _retrieve(
                label, client, dataset, request, var.era5_file, var.era5_convert, var.sea, lat, lon, workdir, meta
            )
            out[var.key].update({(y, m): v for y, m, v in values})
            if var.sea and site is not None and site.sea_distance_km is None and "distance_km" in meta:
                site.sea_distance_km = meta["distance_km"]
    return out


def download_cmip6(
    client, lat: float, lon: float, model: str, experiment: str, years: list[int], workdir: Path
) -> dict[str, Series]:
    """Les 6 variables d'un modèle, pour une expérience (« historical » ou un scénario)."""
    out: dict[str, Series] = {}
    if not years:
        return out
    for var in cfg.CLIMATE_VARIABLES:
        dataset, request = src.cmip6_years_request(var, model, experiment, years, src.ALL_MONTHS, lat, lon)
        values = _retrieve(
            f"CMIP6 {model} {experiment} · {var.label}",
            client, dataset, request, var.cmip6_file, var.cmip6_convert, var.sea, lat, lon, workdir,
        )
        out[var.key] = {(y, m): v for y, m, v in values}
    return out


def _merge(*parts: dict[str, Series]) -> dict[str, Series]:
    merged: dict[str, Series] = defaultdict(dict)
    for part in parts:
        for key, series in part.items():
            merged[key].update(series)
    return dict(merged)


# ======================================================================
# Écriture
# ======================================================================

VALUE_COLUMNS = [v.key for v in cfg.CLIMATE_VARIABLES] + ["et0_mm"]


def upsert_months(
    db: Session, site_id: int, source: str, scenario: str, model: str, corrected: bool,
    months: dict[tuple[int, int], dict[str, float | None]],
) -> int:
    """Écrit des mois de climat, sans doublon : un mois déjà présent est remplacé."""
    if not months:
        return 0
    statement = text(f"""
        INSERT INTO climate_monthly (site_id, source, scenario, model, corrected, year, month, {", ".join(VALUE_COLUMNS)})
        VALUES (:site_id, :source, :scenario, :model, :corrected, :year, :month, {", ".join(":" + c for c in VALUE_COLUMNS)})
        ON CONFLICT ON CONSTRAINT uq_climate_monthly DO UPDATE SET
            {", ".join(f"{c} = EXCLUDED.{c}" for c in VALUE_COLUMNS)}
    """)
    for (y, m), values in sorted(months.items()):
        db.execute(statement, {
            "site_id": site_id, "source": source, "scenario": scenario, "model": model,
            "corrected": corrected, "year": y, "month": m,
            **{c: values.get(c) for c in VALUE_COLUMNS},
        })
    return len(months)


def _rows_from_series(
    series: dict[str, Series], years: range, latitude: float, keys: list[str] | None = None
) -> dict[tuple[int, int], dict[str, float | None]]:
    """Regroupe les séries par mois, et calcule l'ET₀ de chaque mois."""
    keys = keys or [v.key for v in cfg.CLIMATE_VARIABLES]
    months: dict[tuple[int, int], dict[str, float | None]] = {}
    for y in years:
        for m in range(1, 13):
            values = {k: series.get(k, {}).get((y, m)) for k in keys}
            if all(v is None for v in values.values()):
                continue
            values["et0_mm"] = hargreaves_et0_month(
                values.get("air_temperature_c"), values.get("air_temperature_max_c"),
                values.get("air_temperature_min_c"), latitude, y, m,
            )
            months[(y, m)] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in values.items()}
    return months


def load_raw_series(db: Session, site_id: int, model: str, scenario: str) -> dict[str, Series]:
    """Les projections BRUTES déjà enregistrées d'un modèle et d'un scénario."""
    rows = db.execute(
        text(f"""
            SELECT year, month, {", ".join(VALUE_COLUMNS)} FROM climate_monthly
            WHERE site_id = :s AND source = :src AND model = :model AND scenario = :sc AND corrected = FALSE
        """),
        {"s": site_id, "src": SOURCE_CMIP6, "model": model, "sc": scenario},
    ).mappings().all()
    series: dict[str, Series] = defaultdict(dict)
    for r in rows:
        for v in cfg.CLIMATE_VARIABLES:
            if r[v.key] is not None:
                series[v.key][(r["year"], r["month"])] = r[v.key]
    return dict(series)


def write_corrected(
    db: Session, site: ClimateSite, model: str, scenario: str, raw: dict[str, Series],
    observed: dict[str, Series], reference: range,
) -> int:
    """
    Corrige les projections brutes d'un modèle et d'un scénario, mois par
    mois, avec le climat réel de la période `reference`, puis les écrit
    sur la période affichée.
    """
    corrected: dict[str, Series] = {}
    for var in cfg.CLIMATE_VARIABLES:
        factors = correction_factors(
            climatology(observed.get(var.key, {}), reference),
            climatology(raw.get(var.key, {}), reference),
            var.correction,
        )
        corrected[var.key] = {
            (y, m): apply_factor(v, factors.get(m), var.correction) for (y, m), v in raw.get(var.key, {}).items()
        }
    months = _rows_from_series(corrected, range(cfg.START_YEAR, cfg.END_YEAR + 1), site.latitude)
    return upsert_months(db, site.id, SOURCE_CMIP6, scenario, model, True, months)


# ======================================================================
# Les traitements d'un lieu
# ======================================================================


def _set_status(db: Session, site: ClimateSite, status: str, error: str | None = None) -> None:
    site.status = status
    site.last_error = error
    db.commit()


def _real_surface_plan(site: ClimateSite, today: date | None) -> tuple[date, int, int] | None:
    """Premier mois réel manquant, et dernier mois publié ; None si tout est à jour."""
    end_y, end_m = last_complete_month(today)
    start = date(cfg.START_YEAR, 1, 1)
    if site.era5_until:
        start = (site.era5_until.replace(day=28) + timedelta(days=4)).replace(day=1)
    if (start.year, start.month) > (end_y, end_m):
        return None
    return start, end_y, end_m


def store_real_surfaces(db: Session, site: ClimateSite, client, workdir: Path, today: date | None = None) -> int:
    """
    Ajoute les températures de surface réelles (ERA5) des mois complets
    pas encore enregistrés, depuis le début de la période affichée.
    """
    plan = _real_surface_plan(site, today)
    if plan is None:
        return 0
    start, end_y, end_m = plan
    surfaces = download_era5_surfaces(
        client, site.latitude, site.longitude, list(range(start.year, end_y + 1)), workdir, site,
        until=(end_y, end_m),
    )
    keep = {(y, m) for y in range(start.year, end_y + 1) for m in range(1, 13)
            if (start.year, start.month) <= (y, m) <= (end_y, end_m)}
    months: dict[tuple[int, int], dict[str, float | None]] = {}
    for key, series in surfaces.items():
        for ym, v in series.items():
            if ym in keep:
                months.setdefault(ym, {})[key] = round(v, 3)
    written = upsert_months(db, site.id, SOURCE_ERA5, "", "", False, months)
    if months:
        last = max(months)
        site.era5_until = date(last[0], last[1], 1)
    db.commit()
    return written


def observed_for_reference(db: Session, site: ClimateSite, client, reference: range, workdir: Path) -> dict[str, Series]:
    """
    Le climat réel d'une période de référence, pour la correction :
    weather_daily pour les années déjà présentes, l'archive Open-Meteo pour
    les autres (sans les enregistrer), et ERA5 pour les surfaces.
    """
    stored = real_monthly_from_weather(db, site)
    stored_years = {y for series in stored.values() for (y, _m) in series}
    missing = [y for y in reference if y not in stored_years]
    archive: dict[str, Series] = {}
    if missing:
        archive = real_monthly_from_archive(site.latitude, site.longitude, range(min(missing), max(missing) + 1))
    if missing:
        logger.info(
            "🌍 [climat] %s · météo réelle de référence %d-%d lue chez Open-Meteo (non enregistrée)",
            site.name, min(missing), max(missing),
        )
    surfaces = download_era5_surfaces(
        client, site.latitude, site.longitude, list(reference), workdir, site, purpose="référence"
    )
    return _merge(archive, stored, surfaces)


def ensure_farm_sites(db: Session, user_id: int | None = None) -> list[int]:
    """
    Crée le lieu étudié de chaque ferme qui n'en a pas encore (et qui a une
    position ou des parcelles), à l'état « en_attente » : la tâche horaire
    les prépare ensuite. Retourne les numéros des lieux créés.
    """
    rows = db.execute(
        text("""
            SELECT f.id, f.user_id, f.name FROM farm f
            WHERE (CAST(:user_id AS INTEGER) IS NULL OR f.user_id = :user_id)
              AND NOT EXISTS (SELECT 1 FROM climate_site s WHERE s.farm_id = f.id)
        """),
        {"user_id": user_id},
    ).fetchall()
    created: list[int] = []
    for farm in rows:
        points = weather.farm_coordinates(db, farm.id)
        if not points:
            continue
        site = ClimateSite(
            user_id=farm.user_id, farm_id=farm.id, name=farm.name,
            latitude=points[0]["latitude"], longitude=points[0]["longitude"],
        )
        db.add(site)
        db.flush()
        created.append(site.id)
    db.commit()
    return created


def prepare_site(db: Session, site_id: int, client=None, today: date | None = None) -> dict:
    """
    Prépare le climat complet d'un lieu. Peut être relancé sans risque :
    rien n'est écrit en double.

      1. la météo réelle quotidienne, dans weather_daily, depuis le début
         de la période affichée ;
      2. les températures de surface réelles (ERA5) ;
      3. le climat réel de la période de référence (1995-2014) ;
      4. pour chaque modèle et chaque scénario : les projections brutes,
         puis corrigées.
    """
    site = db.get(ClimateSite, site_id)
    if site is None:
        raise ValueError(f"Lieu étudié {site_id} introuvable")
    _set_status(db, site, SITE_RUNNING)
    client = client or src.make_client()
    today = today or date.today()
    reference = range(cfg.BASELINE_START, cfg.BASELINE_END + 1)
    summary = {"site_id": site.id, "weather_days": 0, "real_surface_months": 0, "projections": 0}

    # Nombre de demandes à Copernicus, pour afficher l'avancement
    n_surfaces = len(cfg.ERA5_STORED_VARIABLES)
    plan = _real_surface_plan(site, today)
    n_real = n_surfaces * len(_era5_batches(list(range(plan[0].year, plan[1] + 1)), plan[1:])) if plan else 0
    total = n_real + n_surfaces + len(cfg.MODELS) * len(cfg.CLIMATE_VARIABLES) * (1 + len(cfg.SCENARIOS))
    global _progress
    _progress = _Progress(site.name, total)
    logger.info(
        "🌍 [climat] %s : préparation lancée · %d demandes à Copernicus (%s · %s)",
        site.name, total, ", ".join(cfg.MODELS), ", ".join(cfg.SCENARIOS),
    )

    try:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            summary["weather_days"] = ensure_real_weather(
                db, site, date(cfg.START_YEAR, 1, 1), today - timedelta(days=1)
            )
            logger.info(
                "🌍 [climat] %s · météo réelle Open-Meteo : %d jours ajoutés", site.name, summary["weather_days"]
            )
            summary["real_surface_months"] = store_real_surfaces(db, site, client, workdir, today)
            observed = observed_for_reference(db, site, client, reference, workdir)

            for model in cfg.MODELS:
                past = list(range(cfg.BASELINE_START, min(cfg.BASELINE_END, cfg.CMIP6_HISTORICAL_END) + 1))
                historical = download_cmip6(client, site.latitude, site.longitude, model, HISTORICAL, past, workdir)
                for scenario in cfg.SCENARIOS:
                    future = list(range(max(cfg.CMIP6_HISTORICAL_END + 1, cfg.BASELINE_START), cfg.END_YEAR + 1))
                    raw = _merge(
                        historical,
                        download_cmip6(client, site.latitude, site.longitude, model, scenario, future, workdir),
                    )
                    # Brutes : depuis le début de la référence, pour recalculer plus tard
                    upsert_months(
                        db, site.id, SOURCE_CMIP6, scenario, model, False,
                        _rows_from_series(raw, range(cfg.BASELINE_START, cfg.END_YEAR + 1), site.latitude),
                    )
                    summary["projections"] += write_corrected(db, site, model, scenario, raw, observed, reference)
                    db.commit()
                    logger.info("🌍 [climat] %s · %s %s : projection enregistrée", site.name, model, scenario)
    except Exception as exc:
        db.rollback()
        _set_status(db, site, SITE_FAILED, str(exc)[:2000])
        logger.warning(
            "🌍 [climat] %s : échec après %s, à la demande %d/%d",
            site.name, _duration(time.monotonic() - _progress.started), _progress.done, _progress.total,
        )
        raise
    finally:
        elapsed = time.monotonic() - _progress.started
        _progress = None

    _set_status(db, site, SITE_READY)
    logger.info("🌍 [climat] %s : prêt en %s", site.name, _duration(elapsed))
    return summary


def update_real_month(db: Session, site_id: int, client=None, today: date | None = None) -> int:
    """
    Chaque mois : ajoute les températures de surface réelles du mois écoulé
    et, pour un lieu qui n'est pas une ferme, sa météo quotidienne depuis le
    dernier jour enregistré (celle des fermes est déjà mise à jour chaque
    matin par la tâche météo).
    """
    site = db.get(ClimateSite, site_id)
    if site is None or site.status != SITE_READY:
        return 0
    today = today or date.today()
    if not site.farm_id:
        last = db.execute(
            text("SELECT MAX(day) FROM weather_daily WHERE site_id = :id"), {"id": site.id}
        ).scalar()
        start = (last + timedelta(days=1)) if last else date(cfg.START_YEAR, 1, 1)
        if start <= today - timedelta(days=1):
            weather.sync_site(db, _site_point(db, site), start, today - timedelta(days=1))
    with tempfile.TemporaryDirectory() as tmp:
        return store_real_surfaces(db, site, client or src.make_client(), Path(tmp), today)


def sliding_reference(today: date | None = None) -> range:
    """Les N dernières années complètes (fenêtre glissante du recalcul annuel)."""
    last_year = (today or date.today()).year - 1
    return range(last_year - cfg.SLIDING_YEARS + 1, last_year + 1)


def recompute_corrections(db: Session, site_id: int, client=None, today: date | None = None) -> int:
    """
    Chaque année : recalcule la correction avec les N dernières années
    réelles, à partir des projections brutes déjà enregistrées (sans les
    retélécharger). Les projections corrigées sont remplacées.
    """
    site = db.get(ClimateSite, site_id)
    if site is None or site.status != SITE_READY:
        return 0
    reference = sliding_reference(today)
    if reference.start < cfg.BASELINE_START:
        reference = range(cfg.BASELINE_START, reference.stop)
    written = 0
    with tempfile.TemporaryDirectory() as tmp:
        observed = observed_for_reference(db, site, client or src.make_client(), reference, Path(tmp))
        for model in cfg.MODELS:
            for scenario in cfg.SCENARIOS:
                raw = load_raw_series(db, site.id, model, scenario)
                if raw:
                    written += write_corrected(db, site, model, scenario, raw, observed, reference)
    db.commit()
    return written