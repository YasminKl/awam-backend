"""
Schémas Pydantic pour la table `indice_reading`.

Expose les 22 colonnes du modèle SQLAlchemy pour que le frontend
puisse afficher :
  - la valeur moyenne de l'indice
  - la dispersion (min/max/std/median)
  - la fiabilité (valid_pixels, valid_ratio, is_valid)
  - les infos de la scène Sentinel-2 (scene_id, scene_date, cloud_cover)
  - la clé B2 du COG (pour Titiler)
"""

from datetime import date, datetime

from pydantic import BaseModel, Field


# ======================================================================
# Schéma de base — utilisé pour toutes les réponses
# ======================================================================


class IndiceReadingBase(BaseModel):
    """Champs communs à toutes les réponses d'indice."""

    # Identification
    indice_name: str = Field(..., description="Nom de l'indice (NDVI, NDMI, ...)")

    # Statistiques zonales
    value: float | None = Field(None, description="Moyenne des pixels valides")
    min_value: float | None = Field(None, description="Valeur minimale observée")
    max_value: float | None = Field(None, description="Valeur maximale observée")
    std_value: float | None = Field(None, description="Écart-type (hétérogénéité)")
    median_value: float | None = Field(None, description="Valeur médiane (robuste outliers)")

    # Qualité de la donnée
    valid_pixels: int | None = Field(None, description="Pixels valides (hors nuages/nodata)")
    cloud_pixels: int | None = Field(None, description="Pixels masqués par SCL")
    total_pixels: int | None = Field(None, description="Pixels total dans la parcelle")
    valid_ratio: float | None = Field(None, description="Ratio valid_pixels / total_pixels (0-1)")

    # Bornes théoriques et validation
    theoretical_min: float | None = Field(None, description="Borne théorique minimale")
    theoretical_max: float | None = Field(None, description="Borne théorique maximale")
    is_valid: bool | None = Field(None, description="True si toutes les valeurs sont dans les bornes")
    validation_error: str | None = Field(None, description="Message d'erreur si is_valid=False")

    # Traçabilité de la scène
    scene_id: str | None = Field(None, description="ID unique de la scène Sentinel-2")
    scene_date: date | None = Field(None, description="Date de la scène")
    cloud_cover: float | None = Field(None, description="% de nuages global de la scène")
    b2_key: str | None = Field(None, description="Clé B2 du COG (pour Titiler)")

    # Dates
    recorded_at: datetime | None = Field(None, description="Date/heure de la scène")


# ======================================================================
# Schéma complet — renvoyé par GET /parcels/{id}/indices
# ======================================================================


class IndiceReadingResponse(IndiceReadingBase):
    """
    Réponse complète d'une ligne indice_reading.

    Toutes les colonnes de la DB sont exposées (sauf created_at technique).
    """

    id: int
    parcel_id: int

    class Config:
        from_attributes = True


# ======================================================================
# Schémas pour l'API — valeurs par indice (format pivoté)
# ======================================================================


class IndiceValueCompact(BaseModel):
    """
    Version ultra-compacte pour les courbes temporelles.

    Utilisée par GET /parcels/{id}/indices/timeseries
    """

    scene_date: date
    value: float | None
    is_valid: bool | None


class IndiceDetailCompact(BaseModel):
    """
    Version compacte avec stats essentielles.

    Utilisée par GET /parcels/{id}/indices/latest
    """

    indice_name: str
    value: float | None
    min_value: float | None
    max_value: float | None
    std_value: float | None
    valid_ratio: float | None
    is_valid: bool | None
    scene_date: date | None


# ======================================================================
# Schéma pour la liste des indices disponibles
# ======================================================================


class IndiceAvailableResponse(BaseModel):
    """Liste des indices disponibles pour une parcelle."""

    parcel_id: int
    indices: list[str] = Field(..., description="Noms des indices disponibles")
    count: int = Field(..., description="Nombre d'indices disponibles")

