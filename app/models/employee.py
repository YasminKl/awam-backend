"""
Employé d'un exploitant.

Un employé APPARTIENT à un compte (user_id) : chaque exploitant ne voit,
ne modifie et ne supprime que ses propres employés. Il peut travailler
dans plusieurs fermes de ce compte : le rattachement, avec le rôle
(« coordinateur » ou « membre ») et l'équipe, est dans farm_employee.

L'INACTIVITÉ (is_active) concerne l'employé lui-même, dans TOUTES ses
fermes, indépendamment de l'état des fermes. Un employé inactif est
conservé avec son historique, mais il n'a plus d'équipe, plus de rôle de
coordinateur, et ne reçoit plus de nouvelle tâche. Il peut être réactivé.
"""

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.database import Base


class Employee(Base):
    __tablename__ = "employee"

    id = Column(Integer, primary_key=True, index=True)
    #: Compte propriétaire de l'employé
    user_id = Column(Integer, ForeignKey("utilisateur.id", ondelete="CASCADE"), nullable=True, index=True)
    full_name = Column(String(150), nullable=False)
    phone = Column(String(30), nullable=True)
    #: Faux : employé inactif dans toutes ses fermes (ex. saisonnier parti)
    is_active = Column(Boolean, nullable=False, server_default="true")

    activities = relationship("Activity", secondary="activity_employee", back_populates="employees")
    farm_links = relationship(
        "FarmEmployee",
        back_populates="employee",
        cascade="all, delete-orphan",
    )