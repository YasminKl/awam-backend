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


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    user: UserResponse


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


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