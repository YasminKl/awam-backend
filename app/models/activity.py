"""
Tâches d'une parcelle : prévues, réalisées, ou réalisées mais à suivre.

État (status) :
  a_realiser   : tâche PLANIFIÉE, avec sa date prévue (planned_for) ;
                 elle n'a pas encore de date réelle (performed_at vide) ;
  realisee     : tâche FAITE, à sa date réelle (performed_at) ;
  a_entretenir : tâche faite, mais qui demande un suivi, sans date fixe.

Seules les tâches FAITES (realisee, a_entretenir) comptent comme des faits
accomplis : la date de récolte d'une culture et le diagnostic satellite ne
lisent jamais une tâche seulement planifiée.

Une tâche peut avoir des tâches ATTACHÉES (parent_id), ex. une irrigation
attachée à une plantation ; supprimer la principale supprime les attachées.
"""

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.orm import backref, relationship
from sqlalchemy.sql import func

from app.db.database import Base

#: États d'une tâche
STATUS_PLANNED = "a_realiser"
STATUS_DONE = "realisee"
STATUS_FOLLOW_UP = "a_entretenir"
ACTIVITY_STATUSES = (STATUS_PLANNED, STATUS_DONE, STATUS_FOLLOW_UP)
#: États d'une tâche faite
DONE_STATUSES = (STATUS_DONE, STATUS_FOLLOW_UP)

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
    crop_id = Column(
        Integer, ForeignKey("crop.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: a_realiser | realisee | a_entretenir
    status = Column(String(20), nullable=False, server_default=STATUS_DONE)
    #: Date prévue (tâche planifiée)
    planned_for = Column(Date, nullable=True)
    #: Date réelle : remplie seulement quand la tâche est faite
    performed_at = Column(DateTime(timezone=True), nullable=True)
    #: Équipe chargée de la tâche (code d'un type de tâche), facultative
    team = Column(String(60), nullable=True)
    #: Tâche principale, pour une tâche attachée
    parent_id = Column(Integer, ForeignKey("activity.id", ondelete="CASCADE"), nullable=True, index=True)

    parcel = relationship("Parcel", back_populates="activities")
    employees = relationship("Employee", secondary=activity_employee, back_populates="activities")
    #: Tâches attachées, supprimées avec la tâche principale
    children = relationship(
        "Activity",
        cascade="all, delete-orphan",
        backref=backref("parent", remote_side=[id]),
    )

    @property
    def is_done(self) -> bool:
        return self.status in DONE_STATUSES