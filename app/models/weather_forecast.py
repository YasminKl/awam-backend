"""
Modèle SQLAlchemy pour la table `weather_forecast`.

Les prévisions d'Open-Meteo pour les prochains jours, par ferme. Elles sont
gardées À PART de weather_daily, qui ne contient que du climat réel : les
pages qui calculent avec le climat réel (Projection climatique, Suivi de
l'irrigation, bilan hydrique) ne voient jamais de jours futurs.

Remplacées chaque matin par la tâche `weather.sync_daily` ; elles servent
aux alertes de la page Climat et cultures (gel, forte chaleur annoncés).
"""

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.sql import func

from app.db.database import Base


class WeatherForecast(Base):
    """Prévision d'une journée pour une ferme."""

    __tablename__ = "weather_forecast"
    __table_args__ = (UniqueConstraint("farm_id", "day", name="uq_weather_forecast_farm_day"),)

    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farm.id", ondelete="CASCADE"), nullable=False, index=True)
    day = Column(Date, nullable=False)

    temperature_min_c = Column(Float, nullable=True)
    temperature_max_c = Column(Float, nullable=True)
    temperature_mean_c = Column(Float, nullable=True)
    precipitation_mm = Column(Float, nullable=True)
    et0_mm = Column(Float, nullable=True)
    wind_speed_max_kmh = Column(Float, nullable=True)
    wind_speed_mean_kmh = Column(Float, nullable=True)
    humidity_mean_pct = Column(Float, nullable=True)
    radiation_mj_m2 = Column(Float, nullable=True)

    #: Moment où la prévision a été reçue
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)