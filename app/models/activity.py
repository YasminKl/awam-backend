from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Table
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base

activity_employee = Table(
    "activity_employee",
    Base.metadata,
    Column("activity_id", Integer, ForeignKey("activity.id"), primary_key=True),
    Column("employee_id", Integer, ForeignKey("employee.id"), primary_key=True),
)


class Activity(Base):
    __tablename__ = "activity"

    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(Integer, ForeignKey("parcel.id"), nullable=False, index=True)

    activity_type = Column(String(100), nullable=False)
    performed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    parcel = relationship("Parcel", back_populates="activities")
    employees = relationship("Employee", secondary=activity_employee, back_populates="activities")