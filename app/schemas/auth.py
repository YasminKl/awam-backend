import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator, model_validator

NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ\s'-]+$")


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    role: str

    class Config:
        from_attributes = True


class RegisterRequest(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    password: str
    confirmPassword: str

    @field_validator("email")
    @classmethod
    def email_lowercase(cls, v: str) -> str:
        # Les e-mails sont enregistrés et comparés en minuscules
        v = v.lower()
        # La colonne email de la base accepte 100 caractères au maximum
        if len(v) > 100:
            raise ValueError("L'adresse e-mail ne doit pas dépasser 100 caractères.")
        return v

    @field_validator("firstName", "lastName")
    @classmethod
    def name_valid_format(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Ce champ est requis.")
        if len(v) > 50:
            raise ValueError("Ce champ ne doit pas dépasser 50 caractères.")
        if not NAME_REGEX.match(v):
            raise ValueError("Ce champ ne doit contenir que des lettres.")
        return v

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        if len(v) > 72:
            raise ValueError("Le mot de passe ne doit pas dépasser 72 caractères.")
        return v

    @model_validator(mode="after")
    def passwords_match(self) -> "RegisterRequest":
        if self.password != self.confirmPassword:
            raise ValueError("Les mots de passe ne correspondent pas.")
        return self

    @model_validator(mode="after")
    def full_name_max_length(self) -> "RegisterRequest":
        # Le nom complet enregistré est « prénom nom » : la colonne full_name
        # de la base accepte 100 caractères au maximum.
        if len(f"{self.firstName} {self.lastName}") > 100:
            raise ValueError("Le prénom et le nom réunis ne doivent pas dépasser 99 caractères.")
        return self


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def email_lowercase(cls, v: str) -> str:
        # ⬅️ CASSE E-MAIL : les e-mails sont enregistrés et comparés en minuscules
        return v.lower()


class AuthResponse(BaseModel):
    user: UserResponse


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def email_lowercase(cls, v: str) -> str:
        # ⬅️ CASSE E-MAIL : les e-mails sont enregistrés et comparés en minuscules
        return v.lower()


class ResetPasswordRequest(BaseModel):
    password: str
    confirmPassword: str

    @field_validator("password", "confirmPassword")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        if len(v) > 72:
            raise ValueError("Le mot de passe ne doit pas dépasser 72 caractères.")
        return v

    @model_validator(mode="after")
    def passwords_match(self) -> "ResetPasswordRequest":
        if self.password != self.confirmPassword:
            raise ValueError("Les mots de passe ne correspondent pas.")
        return self


class ResetTokenExpiryResponse(BaseModel):
    expires_at: datetime