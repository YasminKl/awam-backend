from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base


class AgendaEvent(Base):
    __tablename__ = "agenda_event"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("utilisateur.id", ondelete="CASCADE"), nullable=False, index=True)
    

    title = Column(String(255), nullable=False)
    description = Column(String(1000), nullable=True)
    start_at = Column(DateTime(timezone=True), nullable=False)
    end_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, server_default="a_faire")

    # --- Rappels ---
    reminder_sent = Column(Boolean, nullable=False, server_default="false")
    reminder = Column(String(20), nullable=True)   
    reminder_custom_minutes = Column(Integer, nullable=True)          

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


    # Relation Many-to-One avec User
    utilisateur = relationship(
        "User",
        back_populates="agenda_event",
        
    )
