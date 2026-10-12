from authlib.integrations.starlette_client import OAuth
from sqlalchemy import func  # ⬅️ CASSE E-MAIL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, ADMIN_EMAIL
from app.models.utilisateur import User

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    redirect_uri=GOOGLE_REDIRECT_URI,
    client_kwargs={
        "scope": "openid email profile",
        "timeout": 30,
    },
)

print("✅ OAuth Google enregistré avec succès !")

MAX_USERNAME_LENGTH = 50
MAX_FULL_NAME_LENGTH = 100


def generate_unique_username(db: Session, base_username: str) -> str:
    base_username = base_username[:MAX_USERNAME_LENGTH]
    username = base_username
    suffix = 2
    while db.query(User).filter(User.username == username).first() is not None:
        suffix_str = str(suffix)
        max_base_len = MAX_USERNAME_LENGTH - len(suffix_str)
        username = f"{base_username[:max_base_len]}{suffix_str}"
        suffix += 1
    return username


def determine_role_for_new_user(db: Session, email: str) -> str:
    admin_exists = db.query(User).filter(User.role == "admin").first()
    if not admin_exists and ADMIN_EMAIL and email == ADMIN_EMAIL:
        return "admin"
    return "user"


def get_or_create_user(db: Session, google_id: str, email: str, full_name: str) -> User:
    email = email.strip().lower()  # ⬅️ CASSE E-MAIL : e-mails enregistrés et comparés en minuscules

    user = db.query(User).filter(User.google_id == google_id).first()
    if user:
        return user

    user = db.query(User).filter(func.lower(User.email) == email).first()  # ⬅️ CASSE E-MAIL
    if user:
        user.google_id = google_id
        db.commit()
        db.refresh(user)
        return user

    base_username = email.split("@")[0]
    username = generate_unique_username(db, base_username)
    safe_full_name = (full_name or "")[:MAX_FULL_NAME_LENGTH]
    role = determine_role_for_new_user(db, email)

    new_user = User(
        username=username,
        email=email,
        full_name=safe_full_name,
        google_id=google_id,
        role=role,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user