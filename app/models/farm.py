from sqlalchemy import Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base


class Farm(Base):
    __tablename__ = "farm"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("utilisateur.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    location = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    user = relationship("User", back_populates="farms")
    parcels = relationship("Parcel", back_populates="farm", cascade="all, delete-orphan")

    # Relation N↔N via FarmEmployee
    employee_links = relationship(
        "FarmEmployee",
        back_populates="farm",
        cascade="all, delete-orphan",
    )
