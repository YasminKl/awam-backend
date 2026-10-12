from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class CropDiagnosis(Base):
    """
    Résultat du moteur de diagnostic pour une image satellite.

    Archivé à chaque analyse : c'est cet historique qui permet la règle
    des deux observations consécutives (une anomalie confirmée devient
    une alerte) et le suivi des performances au fil des saisons.

    `indicators` garde le détail par indice sous forme JSON :
    [{name, value, delta, level, threshold_label}, ...]
    """

    __tablename__ = "crop_diagnosis"

    id = Column(Integer, primary_key=True, index=True)
    crop_id = Column(
        Integer, ForeignKey("crop.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Date de l'image satellite analysée
    scene_date = Column(Date, nullable=False, index=True)
    stage = Column(String(50), nullable=True)

    #: Détail par indice, tel qu'affiché dans l'interface
    indicators = Column(JSON, nullable=True)

    #: "normal" | "watch" | "alert" | "strong_alert"
    severity = Column(String(20), nullable=False, server_default="normal", index=True)
    #: Lecture croisée des indices, ex. "Stress hydrique probable"
    diagnosis = Column(String(200), nullable=True)
    #: Ce qu'il faut mesurer sur le terrain
    measure = Column(Text, nullable=True)
    #: Ce qu'il faut faire si l'anomalie est confirmée
    action = Column(Text, nullable=True)

    #: Vrai si une coupe ou récolte explique la baisse : pas d'alerte
    cultural_drop = Column(String(5), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    crop = relationship("Crop", back_populates="diagnoses")