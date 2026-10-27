"""
Accès contrôlé au serveur de tuiles (titiler, routes /cog/…).

Titiler lit l'image désignée par le paramètre `url` de chaque requête. Sans
contrôle, n'importe qui pourrait lui faire lire N'IMPORTE QUELLE adresse :
un serveur interne, un fichier local de la machine (GDAL accepte les
chemins), ou un très gros fichier pour surcharger le serveur. C'est une
faille de type SSRF (Server-Side Request Forgery).

Deux protections, appliquées dans app/main.py :
  1. authentification obligatoire sur toutes les routes /cog/… ;
  2. `url` limitée aux images du bucket B2 du projet : les adresses
     signées produites par b2_storage.generate_presigned_url, de la forme
         {B2_ENDPOINT_URL}/{B2_BUCKET_NAME}/rasters/…?X-Amz-…
     Tout le reste est refusé (403).
"""

from __future__ import annotations

import posixpath
from urllib.parse import unquote, urlparse

from fastapi import HTTPException, Query, status

from app.core.config import B2_BUCKET_NAME, B2_ENDPOINT_URL


def _allowed_origin() -> tuple[str, str, str]:
    """Schéma, hôte et préfixe de chemin autorisés : le bucket B2 du projet."""
    endpoint = urlparse(B2_ENDPOINT_URL)
    return endpoint.scheme, endpoint.netloc.lower(), f"/{B2_BUCKET_NAME}/"


def is_allowed_cog_url(url: str) -> bool:
    """Vrai si `url` désigne une image du bucket B2 du projet, en HTTPS."""
    if not url:
        return False
    parsed = urlparse(url)
    scheme, host, prefix = _allowed_origin()
    if parsed.scheme.lower() != "https" or scheme.lower() != "https":
        return False
    if parsed.netloc.lower() != host:
        return False
    # Chemin normalisé : refuse les détours du type /bucket/../autre
    path = posixpath.normpath(unquote(parsed.path))
    return (path + "/").startswith(prefix) and path != prefix.rstrip("/")


def b2_cog_url(
    url: str = Query(..., description="Adresse signée d'une image du bucket B2 du projet"),
) -> str:
    """
    Dépendance de titiler (path_dependency) : renvoie l'adresse de l'image
    si elle appartient au bucket du projet, sinon refuse la requête.
    """
    if not is_allowed_cog_url(url):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Adresse d'image non autorisée : seules les images du projet sont servies.",
        )
    return url