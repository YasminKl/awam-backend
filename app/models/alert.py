"""
Alertes : un ÉPISODE d'anomalie sur une culture, du jour où le diagnostic
satellite la confirme jusqu'au jour où elle disparaît ou est traitée.

Le niveau de CHAQUE image de l'épisode n'est pas dupliqué ici : il est déjà
archivé, date par date, dans crop_diagnosis. L'alerte garde le niveau
actuel, la cause probable et les dates de début et de fin.

Niveaux (severity), repris du moteur de diagnostic :
  alert         → affiché « Anomalie détectée » : un signal anormal
                  confirmé sur deux images successives ;
  strong_alert  → affiché « Anomalie persistante » : une perte de vigueur
                  (feuillage et chlorophylle en baisse ensemble) confirmée
                  sur deux images successives.
Les mots « forte » ou « critique » ne sont jamais affichés : le niveau
mesure ce que le satellite confirme, pas une gravité constatée sur place.
"""

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class Alert(Base):
    __tablename__ = "alert"

    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(Integer, ForeignKey("parcel.id", ondelete="CASCADE"), nullable=False, index=True)
    #: Culture concernée (None si la culture a été supprimée depuis)
    crop_id = Column(Integer, ForeignKey("crop.id", ondelete="SET NULL"), nullable=True, index=True)

    #: "diagnostic_satellite" pour les alertes du diagnostic
    alert_type = Column(String(100), nullable=False)
    #: Niveau actuel de l'épisode : "alert" | "strong_alert"
    severity = Column(String(50), nullable=False)
    message = Column(String(500), nullable=False)

    #: Cause probable, ce qui l'indique, et ce qu'il faut faire
    cause = Column(String(200), nullable=True)
    evidence = Column(Text, nullable=True)
    action = Column(Text, nullable=True)
    #: Part de la parcelle observée sur la dernière image (0 à 1)
    valid_ratio = Column(Float, nullable=True)

    #: Première et dernière image de l'épisode
    first_scene_date = Column(Date, nullable=True)
    last_scene_date = Column(Date, nullable=True)

    resolved = Column(Boolean, nullable=False, server_default="false")
    #: Pourquoi l'alerte est fermée : "treated" (problème vérifié et traité),
    #: "false_alarm" (rien d'anormal sur le terrain) ou "auto" (la culture
    #: est redevenue normale sur une image suivante)
    resolution = Column(String(20), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    #: Dernière fois que l'alerte a été créée, mise à jour ou fermée :
    #: c'est elle qui place l'alerte dans l'historique des derniers jours
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    parcel = relationship("Parcel", back_populates="alerts")