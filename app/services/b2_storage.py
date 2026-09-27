import os
import time
import logging

import boto3
from botocore.config import Config

from app.core.config import (
    B2_KEY_ID,
    B2_APPLICATION_KEY,
    B2_BUCKET_NAME,
    B2_ENDPOINT_URL,
    B2_REGION,
)

logger = logging.getLogger(__name__)

# Taille maximale acceptée par put_object en une seule fois.
# Au-delà, on retombe sur upload_file (multipart) mais SANS nettoyage.
MAX_PUT_OBJECT_SIZE = 100 * 1024 * 1024


def get_b2_client():
    """
    Client boto3 configuré pour Backblaze B2 via l'API S3-compatible.
    IMPORTANT : B2_REGION doit correspondre à l'endpoint.
    """
    return boto3.client(
        "s3",
        endpoint_url=B2_ENDPOINT_URL,
        aws_access_key_id=B2_KEY_ID,
        aws_secret_access_key=B2_APPLICATION_KEY,
        region_name=B2_REGION,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def _delete_old_versions(key: str, protect_version_id: str | None) -> None:
    """
    Supprime toutes les anciennes versions d'un objet B2, SAUF celle
    passée dans protect_version_id.

    On NE touche PAS aux DeleteMarkers : les supprimer peut créer des
    états incohérents.

    Best-effort : une erreur ici ne doit jamais faire échouer l'upload.
    """
    if not protect_version_id:
        logger.warning("Pas de VersionId pour %s → nettoyage ignoré", key)
        return

    try:
        client = get_b2_client()
        paginator = client.get_paginator("list_object_versions")

        to_delete = []
        for page in paginator.paginate(Bucket=B2_BUCKET_NAME, Prefix=key):
            for v in page.get("Versions", []):
                if v["Key"] == key and v["VersionId"] != protect_version_id:
                    to_delete.append(v["VersionId"])
            # On ignore volontairement DeleteMarkers.

        if not to_delete:
            return

        objects = [{"Key": key, "VersionId": vid} for vid in to_delete]
        for i in range(0, len(objects), 1000):
            client.delete_objects(
                Bucket=B2_BUCKET_NAME,
                Delete={"Objects": objects[i:i + 1000], "Quiet": True},
            )

        logger.info(
            "🧹 %d ancienne(s) version(s) supprimée(s) pour %s (conservée : %s)",
            len(objects), key, protect_version_id,
        )

    except Exception as e:
        logger.warning("Nettoyage versions échoué pour %s : %s", key, e)


def _upload_put_object(local_path: str, key: str) -> tuple[str, str | None]:
    """
    Upload via put_object : charge le fichier en RAM puis l'envoie
    en une seule requête. Retourne (url, version_id).

    Utilisé quand le fichier est petit (< 100 Mo) ET qu'on veut
    récupérer le VersionId pour pouvoir nettoyer les anciennes
    versions.
    """
    client = get_b2_client()
    with open(local_path, "rb") as f:
        body = f.read()

    resp = client.put_object(
        Bucket=B2_BUCKET_NAME,
        Key=key,
        Body=body,
        ContentType="image/tiff",
    )
    version_id = resp.get("VersionId")
    logger.info("✅ Upload B2 OK (put_object) : %s (version %s)", key, version_id)

    return f"{B2_ENDPOINT_URL}/{B2_BUCKET_NAME}/{key}", version_id


def _upload_multipart(local_path: str, key: str) -> str:
    """
    Upload via upload_file (multipart géré par boto3).
    Ne retourne PAS le VersionId → pas de nettoyage automatique.

    Utilisé en fallback pour les gros fichiers (> 100 Mo).
    """
    client = get_b2_client()
    client.upload_file(
        local_path,
        B2_BUCKET_NAME,
        key,
        ExtraArgs={"ContentType": "image/tiff"},
    )
    logger.info("✅ Upload B2 OK (multipart) : %s", key)
    return f"{B2_ENDPOINT_URL}/{B2_BUCKET_NAME}/{key}"


def upload_file_to_b2(local_path: str, key: str, max_retries: int = 3) -> str:
    """
    Upload un fichier local vers B2.

    Comportement :
      - Fichier < 100 Mo : put_object + nettoyage des anciennes versions
      - Fichier ≥ 100 Mo : multipart (pas de nettoyage)

    Retourne l'URL B2 non signée (juste pour traçabilité en base).
    Pour un accès depuis le front, utilisez `generate_presigned_url`.
    """
    size = os.path.getsize(local_path)
    logger.info("📦 Upload %s (%d bytes)", local_path, size)

    if size < 1000:
        logger.warning(
            "🚨 Fichier suspect : %s ne fait que %d bytes",
            local_path, size,
        )

    use_single_put = size < MAX_PUT_OBJECT_SIZE

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            if use_single_put:
                url, version_id = _upload_put_object(local_path, key)
                # Nettoyage : garde uniquement la nouvelle version
                _delete_old_versions(key, protect_version_id=version_id)
                return url
            else:
                return _upload_multipart(local_path, key)

        except Exception as e:
            last_error = e
            logger.warning(
                "⚠️ Upload B2 échoué (%d/%d) : %s", attempt, max_retries, e
            )
            if attempt < max_retries:
                time.sleep(2 * attempt)

    raise last_error


def download_file_from_b2(key: str, local_path: str) -> None:
    """Télécharge un fichier depuis B2 (boto3 déchiffre SSE-B2 auto)."""
    client = get_b2_client()
    client.download_file(B2_BUCKET_NAME, key, local_path)


def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    """
    Génère une URL signée valable `expires_in` secondes.

    Le bucket B2 est privé → l'URL brute renvoie 403. Cette URL
    signée permet au navigateur / titiler de lire le COG.
    """
    client = get_b2_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": B2_BUCKET_NAME, "Key": key},
        ExpiresIn=expires_in,
    )


def check_encryption(key: str) -> dict:
    """
    Vérifie via HEAD si le fichier est chiffré (SSE-B2) ou verrouillé.
    Best-effort, ne lève jamais d'exception.
    """
    try:
        client = get_b2_client()
        resp = client.head_object(Bucket=B2_BUCKET_NAME, Key=key)

        info = {
            "sse": resp.get("ServerSideEncryption"),
            "sse_c": resp.get("SSECustomerAlgorithm"),
            "object_lock_mode": resp.get("ObjectLockMode"),
            "retain_until": resp.get("ObjectLockRetainUntilDate"),
            "legal_hold": resp.get("ObjectLockLegalHoldStatus"),
        }

        if info["object_lock_mode"]:
            logger.info(
                "🔒 [B2] Object Lock ACTIF sur %s : mode=%s",
                key, info["object_lock_mode"],
            )
        elif info["sse"]:
            logger.info("🔐 [B2] %s chiffré (SSE-B2 : %s)", key, info["sse"])
        else:
            logger.info("ℹ️ [B2] %s : ni chiffrement ni verrou détecté", key)

        return info

    except Exception as e:
        logger.warning("Vérification chiffrement échouée pour %s : %s", key, e)
        return {}