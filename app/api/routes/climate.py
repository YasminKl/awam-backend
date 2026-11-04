"""
Routes de la projection climatique.

  GET    /api/climate/sites              lieux étudiés (ceux des fermes sont créés au besoin)
  POST   /api/climate/sites              étudier un nouveau lieu
  DELETE /api/climate/sites/{id}         retirer un lieu (pas celui d'une ferme)
  POST   /api/climate/sites/{id}/retry   relancer un lieu en échec
  GET    /api/climate/sites/{id}/summary séries, chiffres clés et écart, pour un scénario

La préparation (téléchargements Copernicus, correction des biais) est
faite en arrière-plan par Celery : une route ne l'attend jamais. Si le
lancement immédiat échoue (Redis indisponible), la tâche horaire
« climate.check_pending » le rattrape.
"""

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.core import climate_config as cfg
from app.db.database import get_db
from app.models.climate import SITE_FAILED, SITE_PENDING, ClimateSite
from app.models.utilisateur import User
from app.schemas.climate import ClimateSiteCreate, ClimateSiteResponse
from app.services import climate_projection_service as projection
from app.services import climate_summary_service as summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/climate", tags=["climate"])


def _launch(site_id: int) -> None:
    """Lance la préparation en arrière-plan ; sinon, la tâche horaire s'en chargera."""
    try:
        from app.tasks.climate_tasks import prepare_site

        prepare_site.delay(site_id)
    except Exception as exc:  # noqa: BLE001 — Redis indisponible : rattrapé par check_pending
        logger.warning("🌍 [climat] lancement différé du lieu %s : %s", site_id, exc)


def _own_site(db: Session, site_id: int, user: User) -> ClimateSite:
    site = db.query(ClimateSite).filter(ClimateSite.id == site_id, ClimateSite.user_id == user.id).first()
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lieu introuvable.")
    return site


@router.get("/sites", response_model=list[ClimateSiteResponse])
async def list_sites(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Les lieux étudiés : d'abord les fermes, puis les autres lieux, par nom."""
    for site_id in projection.ensure_farm_sites(db, user.id):
        _launch(site_id)
    sites = db.query(ClimateSite).filter(ClimateSite.user_id == user.id).all()
    return sorted(sites, key=lambda s: (s.farm_id is None, s.name.lower()))


@router.post("/sites", response_model=ClimateSiteResponse, status_code=status.HTTP_201_CREATED)
async def create_site(
    payload: ClimateSiteCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Étudier un nouveau lieu : il est créé « en attente », puis préparé en arrière-plan."""
    site = ClimateSite(
        user_id=user.id, name=payload.name.strip(),
        latitude=round(payload.latitude, 4), longitude=round(payload.longitude, 4),
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    _launch(site.id)
    return site


@router.delete("/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(site_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Retire un lieu étudié et son climat. Le lieu d'une ferme part avec la ferme."""
    site = _own_site(db, site_id, user)
    if site.farm_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le climat d'une ferme est retiré avec la ferme elle-même.",
        )
    db.delete(site)
    db.commit()


@router.post("/sites/{site_id}/retry", response_model=ClimateSiteResponse)
async def retry_site(site_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Relance la préparation d'un lieu en échec."""
    site = _own_site(db, site_id, user)
    if site.status == SITE_FAILED:
        site.status = SITE_PENDING
        site.last_error = None
        db.commit()
        db.refresh(site)
        _launch(site.id)
    return site


@router.get("/sites/{site_id}/summary")
async def site_summary(
    site_id: int,
    scenario: Optional[str] = Query(None, description="Scénario (par défaut : le premier de la configuration)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Séries année par année, chiffres clés et surveillance de l'écart, pour un scénario."""
    site = _own_site(db, site_id, user)
    scenario = scenario or cfg.SCENARIOS[0]
    if scenario not in cfg.SCENARIOS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scénario inconnu.")
    return summary.site_summary(db, site, scenario, last_complete_year=date.today().year - 1)