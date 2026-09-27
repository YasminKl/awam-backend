import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Charger les variables d'environnement
load_dotenv()

# Récupérer l'URL de la base de données
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

# Vérifier que l'URL existe
if not SQLALCHEMY_DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set!")

# Créer le moteur de base de données
engine = create_engine(SQLALCHEMY_DATABASE_URL)

# Créer la session locale
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base pour les modèles
Base = declarative_base()

# Dépendance pour obtenir la session DB
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()