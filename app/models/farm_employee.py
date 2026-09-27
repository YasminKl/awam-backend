# app/models/farm_employee.py
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base


class FarmEmployee(Base):
    """
    Table d'association enrichie entre Farm et Employee.

    Permet de stocker des métadonnées sur la relation :
      - rôle de l'employé dans cette ferme
      - date d'embauche
      - statut (actif / inactif)
    """
    __tablename__ = "farm_employee"

    id = Column(Integer, primary_key=True, index=True)

    farm_id = Column(
        Integer,
        ForeignKey("farm.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    employee_id = Column(
        Integer,
        ForeignKey("employee.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Métadonnées de la relation
    role = Column(String(50), nullable=True)          # 'ouvrier', 'contremaître', 'chef d'équipe'
    hired_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(String(10), nullable=False, server_default="true")

    # Contrainte : un employé ne peut être lié qu'une fois à une ferme
    __table_args__ = (
        UniqueConstraint("farm_id", "employee_id", name="uq_farm_employee"),
    )

    # Relations
    farm = relationship("Farm", back_populates="employee_links")
    employee = relationship("Employee", back_populates="farm_links")