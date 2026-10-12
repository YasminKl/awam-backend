from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base


class ParcelAnalysis(Base):
    """
    Analyse de laboratoire d'une parcelle (sol et eau d'irrigation).

    Une parcelle peut en avoir plusieurs : chaque analyse est datée, la plus
    récente est celle affichée sur le tableau de bord, les précédentes
    constituent l'historique.
    """

    __tablename__ = "parcel_analysis"

    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(
        Integer, ForeignKey("parcel.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Date du prélèvement / de l'analyse (pas la date de saisie)
    analyzed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # ------ Sol ------
    soil_type = Column(String(100), nullable=True)       # texture : sableuse, sable-limon...
    soil_ph = Column(Float, nullable=True)               # sans unité (0-14)
    soil_humidity_pct = Column(Float, nullable=True)     # %
    soil_organic_matter_pct = Column(Float, nullable=True)  # %
    soil_salinity_g_l = Column(Float, nullable=True)     # g/L

    # ------ Eau d'irrigation ------
    water_ph = Column(Float, nullable=True)              # sans unité (0-14)
    water_nitrates_mg_l = Column(Float, nullable=True)   # mg/L

    notes = Column(String(1000), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    parcel = relationship("Parcel", back_populates="analyses")