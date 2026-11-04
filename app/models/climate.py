"""
Projection climatique : les lieux étudiés et leur climat mensuel.

Aucune donnée en double :
  - la météo RÉELLE quotidienne (pluie, températures min et max, ET₀) reste
    dans weather_daily, pour les fermes comme pour les lieux étudiés : le
    climat la résume par mois au moment de l'affichage, sans la recopier ;
  - climate_monthly ne garde que ce qui n'existe nulle part ailleurs : les
    projections CMIP6 (brutes et corrigées de leurs biais), et les
    températures de surface de la terre et de la mer réelles (ERA5).

Les réglages (période, scénarios, modèles…) sont dans
app/core/climate_config.py.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base

#: États d'un lieu étudié
SITE_PENDING = "en_attente"
SITE_RUNNING = "en_cours"
SITE_READY = "pret"
SITE_FAILED = "echec"
SITE_STATUSES = (SITE_PENDING, SITE_RUNNING, SITE_READY, SITE_FAILED)

#: Sources du climat mensuel
SOURCE_ERA5 = "era5"
SOURCE_CMIP6 = "cmip6"


class ClimateSite(Base):
    """Un lieu étudié : une ferme d'AWAM, ou un point choisi sur la carte."""

    __tablename__ = "climate_site"
    __table_args__ = (
        CheckConstraint(
            "status IN ('en_attente', 'en_cours', 'pret', 'echec')", name="ck_climate_site_status"
        ),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_climate_site_latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_climate_site_longitude"),
        # Un seul lieu étudié par ferme
        Index("uq_climate_site_farm", "farm_id", unique=True, postgresql_where=text("farm_id IS NOT NULL")),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("utilisateur.id", ondelete="CASCADE"), nullable=False, index=True)
    #: Ferme liée ; vide pour un lieu étudié hors des fermes
    farm_id = Column(Integer, ForeignKey("farm.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(150), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    #: en_attente | en_cours | pret | echec
    status = Column(String(20), nullable=False, server_default=SITE_PENDING)
    #: Message de la dernière erreur (ex. refus de Copernicus)
    last_error = Column(Text, nullable=True)
    #: Dernier mois réel (ERA5) enregistré : seuls les mois suivants seront ajoutés
    era5_until = Column(Date, nullable=True)
    #: Distance (km) à la maille de mer la plus proche, mesurée en lisant la
    #: température de la mer : un lieu à moins de 50 km est dit côtier
    sea_distance_km = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    #: Climat mensuel, supprimé avec le lieu (cascade dans la base)
    months = relationship(
        "ClimateMonthly", back_populates="site", cascade="all, delete-orphan", passive_deletes=True
    )


class ClimateMonthly(Base):
    """
    Un mois de climat pour un lieu : soit une projection CMIP6 (un scénario,
    un modèle, brute ou corrigée), soit les températures de surface réelles
    (ERA5, sans scénario ni modèle).
    """

    __tablename__ = "climate_monthly"
    __table_args__ = (
        CheckConstraint("source IN ('era5', 'cmip6')", name="ck_climate_monthly_source"),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_climate_monthly_month"),
        CheckConstraint(
            "(source = 'era5' AND scenario = '' AND model = '' AND corrected = FALSE)"
            " OR (source = 'cmip6' AND scenario <> '' AND model <> '')",
            name="ck_climate_monthly_source_fields",
        ),
        # Un seul mois par lieu, source, scénario, modèle et correction
        UniqueConstraint(
            "site_id", "source", "scenario", "model", "corrected", "year", "month", name="uq_climate_monthly"
        ),
        Index("ix_climate_monthly_site_period", "site_id", "source", "scenario", "year", "month"),
    )

    id = Column(Integer, primary_key=True)
    site_id = Column(Integer, ForeignKey("climate_site.id", ondelete="CASCADE"), nullable=False)
    #: era5 (climat réel) | cmip6 (projection)
    source = Column(String(10), nullable=False)
    #: Vides ('') pour ERA5 ; ex. « ssp2_4_5 » et « ec_earth3_cc » pour CMIP6
    scenario = Column(String(20), nullable=False, server_default="")
    model = Column(String(40), nullable=False, server_default="")
    #: Projection corrigée de ses biais (vrai) ou brute (faux)
    corrected = Column(Boolean, nullable=False, server_default="false")
    year = Column(SmallInteger, nullable=False)
    month = Column(SmallInteger, nullable=False)

    precipitation_mm = Column(Float, nullable=True)
    air_temperature_c = Column(Float, nullable=True)
    air_temperature_max_c = Column(Float, nullable=True)
    air_temperature_min_c = Column(Float, nullable=True)
    land_surface_temperature_c = Column(Float, nullable=True)
    sea_surface_temperature_c = Column(Float, nullable=True)
    #: Évapotranspiration de référence, méthode de Hargreaves (FAO-56)
    et0_mm = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    site = relationship("ClimateSite", back_populates="months")