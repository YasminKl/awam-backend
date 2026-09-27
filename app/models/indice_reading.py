from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base


class IndiceReading(Base):
    __tablename__ = "indice_reading"

    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(Integer, ForeignKey("parcel.id", ondelete="CASCADE"), nullable=False, index=True)

    indice_name = Column(String(50), nullable=False, index=True)
    value = Column(Float, nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    parcel = relationship("Parcel", back_populates="indice_readings")