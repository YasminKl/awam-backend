import os
from dotenv import load_dotenv

load_dotenv()
print("🔵 Chargement de config.py...")


GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = 1

SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY")

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

print(f"🔵 FRONTEND_URL = {FRONTEND_URL}")


if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REDIRECT_URI:
    raise ValueError(
        "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET et GOOGLE_REDIRECT_URI doivent être définis dans .env"
    )

if not JWT_SECRET_KEY:
    raise ValueError("JWT_SECRET_KEY doit être défini dans .env")

if not SESSION_SECRET_KEY:
    raise ValueError("SESSION_SECRET_KEY doit être défini dans .env")

print("✅ config.py chargé avec succès !")

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
# ⬅️ CASSE E-MAIL : en minuscules, comme tous les e-mails enregistrés en base
ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").strip().lower() or None

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev")

if not RESEND_API_KEY:
    raise ValueError("RESEND_API_KEY doit être défini dans .env")

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
    raise ValueError(
        "CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY et CLOUDINARY_API_SECRET doivent être définis dans .env"
    )


REDIS_URL = os.getenv("REDIS_URL")

if not REDIS_URL:
    raise ValueError("REDIS_URL doit être défini dans .env")


# ----------------------------------------------------------------------
# Backblaze B2
# ----------------------------------------------------------------------
B2_KEY_ID = os.getenv("B2_KEY_ID")
B2_APPLICATION_KEY = os.getenv("B2_APPLICATION_KEY")
B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME")
B2_ENDPOINT_URL = os.getenv("B2_ENDPOINT_URL")
B2_REGION = os.getenv("B2_REGION", "us-east-005")

if not all([B2_KEY_ID, B2_APPLICATION_KEY, B2_BUCKET_NAME, B2_ENDPOINT_URL]):
    raise ValueError(
        "B2_KEY_ID, B2_APPLICATION_KEY, B2_BUCKET_NAME et B2_ENDPOINT_URL "
        "doivent être définis dans .env"
    )