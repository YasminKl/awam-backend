from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base


class Alert(Base):
    __tablename__ = "alert"

    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(Integer, ForeignKey("parcel.id", ondelete="CASCADE"), nullable=False, index=True)

    alert_type = Column(String(100), nullable=False)
    severity = Column(String(50), nullable=False)
    message = Column(String(500), nullable=False)
    resolved = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    parcel = relationship("Parcel", back_populates="alerts")