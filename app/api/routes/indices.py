"""
Routes API pour consulter les indices spectraux d'une parcelle.

Endpoints :
  - GET /parcels/{id}/indices                  → liste complète (avec filtres)
  - GET /parcels/{id}/indices/available        → noms des indices dispo
  - GET /parcels/{id}/indices/latest           → 1 valeur par indice (dernière scène)
  - GET /parcels/{id}/indices/timeseries       → courbe temporelle d'un indice
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.db.database import get_db
from app.models.farm import Farm
from app.models.indice_reading import IndiceReading
from app.models.parcel import Parcel
from app.models.utilisateur import User
from app.schemas.indice_reading import (
    IndiceAvailableResponse,
    IndiceDetailCompact,
    IndiceReadingResponse,
    IndiceValueCompact,
)

router = APIRouter(tags=["indices"])


# ----------------------------------------------------------------------
# Helper : vérifier que la parcelle appartient à l'utilisateur
# ----------------------------------------------------------------------


def _get_owned_parcel(parcel_id: int, user: User, db: Session) -> Parcel:
    """Récupère une parcelle uniquement si elle appartient à l'utilisateur courant."""
    parcel = (
        db.query(Parcel)
        .join(Farm)
        .filter(Parcel.id == parcel_id, Farm.user_id == user.id)
        .first()
    )
    if not parcel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parcelle introuvable.",
        )
    return parcel


# ----------------------------------------------------------------------
# GET /parcels/{id}/indices — liste complète
# ----------------------------------------------------------------------


@router.get(
    "/parcels/{parcel_id}/indices",
    response_model=List[IndiceReadingResponse],
)
async def list_parcel_indices(
    parcel_id: int,
    indice_name: Optional[str] = Query(
        None, description="Filtrer sur un indice (ex: NDVI)"
    ),
    scene_date: Optional[str] = Query(
        None, description="Filtrer sur une date (YYYY-MM-DD)"
    ),
    only_valid: bool = Query(
        False, description="Ne retourner que les indices is_valid=True"
    ),
    limit: int = Query(
        500, ge=1, le=5000, description="Nombre max de résultats"
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Liste les indices spectraux d'une parcelle.

    Filtres disponibles :
      - **indice_name** : filtrer sur un indice (NDVI, NDMI, ...)
      - **scene_date** : filtrer sur une date précise (YYYY-MM-DD)
      - **only_valid** : exclure les données is_valid=False
      - **limit** : nombre max de résultats (défaut 500)
    """
    _get_owned_parcel(parcel_id, user, db)

    query = db.query(IndiceReading).filter(
        IndiceReading.parcel_id == parcel_id
    )

    if indice_name:
        query = query.filter(IndiceReading.indice_name == indice_name.upper())

    if scene_date:
        query = query.filter(
            text("scene_date = :sd").bindparams(sd=scene_date)
        )

    if only_valid:
        query = query.filter(IndiceReading.is_valid.is_(True))

    return (
        query.order_by(IndiceReading.scene_date, IndiceReading.indice_name)
        .limit(limit)
        .all()
    )


# ----------------------------------------------------------------------
# GET /parcels/{id}/indices/available — noms des indices dispo
# ----------------------------------------------------------------------


@router.get(
    "/parcels/{parcel_id}/indices/available",
    response_model=IndiceAvailableResponse,
)
async def list_available_indices(
    parcel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Liste les noms des indices disponibles pour une parcelle
    (ceux qui ont au moins une ligne en base).
    """
    _get_owned_parcel(parcel_id, user, db)

    rows = (
        db.query(IndiceReading.indice_name)
        .filter(IndiceReading.parcel_id == parcel_id)
        .distinct()
        .order_by(IndiceReading.indice_name)
        .all()
    )
    names = [r[0] for r in rows]

    return IndiceAvailableResponse(
        parcel_id=parcel_id,
        indices=names,
        count=len(names),
    )


# ----------------------------------------------------------------------
# GET /parcels/{id}/indices/latest — 1 valeur par indice (dernière scène)
# ----------------------------------------------------------------------


@router.get(
    "/parcels/{parcel_id}/indices/latest",
    response_model=List[IndiceDetailCompact],
)
async def get_latest_indices(
    parcel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Renvoie la dernière valeur (scène la plus récente) pour chaque indice.

    Utile pour afficher un tableau de bord avec les valeurs actuelles.

    Exemple de réponse :
      [
        {"indice_name": "NDVI", "value": 0.32, "min_value": 0.12, ...},
        {"indice_name": "NDMI", "value": -0.001, ...},
        ...
      ]
    """
    _get_owned_parcel(parcel_id, user, db)

    # Requête SQL : pour chaque indice, on prend la ligne la plus récente
    rows = db.execute(
        text("""
            SELECT DISTINCT ON (indice_name)
                indice_name, value, min_value, max_value, std_value,
                valid_ratio, is_valid, scene_date
            FROM indice_reading
            WHERE parcel_id = :parcel_id
            ORDER BY indice_name, scene_date DESC, id DESC
        """),
        {"parcel_id": parcel_id},
    ).fetchall()

    return [
        IndiceDetailCompact(
            indice_name=r.indice_name,
            value=r.value,
            min_value=r.min_value,
            max_value=r.max_value,
            std_value=r.std_value,
            valid_ratio=r.valid_ratio,
            is_valid=r.is_valid,
            scene_date=r.scene_date,
        )
        for r in rows
    ]


# ----------------------------------------------------------------------
# GET /parcels/{id}/indices/timeseries — courbe temporelle d'un indice
# ----------------------------------------------------------------------


@router.get(
    "/parcels/{parcel_id}/indices/timeseries",
    response_model=List[IndiceValueCompact],
)
async def get_indice_timeseries(
    parcel_id: int,
    indice_name: str = Query(..., description="Nom de l'indice (ex: NDVI)"),
    days: int = Query(
        180, ge=1, le=1095, description="Nombre de jours en arrière (défaut 180)"
    ),
    only_valid: bool = Query(
        True, description="Ne garder que les valeurs is_valid=True"
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Renvoie la courbe temporelle d'un indice pour une parcelle.

    Utile pour afficher l'évolution du NDVI sur les 6 derniers mois.

    Exemple :
      GET /parcels/71/indices/timeseries?indice_name=NDVI&days=180
      → [
          {"scene_date": "2026-04-15", "value": 0.45, "is_valid": true},
          {"scene_date": "2026-05-02", "value": 0.52, "is_valid": true},
          ...
        ]
    """
    _get_owned_parcel(parcel_id, user, db)

    query = (
        db.query(IndiceReading)
        .filter(
            IndiceReading.parcel_id == parcel_id,
            IndiceReading.indice_name == indice_name.upper(),
        )
    )

    if only_valid:
        query = query.filter(IndiceReading.is_valid.is_(True))

    # Filtrer sur les N derniers jours
    query = query.filter(
        text(f"scene_date >= CURRENT_DATE - INTERVAL '{int(days)} days'")
    )

    rows = query.order_by(IndiceReading.scene_date).all()

    return [
        IndiceValueCompact(
            scene_date=r.scene_date,
            value=r.value,
            is_valid=r.is_valid,
        )
        for r in rows
        if r.scene_date is not None
    ]