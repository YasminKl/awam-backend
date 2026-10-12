from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class CropObservation(Base):
    """
    Observation de terrain saisie par l'utilisateur.

    Complète les données satellite par ce qui ne se voit pas d'en haut :
    état des bourgeons, présence de ravageurs, aspect des feuilles.
    Le stade est enregistré au moment de la saisie, pour garder le
    contexte même si le calendrier évolue.
    """

    __tablename__ = "crop_observation"

    id = Column(Integer, primary_key=True, index=True)
    crop_id = Column(
        Integer, ForeignKey("crop.id", ondelete="CASCADE"), nullable=False, index=True
    )

    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    #: Stade au moment de l'observation (code du référentiel)
    stage = Column(String(50), nullable=True)
    content = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    crop = relationship("Crop", back_populates="observations")