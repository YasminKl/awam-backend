import re
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator, model_validator

from app.schemas.auth import NAME_REGEX, UserResponse

USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")


class UpdateFullNameRequest(BaseModel):
    full_name: str

    @field_validator("full_name")
    @classmethod
    def full_name_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Le nom complet est requis.")
        if len(v) > 100:
            raise ValueError("Le nom complet ne doit pas dépasser 100 caractères.")
        if not NAME_REGEX.match(v):
            raise ValueError("Le nom complet ne doit contenir que des lettres.")
        return v


class UpdateUsernameRequest(BaseModel):
    username: str

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Le nom d'utilisateur doit contenir au moins 3 caractères.")
        if len(v) > 50:
            raise ValueError("Le nom d'utilisateur ne doit pas dépasser 50 caractères.")
        if not USERNAME_REGEX.match(v):
            raise ValueError(
                "Le nom d'utilisateur ne doit contenir que des lettres, chiffres, tirets et underscores."
            )
        return v


class UpdateEmailRequest(BaseModel):
    email: EmailStr


class UpdatePasswordRequest(BaseModel):
    current_password: Optional[str] = None
    new_password: str
    confirm_password: str

    @field_validator("new_password", "confirm_password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        if len(v) > 72:
            raise ValueError("Le mot de passe ne doit pas dépasser 72 caractères.")
        return v

    @model_validator(mode="after")
    def passwords_match(self) -> "UpdatePasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("Les mots de passe ne correspondent pas.")
        return self


class ProfileResponse(UserResponse):
    avatar_url: Optional[str] = None
    has_password: bool