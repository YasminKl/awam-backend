"""
Modèle SQLAlchemy pour la table `indice_reading`.

Stocke les statistiques zonales des indices spectraux calculés
pour chaque parcelle et chaque scène Sentinel-2.

Une ligne = (parcelle, indice, scène).

Table alimentée par la tâche Celery `parcels.ingest_parcel_rasters`.
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    Text,
    Date,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class IndiceReading(Base):
    """
    Statistiques d'un indice spectral pour une parcelle et une scène.

    Exemples d'indices : NDVI, NDMI, NDWI, NDRE, EVI, SAVI, MSAVI,
    NBR, REDEDGE, VARI, CARBONATE, SI_SOIL, PSRI, FCOVER.
    """

    __tablename__ = "indice_reading"

    # ==================================================================
    # Identifiants
    # ==================================================================
    id = Column(Integer, primary_key=True, index=True)

    # ==================================================================
    # Lien avec la parcelle
    # ==================================================================
    parcel_id = Column(
        Integer,
        ForeignKey("parcel.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ==================================================================
    # Identification de l'indice
    # ==================================================================
    indice_name = Column(
        String(50),
        nullable=False,
        index=True,
        comment="Nom de l'indice : NDVI, NDMI, NDWI, NDRE, EVI, SAVI, "
                "MSAVI, NBR, REDEDGE, VARI, CARBONATE, SI_SOIL, PSRI, FCOVER",
    )

    # ==================================================================
    # Statistiques observées (calculées sur la géométrie de la parcelle)
    # ==================================================================
    value = Column(
        Float,
        nullable=True,
        comment="Moyenne des pixels valides sur la parcelle",
    )
    min_value = Column(
        Float,
        nullable=True,
        comment="Valeur minimale observée sur la parcelle",
    )
    max_value = Column(
        Float,
        nullable=True,
        comment="Valeur maximale observée sur la parcelle",
    )
    std_value = Column(
        Float,
        nullable=True,
        comment="Écart-type (hétérogénéité de la parcelle)",
    )
    median_value = Column(
        Float,
        nullable=True,
        comment="Valeur médiane (robuste aux outliers)",
    )

    # ==================================================================
    # Qualité de la donnée
    # ==================================================================
    valid_pixels = Column(
        Integer,
        nullable=True,
        comment="Nombre de pixels valides (hors nuages et hors nodata)",
    )
    cloud_pixels = Column(
        Integer,
        nullable=True,
        comment="Nombre de pixels masqués par SCL (nuages/ombres/neige)",
    )
    total_pixels = Column(
        Integer,
        nullable=True,
        comment="Nombre total de pixels dans la parcelle",
    )
    valid_ratio = Column(
        Float,
        nullable=True,
        comment="Ratio de validité = valid_pixels / total_pixels (0 à 1)",
    )

    # ==================================================================
    # Bornes théoriques et validation
    # ==================================================================
    theoretical_min = Column(
        Float,
        nullable=False,
        comment="Borne théorique minimale de l'indice (ex: -1.0 pour NDVI)",
    )
    theoretical_max = Column(
        Float,
        nullable=False,
        comment="Borne théorique maximale de l'indice (ex: 1.0 pour NDVI)",
    )
    is_valid = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
        comment="True si toutes les valeurs respectent les bornes théoriques",
    )
    validation_error = Column(
        Text,
        nullable=True,
        comment="Message d'erreur si is_valid=False",
    )

    # ==================================================================
    # Traçabilité de la scène Sentinel-2
    # ==================================================================
    scene_id = Column(
        String(255),
        nullable=True,
        index=True,
        comment="ID unique de la scène : S2B_MSIL2A_20260909T101019_...",
    )
    scene_date = Column(
        Date,
        nullable=True,
        index=True,
        comment="Date de la scène (YYYY-MM-DD)",
    )
    cloud_cover = Column(
        Float,
        nullable=True,
        comment="% de nuages global de la scène",
    )
    b2_key = Column(
        String(500),
        nullable=True,
        comment="Clé B2 du COG généré (ex: rasters/farm_X/parcel_Y/sentinel_ndvi/2026-09-09.tif)",
    )

    # ==================================================================
    # Dates
    # ==================================================================
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="Date de la scène (datetime complet avec timezone)",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Date d'insertion en base (technique)",
    )

    # ==================================================================
    # Relation
    # ==================================================================
    parcel = relationship("Parcel", back_populates="indice_readings")

    # ==================================================================
    # Contraintes et index
    # ==================================================================
    __table_args__ = (
        # ✅ Unicité : une seule ligne par (parcelle, indice, scène)
        UniqueConstraint(
            "parcel_id",
            "indice_name",
            "scene_id",
            name="uq_indice_reading_parcel_indice_scene",
        ),
        # ✅ Index composite pour requêtes fréquentes
        Index(
            "ix_indice_reading_parcel_indice_date",
            "parcel_id",
            "indice_name",
            "scene_date",
        ),
        # ✅ Index pour filtrer les données valides
        Index(
            "ix_indice_reading_valid",
            "is_valid",
        ),
    )

    # ==================================================================
    # Représentation pour debug
    # ==================================================================
    def __repr__(self) -> str:
        return (
            f"<IndiceReading(id={self.id}, "
            f"parcel_id={self.parcel_id}, "
            f"indice={self.indice_name}, "
            f"value={self.value}, "
            f"scene_date={self.scene_date})>"
        )