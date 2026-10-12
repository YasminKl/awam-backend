from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.core.config import JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRATION_DAYS
from app.db.database import get_db
from app.models.utilisateur import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _safe_truncate_password(password: str) -> str:
    """Tronque le mot de passe à 72 octets max (limite bcrypt),
    sans jamais couper un caractère multi-octets en deux."""
    encoded = password.encode("utf-8")
    if len(encoded) <= 72:
        return password
    # On coupe à 72 octets, puis on redécode en ignorant les octets
    # invalides laissés en fin de séquence par la coupure brutale.
    return encoded[:72].decode("utf-8", errors="ignore")


def hash_password(password: str) -> str:
    safe_password = _safe_truncate_password(password)
    return pwd_context.hash(safe_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    safe_password = _safe_truncate_password(plain_password)
    return pwd_context.verify(safe_password, hashed_password)


def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=JWT_EXPIRATION_DAYS)
    payload = {
        "sub": str(user_id),
        "iat": now,      # ⬅️ AJOUT : date de création du token
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Non authentifié.")

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalide ou expirée.")

    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Utilisateur introuvable.")

    # ⬇️ AJOUT : rejette le token s'il a été émis avant le dernier changement de mot de passe
    if user.password_changed_at is not None:
        token_issued_at = payload.get("iat")
        if token_issued_at is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalide ou expirée.")

        token_issued_at_dt = datetime.fromtimestamp(token_issued_at, tz=timezone.utc)
        # `iat` est en secondes entières : on compare à la seconde près, sinon un
        # cookie émis dans la même seconde que le changement serait refusé.
        if token_issued_at_dt < user.password_changed_at.replace(microsecond=0):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expirée, veuillez vous reconnecter.")

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs.",
        )
    return user