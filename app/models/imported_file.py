from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.db.database import Base


class ImportedFile(Base):
    __tablename__ = "imported_file"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False, unique=True, index=True)
    imported_at = Column(DateTime(timezone=True), server_default=func.now())