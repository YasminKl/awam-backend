"""
Routes API pour les rasters, rattachées à une parcelle précise.

- /raster/current : dernier raster d'un type donné pour une parcelle
- /raster/list     : historique des rasters d'une parcelle
- /raster/upload   : upload manuel (fallback), toujours rattaché à une parcelle
"""

import os
import uuid
import tempfile
import logging
from datetime import date as date_type
from typing import get_args

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.utilisateur import User
from app.api.routes.auth import get_current_user
from app.schemas.raster import RasterType, RasterResponse, RasterCurrentResponse
from app.services.b2_storage import upload_file_to_b2, generate_presigned_url

from rio_cogeo.cogeo import cog_translate, cog_validate
from rio_cogeo.profiles import cog_profiles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/raster", tags=["raster"])

ALLOWED_EXTENSIONS = {".tif", ".tiff"}
RASTER_TYPES = set(get_args(RasterType))

COG_PROFILE = cog_profiles.get("deflate")
COG_PROFILE.update({
    "BLOCKSIZE": 512,
    "OVERVIEW_RESAMPLING": "bilinear",
    "BIGTIFF": "IF_SAFER",
})


def _get_owned_parcel(parcel_id: int, user_id: int, db: Session):
    """Vérifie que la parcelle appartient à l'utilisateur, renvoie (id, farm_id)."""
    row = db.execute(
        text("""
            SELECT p.id, p.farm_id
            FROM parcel p
            JOIN farm f ON f.id = p.farm_id
            WHERE p.id = :parcel_id AND f.user_id = :user_id
        """),
        {"parcel_id": parcel_id, "user_id": user_id},
    ).fetchone()
    if not row:
        raise HTTPException(404, "Parcelle introuvable.")
    return row


def _convert_to_cog(src_path: str, dst_path: str) -> None:
    """Convertit un GeoTIFF en Cloud Optimized GeoTIFF (COG), 100% Python."""
    try:
        cog_translate(src_path, dst_path, COG_PROFILE, in_memory=False, quiet=True)
    except Exception as exc:
        logger.exception("Échec de la conversion COG")
        raise HTTPException(500, f"Erreur conversion COG : {exc}") from exc

    is_valid, _, errors = cog_validate(dst_path, quiet=True)
    if not is_valid:
        logger.warning("COG généré mais validation échouée : %s", errors)


# ---------- Upload manuel d'un .tif pour une parcelle ----------

@router.post("/upload", response_model=RasterResponse)
async def upload_raster(
    file: UploadFile = File(...),
    parcel_id: int = Form(...),
    raster_type: str = Form(...),
    scene_date: str | None = Form(
        None, description="Format YYYY-MM-DD, requis pour sentinel_rgb/sentinel_ndvi"
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    parcel = _get_owned_parcel(parcel_id, current_user.id, db)

    if raster_type not in RASTER_TYPES:
        raise HTTPException(
            400,
            f"raster_type invalide. Attendu: {', '.join(sorted(RASTER_TYPES))}",
        )

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Seuls les fichiers .tif/.tiff sont acceptés.")

    parsed_date: date_type | None = None
    if scene_date:
        try:
            parsed_date = date_type.fromisoformat(scene_date)
        except ValueError:
            raise HTTPException(400, "scene_date invalide, format attendu YYYY-MM-DD.")

    with tempfile.TemporaryDirectory() as tmpdir:
        uid = uuid.uuid4().hex
        input_path = os.path.join(tmpdir, f"{uid}_input.tif")
        cog_path = os.path.join(tmpdir, f"{uid}_cog.tif")

        with open(input_path, "wb") as f:
            f.write(await file.read())

        _convert_to_cog(input_path, cog_path)

        if raster_type == "mask":
            key = f"rasters/farm_{parcel.farm_id}/parcel_{parcel_id}/mask/mask.tif"
        else:
            date_label = scene_date or "manual"
            key = (
                f"rasters/farm_{parcel.farm_id}/parcel_{parcel_id}/"
                f"{raster_type}/{date_label}_{uuid.uuid4().hex[:8]}.tif"
            )

        url = upload_file_to_b2(cog_path, key)

    if raster_type == "mask":
        db.execute(
            text("DELETE FROM raster WHERE parcel_id = :parcel_id AND raster_type = 'mask'"),
            {"parcel_id": parcel_id},
        )

    row = db.execute(
        text("""
            INSERT INTO raster (parcel_id, raster_type, scene_date, b2_key, b2_url)
            VALUES (:parcel_id, :raster_type, :scene_date, :b2_key, :b2_url)
            RETURNING id, parcel_id, raster_type, scene_date, b2_url, created_at
        """),
        {
            "parcel_id": parcel_id,
            "raster_type": raster_type,
            "scene_date": parsed_date,
            "b2_key": key,
            "b2_url": url,
        },
    ).fetchone()
    db.commit()

    return RasterResponse(
        id=row.id,
        parcel_id=row.parcel_id,
        raster_type=row.raster_type,
        scene_date=row.scene_date,
        url=row.b2_url,
        created_at=row.created_at,
    )


# ---------- Historique des rasters d'une parcelle ----------

@router.get("/list", response_model=list[RasterResponse])
async def list_rasters(
    parcel_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_parcel(parcel_id, current_user.id, db)

    rows = db.execute(
        text("""
            SELECT id, parcel_id, raster_type, scene_date, b2_url, created_at
            FROM raster
            WHERE parcel_id = :parcel_id
            ORDER BY COALESCE(scene_date, created_at::date) DESC, created_at DESC
        """),
        {"parcel_id": parcel_id},
    ).fetchall()

    return [
        RasterResponse(
            id=r.id,
            parcel_id=r.parcel_id,
            raster_type=r.raster_type,
            scene_date=r.scene_date,
            url=r.b2_url,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ---------- Raster courant d'une parcelle (URL signée fraîche) ----------

@router.get("/current", response_model=RasterCurrentResponse)
async def get_current_raster(
    parcel_id: int = Query(..., description="ID de la parcelle"),
    type: str = Query(..., description="mask | sentinel_rgb | sentinel_ndvi"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Retourne une URL signée vers le raster le plus récent du type demandé,
    pour la parcelle donnée. Le bucket B2 étant privé, l'URL brute stockée
    en base ne suffit pas : on génère une URL présignée à chaque appel.
    """
    _get_owned_parcel(parcel_id, current_user.id, db)

    if type not in RASTER_TYPES:
        raise HTTPException(
            400,
            f"type invalide. Attendu: {', '.join(sorted(RASTER_TYPES))}",
        )

    row = db.execute(
        text("""
            SELECT b2_key, scene_date, bbox_west, bbox_south, bbox_east, bbox_north
            FROM raster
            WHERE parcel_id = :parcel_id AND raster_type = :type
            ORDER BY COALESCE(scene_date, created_at::date) DESC, created_at DESC
            LIMIT 1
        """),
        {"parcel_id": parcel_id, "type": type},
    ).fetchone()

    if not row:
        return {"url": None, "scene_date": None, "bbox": None}

    signed_url = generate_presigned_url(row.b2_key, expires_in=3600)

    bbox = None
    if None not in (row.bbox_west, row.bbox_south, row.bbox_east, row.bbox_north):
        bbox = [row.bbox_west, row.bbox_south, row.bbox_east, row.bbox_north]

    return {"url": signed_url, "scene_date": row.scene_date, "bbox": bbox}