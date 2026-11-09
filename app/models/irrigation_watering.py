"""
Un arrosage : une tâche (activity) de type « irrigation », avec ses
détails d'eau. Une ligne par tâche, supprimée avec elle.

Façons de mesurer l'eau (method) :
  - arrosoirs : nombre d'arrosoirs × capacité de l'arrosoir ;
  - duree     : durée (heures de début et de fin, ou minutes) × débit de
                l'équipement ;
  - compteur  : volume lu au compteur, à la sortie de la pompe ;
  - volume    : litres estimés à la main, sans équipement.
Le volume (L) est calculé à l'enregistrement, et gardé : si l'équipement
est supprimé ou modifié plus tard, l'historique reste juste.
"""

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, String, Time
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base

METHOD_CANS = "arrosoirs"
METHOD_DURATION = "duree"
METHOD_METER = "compteur"
METHOD_VOLUME = "volume"
METHODS = (METHOD_CANS, METHOD_DURATION, METHOD_METER, METHOD_VOLUME)

#: Le type de tâche d'un arrosage, dans la planification
WATERING_TASK_TYPE = "irrigation"


class IrrigationWatering(Base):
    __tablename__ = "irrigation_watering"
    __table_args__ = (
        CheckConstraint(
            "method IN ('arrosoirs', 'duree', 'compteur', 'volume')", name="ck_irrigation_watering_method"
        ),
        CheckConstraint(
            "(can_count IS NULL OR can_count > 0)"
            " AND (duration_min IS NULL OR duration_min > 0)"
            " AND (meter_volume_l IS NULL OR meter_volume_l > 0)"
            " AND (volume_l IS NULL OR volume_l >= 0)",
            name="ck_irrigation_watering_positive",
        ),
    )

    id = Column(Integer, primary_key=True)
    activity_id = Column(Integer, ForeignKey("activity.id", ondelete="CASCADE"), nullable=False, unique=True)
    #: Équipement utilisé ; vide s'il a été supprimé depuis (le volume reste)
    equipment_id = Column(
        Integer, ForeignKey("irrigation_equipment.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: arrosoirs | duree | compteur | volume
    method = Column(String(20), nullable=False)
    can_count = Column(Integer, nullable=True)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    duration_min = Column(Float, nullable=True)
    meter_volume_l = Column(Float, nullable=True)
    #: Volume d'eau apporté (L), calculé à l'enregistrement
    volume_l = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    activity = relationship("Activity")
    equipment = relationship("IrrigationEquipment")