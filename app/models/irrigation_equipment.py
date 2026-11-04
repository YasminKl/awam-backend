"""
Équipements d'irrigation d'une parcelle : arrosoir, tuyau depuis le puits,
goutte-à-goutte. Une parcelle peut en avoir plusieurs, chacun desservant
une ou plusieurs de ses cultures.

Chaque type a ses paramètres ; les autres colonnes restent vides :
  - arrosoir         : capacité (L) ;
  - tuyau            : débit mesuré (L/min), diamètre, longueur ; compteur ;
  - goutte_a_goutte  : débit d'un goutteur (L/h), goutteurs par plant,
                       écartements entre plants et entre lignes (m).
L'efficience est facultative : aucune valeur par défaut tant qu'elle n'est
pas sourcée.
"""

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.sql import func

from app.db.database import Base

KIND_CAN = "arrosoir"
KIND_HOSE = "tuyau"
KIND_DRIP = "goutte_a_goutte"
KINDS = (KIND_CAN, KIND_HOSE, KIND_DRIP)

WATER_SOURCES = ("puits", "reseau", "bassin", "autre")


class IrrigationEquipment(Base):
    __tablename__ = "irrigation_equipment"
    __table_args__ = (
        CheckConstraint("kind IN ('arrosoir', 'tuyau', 'goutte_a_goutte')", name="ck_irrigation_equipment_kind"),
        CheckConstraint(
            "water_source IN ('puits', 'reseau', 'bassin', 'autre')", name="ck_irrigation_equipment_source"
        ),
        CheckConstraint(
            "(can_capacity_l IS NULL OR can_capacity_l > 0)"
            " AND (hose_flow_l_min IS NULL OR hose_flow_l_min > 0)"
            " AND (hose_diameter_in IS NULL OR hose_diameter_in > 0)"
            " AND (hose_length_m IS NULL OR hose_length_m > 0)"
            " AND (emitter_flow_l_h IS NULL OR emitter_flow_l_h > 0)"
            " AND (emitters_per_plant IS NULL OR emitters_per_plant > 0)"
            " AND (plant_spacing_m IS NULL OR plant_spacing_m > 0)"
            " AND (row_spacing_m IS NULL OR row_spacing_m > 0)"
            " AND (efficiency_pct IS NULL OR (efficiency_pct > 0 AND efficiency_pct <= 100))",
            name="ck_irrigation_equipment_positive",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(Integer, ForeignKey("parcel.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    #: arrosoir | tuyau | goutte_a_goutte
    kind = Column(String(20), nullable=False)
    #: Cultures desservies (identifiants, de cette parcelle)
    crop_ids = Column(ARRAY(Integer), nullable=False, server_default=text("'{}'"))
    #: puits | reseau | bassin | autre
    water_source = Column(String(20), nullable=False, server_default="puits")
    #: Un compteur mesure le volume à la sortie de la pompe
    has_meter = Column(Boolean, nullable=False, server_default="false")

    #: Arrosoir : capacité (L)
    can_capacity_l = Column(Float, nullable=True)
    #: Tuyau : débit mesuré (L/min), diamètre (pouces), longueur (m)
    hose_flow_l_min = Column(Float, nullable=True)
    hose_diameter_in = Column(Float, nullable=True)
    hose_length_m = Column(Float, nullable=True)
    #: Goutte-à-goutte : débit d'un goutteur (L/h), goutteurs par plant, écartements (m)
    emitter_flow_l_h = Column(Float, nullable=True)
    emitters_per_plant = Column(Integer, nullable=True)
    plant_spacing_m = Column(Float, nullable=True)
    row_spacing_m = Column(Float, nullable=True)
    #: Efficience (%), facultative
    efficiency_pct = Column(Float, nullable=True)

    active = Column(Boolean, nullable=False, server_default="true")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())