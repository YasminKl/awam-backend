"""
Modèle SQLAlchemy pour la table `weather_daily`.

Historique météo quotidien, issu d'Open-Meteo, pour une ferme OU pour un
lieu étudié par la projection climatique (climate_site) : c'est la seule
source de la météo réelle quotidienne, le climat ne la recopie pas.

Une ligne = (ferme, jour) ou (lieu étudié, jour). La météo est stockée par ferme et non par
parcelle : les données ont une maille d'environ 10 km, toutes les
parcelles d'une même ferme auraient donc les mêmes valeurs.

Table alimentée par :
  - la tâche Celery quotidienne `weather.sync_daily` (5 h 30) ;
  - le script `python -m app.scripts.backfill_weather` (historique).
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    String,
    Float,
    Date,
    Index,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.sql import func, text

from app.db.database import Base


class WeatherDaily(Base):
    """Météo d'une journée pour une ferme, ou pour un lieu étudié."""

    __tablename__ = "weather_daily"

    __table_args__ = (
        UniqueConstraint("farm_id", "day", name="uq_weather_daily_farm_day"),
        # Un seul jour par lieu étudié
        Index("uq_weather_daily_site_day", "site_id", "day", unique=True, postgresql_where=text("site_id IS NOT NULL")),
        # Une ligne appartient à une ferme OU à un lieu étudié, jamais aux deux
        CheckConstraint("(farm_id IS NOT NULL) <> (site_id IS NOT NULL)", name="ck_weather_daily_owner"),
    )

    id = Column(Integer, primary_key=True, index=True)

    #: Ferme ; vide pour un lieu étudié
    farm_id = Column(
        Integer,
        ForeignKey("farm.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    #: Lieu étudié par la projection climatique ; vide pour une ferme
    site_id = Column(
        Integer,
        ForeignKey("climate_site.id", ondelete="CASCADE"),
        nullable=True,
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
        #: Température moyenne de la journée (°C)
    temperature_mean_c = Column(Float, nullable=True)
    #: Humidité relative moyenne de l'air (%)
    humidity_mean_pct = Column(Float, nullable=True)
    #: Vent moyen à 10 m (km/h)
    wind_speed_mean_kmh = Column(Float, nullable=True)

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