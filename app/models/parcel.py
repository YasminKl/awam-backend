from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from geoalchemy2 import Geometry
from app.db.database import Base


class Parcel(Base):
    __tablename__ = "parcel"

    id = Column(Integer, primary_key=True, index=True)
    farm_id = Column(Integer, ForeignKey("farm.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(150), nullable=False)
    culture_type = Column(String(100), nullable=True)
    area_ha = Column(Float, nullable=True)

    # État agronomique de la parcelle (active / inactive...). Ne représente
    # QUE cette notion : ne jamais y écrire "processing" ou assimilé.
    status = Column(String(20), nullable=False, server_default="active")

    # État du traitement des rasters (masque + Sentinel), indépendant de `status`.
    # "pending" : génération en cours ou pas encore lancée
    # "ready"   : rasters générés avec succès
    # "failed"  : la dernière tentative de génération a échoué
    raster_status = Column(String(20), nullable=False, server_default="pending")

    soil_type = Column(String(100), nullable=True)
    irrigation_type = Column(String(100), nullable=True)
    geom = Column(Geometry(geometry_type="POLYGON", srid=4326), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # ------ Relations ------
    farm = relationship("Farm", back_populates="parcels")
    activities = relationship("Activity", back_populates="parcel", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="parcel", cascade="all, delete-orphan")
    indice_readings = relationship("IndiceReading", back_populates="parcel", cascade="all, delete-orphan")
    rasters = relationship("Raster", back_populates="parcel", cascade="all, delete-orphan", order_by="Raster.created_at.desc()")
