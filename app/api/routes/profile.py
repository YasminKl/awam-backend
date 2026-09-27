from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user, hash_password, verify_password
from app.db.database import get_db
from app.models.utilisateur import User
from app.schemas.profile import (
    UpdateFullNameRequest,
    UpdateUsernameRequest,
    UpdateEmailRequest,
    UpdatePasswordRequest,
    ProfileResponse,
)
from app.services.avatar_service import save_avatar, delete_avatar, get_avatar_signed_url
from datetime import datetime, timezone

router = APIRouter(prefix="/profile", tags=["profile"])


def build_profile_response(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "avatar_url": get_avatar_signed_url(user.avatar_url),
        "has_password": user.password_hash is not None,
    }


@router.get("", response_model=ProfileResponse)
async def get_profile(user: User = Depends(get_current_user)):
    return build_profile_response(user)


@router.put("/full-name", response_model=ProfileResponse)
async def update_full_name(
    payload: UpdateFullNameRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.full_name = payload.full_name
    db.commit()
    db.refresh(user)
    return build_profile_response(user)


@router.put("/username", response_model=ProfileResponse)
async def update_username(
    payload: UpdateUsernameRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(User).filter(User.username == payload.username, User.id != user.id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ce nom d'utilisateur est déjà pris.",
        )

    user.username = payload.username
    db.commit()
    db.refresh(user)
    return build_profile_response(user)


@router.put("/email", response_model=ProfileResponse)
async def update_email(
    payload: UpdateEmailRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(User).filter(User.email == payload.email, User.id != user.id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un compte existe déjà avec cette adresse e-mail.",
        )

    user.email = payload.email
    db.commit()
    db.refresh(user)
    return build_profile_response(user)


@router.put("/password", response_model=ProfileResponse)
async def update_password(
    payload: UpdatePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.password_hash:
        if not payload.current_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="L'ancien mot de passe est requis.",
            )
        if not verify_password(payload.current_password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Ancien mot de passe incorrect.",
            )

    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return build_profile_response(user)


@router.post("/avatar", response_model=ProfileResponse)
async def update_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    old_avatar_key = user.avatar_url

    new_avatar_key = await save_avatar(file, user.id)
    user.avatar_url = new_avatar_key
    db.commit()
    db.refresh(user)

    if old_avatar_key:
        delete_avatar(old_avatar_key)

    return build_profile_response(user)