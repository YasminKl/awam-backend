from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.db.database import Base
from sqlalchemy.orm import relationship

class User(Base):
    __tablename__ = "utilisateur"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    full_name = Column(String(100))
    google_id = Column(String(255), unique=True, index=True, nullable=True)
    password_hash = Column(String(255), nullable=True)
    reset_token = Column(String(255), unique=True, index=True, nullable=True)
    reset_token_expires = Column(DateTime(timezone=True), nullable=True)
    role = Column(String(20), nullable=False, server_default="user")
    password_changed_at = Column(DateTime(timezone=True), nullable=True)
    avatar_url = Column(String(255),nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relation One-to-Many avec task
    agenda_event = relationship(
        "AgendaEvent",                     
        back_populates="utilisateur", 
        cascade="all, delete-orphan"  
    )

    # Relation One-to-Many avec Parcelle
    # fermes ; chaque ferme a ses propres parcelles)
    farms = relationship(
        "Farm",
        back_populates="user",
        cascade="all, delete-orphan"
    )