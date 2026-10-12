from sqlalchemy import Boolean, Column, Integer, String, Float, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class Crop(Base):
    """
    Culture en place sur une parcelle, pour une saison.

    Une parcelle peut en porter plusieurs au fil du temps : la plus
    récente est la culture courante, les précédentes constituent
    l'historique et permettent d'analyser les rotations.

    `species` renvoie au référentiel agronomique (app/services/agronomy) :
    c'est lui qui détermine les stades, les seuils et les coefficients
    culturaux appliqués.
    """

    __tablename__ = "crop"

    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(
        Integer, ForeignKey("parcel.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Code du référentiel : olivier, lavande, amandier...
    species = Column(String(50), nullable=False, index=True)
    variety = Column(String(100), nullable=True)

    planting_date = Column(Date, nullable=True)
    expected_harvest_date = Column(Date, nullable=True)
    #: Renseignée à la main, ou déduite d'une activité de type récolte
    actual_harvest_date = Column(Date, nullable=True)

    #: Plants par hectare, saisie (non mesurable par satellite)
    density = Column(Float, nullable=True)
    
    #: Arbres fruitiers uniquement : le verger produit-il déjà ?
    #: Coché par l'utilisateur, ou automatiquement dès qu'une récolte
    #: réelle est enregistrée. Conditionne le calcul de la récolte prévue.
    in_production = Column(Boolean, nullable=False, server_default="false")

    #: "active" | "harvested" | "abandoned"
    status = Column(String(20), nullable=False, server_default="active", index=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    parcel = relationship("Parcel", back_populates="crops")
    observations = relationship(
        "CropObservation",
        back_populates="crop",
        cascade="all, delete-orphan",
        order_by="CropObservation.observed_at.desc()",
    )
    diagnoses = relationship(
        "CropDiagnosis",
        back_populates="crop",
        cascade="all, delete-orphan",
        order_by="CropDiagnosis.scene_date.desc()",
    )