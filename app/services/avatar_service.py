# app/services/avatar_service.py
import uuid
import cloudinary
import cloudinary.uploader
from fastapi import UploadFile, HTTPException, status

from app.core.config import (
    CLOUDINARY_CLOUD_NAME,
    CLOUDINARY_API_KEY,
    CLOUDINARY_API_SECRET,
)

# Configuration Cloudinary
cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 Mo


async def save_avatar(file: UploadFile, user_id: int) -> str:
    """Upload un avatar sur Cloudinary"""
    
    # Vérifier l'extension
    extension = ""
    if file.filename and "." in file.filename:
        extension = f".{file.filename.rsplit('.', 1)[-1].lower()}"
    
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'image non autorisé. Utilisez JPG, PNG, WEBP ou GIF.",
        )

    # Lire le contenu
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'image ne doit pas dépasser 5 Mo.",
        )

    try:
        # Upload vers Cloudinary
        result = cloudinary.uploader.upload(
            contents,
            folder=f"avatars/{user_id}",
            public_id=str(uuid.uuid4()),
            resource_type="image",
            transformation=[
                {"width": 500, "height": 500, "crop": "fill"},
                {"quality": "auto"}
            ]
        )
        
        return result.get("secure_url")
        
    except Exception as e:
        print(f"🔴 Erreur Cloudinary: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'envoi de l'image.",
        )


def get_avatar_signed_url(object_key: str) -> str | None:
    """Retourne l'URL de l'avatar (Cloudinary génère déjà des URLs publiques)"""
    if not object_key:
        return None
    return object_key


def delete_avatar(object_key: str) -> None:
    """Supprime un avatar de Cloudinary"""
    if not object_key:
        return

    try:
        # Extraire le public_id de l'URL Cloudinary
        parts = object_key.split("/")
        
        if "upload" in parts:
            upload_index = parts.index("upload")
            public_id_parts = parts[upload_index + 1:]
            public_id = "/".join(public_id_parts).split(".")[0]
        else:
            public_id = "/".join(parts[-3:]).split(".")[0]
        
        cloudinary.uploader.destroy(public_id)
        print(f"✅ Avatar supprimé: {public_id}")
    except Exception as e:
        print(f"🔴 Erreur suppression avatar: {e}")
        pass