from sqlalchemy import Column, Integer, String, Text, DateTime, Float, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base


class Raster(Base):
    __tablename__ = "raster"

    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(
        Integer,
        ForeignKey("parcel.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    nom = Column(String(255), nullable=True)
    b2_key = Column(String(500), nullable=False)
    b2_url = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    # Emprise du COG en EPSG:4326 (WGS84), pour que le frontend puisse
    # poser `bounds` sur la source raster MapLibre et éviter que des
    # tuiles vides soient demandées à titiler hors de l'emprise réelle.
    bbox_west = Column(Float, nullable=True)
    bbox_south = Column(Float, nullable=True)
    bbox_east = Column(Float, nullable=True)
    bbox_north = Column(Float, nullable=True)

    # Distinguer les rasters
    raster_type = Column(String(50), nullable=True, index=True)
    scene_date = Column(DateTime, nullable=True)
    cloud_cover = Column(Float, nullable=True)

    # N rasters → 1 parcel
    parcel = relationship("Parcel", back_populates="rasters")