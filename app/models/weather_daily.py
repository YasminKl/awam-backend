"""
Modèle SQLAlchemy pour la table `weather_daily`.

Historique météo quotidien par ferme, issu d'Open-Meteo.

Une ligne = (ferme, jour). La météo est stockée par ferme et non par
parcelle : les données ont une maille d'environ 10 km, toutes les
parcelles d'une même ferme auraient donc les mêmes valeurs.

Table alimentée par :
  - la tâche Celery quotidienne `weather.sync_daily` (5 h 30) ;
  - le script `python -m app.scripts.backfill_weather` (historique).
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.db.database import Base


class WeatherDaily(Base):
    """Météo d'une journée pour une ferme."""

    __tablename__ = "weather_daily"

    __table_args__ = (
        UniqueConstraint("farm_id", "day", name="uq_weather_daily_farm_day"),
    )

    id = Column(Integer, primary_key=True, index=True)

    farm_id = Column(
        Integer,
        ForeignKey("farm.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Jour local de la ferme
    day = Column(Date, nullable=False, index=True)

    # ==================================================================
    # Mesures du jour
    # ==================================================================
    temperature_min_c = Column(Float, nullable=True)
    temperature_max_c = Column(Float, nullable=True)
    #: Pluie cumulée (mm)
    precipitation_mm = Column(Float, nullable=True)
    #: Évapotranspiration de référence FAO-56 (mm)
    et0_mm = Column(Float, nullable=True)
    #: Vent maximal à 10 m (km/h)
    wind_speed_max_kmh = Column(Float, nullable=True)
    #: Rayonnement solaire cumulé (MJ/m²)
    radiation_mj_m2 = Column(Float, nullable=True)

    # ==================================================================
    # Traçabilité
    # ==================================================================
    #: Point interrogé (coordonnées de la ferme ou centre de ses parcelles)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    #: "archive" (réanalyse, définitive) ou "forecast" (jours récents)
    source = Column(String(20), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )