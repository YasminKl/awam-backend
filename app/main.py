# ======================================================================
# ⚠️ Configuration GDAL — DOIT être AVANT les imports rasterio/titiler
# ======================================================================
import os

os.environ["GDAL_HTTP_HEADERS"] = ""
os.environ["GDAL_HTTP_TIMEOUT"] = "30"
os.environ["GDAL_HTTP_MULTIPLEX"] = "YES"
os.environ["GDAL_HTTP_VERSION"] = "2"
os.environ["AWS_NO_SIGN_REQUEST"] = "YES"
os.environ["AWS_ACCESS_KEY_ID"] = ""
os.environ["AWS_SECRET_ACCESS_KEY"] = ""
os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"
os.environ["CPL_VSIL_CURL_ALLOWED_EXTENSIONS"] = ".tif,.tiff"
os.environ["GDAL_HTTP_UNSAFESSL"] = "NO"


# ======================================================================
# ⚠️ Filtrage des warnings AVANT tout import
# ======================================================================
import warnings
warnings.filterwarnings("ignore", message=".*DoesNotConformTo.*")
warnings.filterwarnings("ignore", message=".*CPLE_NotSupported.*")



# ======================================================================
# Imports (GDAL est configuré)
# ======================================================================
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
import warnings

from app.core.config import FRONTEND_URL, SESSION_SECRET_KEY, ADMIN_EMAIL
from app.api.routes.auth import router as auth_router
from app.api.routes.profile import router as profile_router
from app.db.database import SessionLocal
from app.models.utilisateur import User
from sqlalchemy import func  # ⬅️ CASSE E-MAIL

from app.api.routes.parcels import router as parcels_router
from app.api.routes.vector_layers import router as vector_layers_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.raster import router as raster_router
from app.api.routes.farms import router as farm_router
from titiler.core.factory import TilerFactory
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers
from app.api.routes.agenda import router as agenda_router
from app.api.routes.indices import router as indices_router
from app.api.routes.alerts import router as alerts_router
from app.api.routes.employees import router as employees_router
from app.api.routes.farm_employees import router as farm_employees_router  # ⬅️ AJOUT
from app.api.routes.activities import router as activities_router
from app.api.routes.weather import router as weather_router
from app.api.routes.parcel_analyses import router as parcel_analyses_router
from app.api.routes.crops import router as crops_router
from app.api.routes.diagnosis import router as diagnosis_router

print("🔵 Lancement de l'application FastAPI...")

app = FastAPI(
    title="AWAM API",
    version="1.0.0"
)

print("🔵 Ajout du middleware Session...")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

print("🔵 Ajout du middleware CORS...")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
print(f"🔵 CORS configuré avec FRONTEND_URL = {FRONTEND_URL}")

# ==================================================================
# Routers SANS préfixe /api
# ==================================================================

print("🔵 Ajout du router auth...")
app.include_router(auth_router)

print("🔵 Ajout du router profile...")
app.include_router(profile_router)

print("🔵 Ajout du router vector_layers...")
app.include_router(vector_layers_router)

print("🔵 Ajout du router agenda...")
app.include_router(agenda_router)

# ==================================================================
# Routers AVEC préfixe /api
# ==================================================================

print("🔵 Ajout du router dashboard...")
app.include_router(dashboard_router, prefix="/api")

print("🔵 Ajout du router raster...")
app.include_router(raster_router, prefix="/api")

print("🔵 Ajout du router parcels...")
app.include_router(parcels_router, prefix="/api")

print("🔵 Ajout du router farm...")
app.include_router(farm_router, prefix="/api")

print("🔵 Ajout du router indices...")
app.include_router(indices_router, prefix="/api")

print("🔵 Ajout du router alerts...")
app.include_router(alerts_router, prefix="/api")

print("🔵 Ajout du router employees...")
app.include_router(employees_router, prefix="/api")

print("🔵 Ajout du router farm_employees...")  # ⬅️ AJOUT
app.include_router(farm_employees_router, prefix="/api")  # ⬅️ AJOUT

print("🔵 Ajout du router activities...")
app.include_router(activities_router, prefix="/api")

print("🔵 Ajout du router weather...")
app.include_router(weather_router, prefix="/api")

print("🔵 Ajout du router parcel_analyses...")
app.include_router(parcel_analyses_router, prefix="/api")

print("🔵 Ajout du router crops...")
app.include_router(crops_router, prefix="/api")

print("🔵 Ajout du router diagnosis...")
app.include_router(diagnosis_router, prefix="/api")

# ==================================================================
# Titiler
# ==================================================================
print("🔵 Ajout des handlers d'exception titiler...")
add_exception_handlers(app, DEFAULT_STATUS_CODES)

print("🔵 Ajout du router titiler (COG)...")
cog = TilerFactory()
app.include_router(cog.router, prefix="/cog", tags=["COG"])

print("✅ Application prête !")


@app.on_event("startup")
def bootstrap_admin():
    db = SessionLocal()
    try:
        admin_exists = db.query(User).filter(User.role == "admin").first()
        if admin_exists:
            print(f"🔵 Un admin existe déjà ({admin_exists.email}), bootstrap ignoré.")
            return

        if not ADMIN_EMAIL:
            print("🔴 ADMIN_EMAIL n'est pas défini dans .env")
            return

        user = db.query(User).filter(func.lower(User.email) == ADMIN_EMAIL).first()  # ⬅️ CASSE E-MAIL
        if not user:
            print(f"🔴 Aucun utilisateur trouvé avec l'email {ADMIN_EMAIL}")
            return

        user.role = "admin"
        db.commit()
        print(f"✅ {ADMIN_EMAIL} promu premier administrateur.")
    finally:
        db.close()


@app.get("/health")
async def health_check():
    return {"status": "ok"}