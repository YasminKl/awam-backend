from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.db.database import Base


class Employee(Base):
    __tablename__ = "employee"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(150), nullable=False)
    phone = Column(String(30), nullable=True)

    activities = relationship("Activity", secondary="activity_employee", back_populates="employees")

    # Relation N↔N via FarmEmployee
    farm_links = relationship(
        "FarmEmployee",
        back_populates="employee",
        cascade="all, delete-orphan",
    )