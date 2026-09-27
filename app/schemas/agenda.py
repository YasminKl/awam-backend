from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

ALLOWED_STATUSES = {"a_faire", "fait", "annule", "reporte"}
ALLOWED_REMINDERS = {"15_min", "1_hour", "1_day", "1_week", "custom"}
ALLOWED_REMINDER_UNITS = {"minutes", "hours", "days"}

MAX_DESCRIPTION_LENGTH = 1000


class AgendaEventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    start_at: datetime
    end_at: Optional[datetime] = None
    status: str = "a_faire"
    reminder: Optional[str] = "1_day"
    reminder_custom_minutes: Optional[int] = None
    reminder_custom_unit: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Le titre est requis.")
        if len(v) > 150:
            raise ValueError("Le titre ne doit pas dépasser 150 caractères.")
        return v

    @field_validator("start_at", "end_at")
    @classmethod
    def datetime_has_timezone(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        if v.tzinfo is None:
            raise ValueError("La date doit inclure une information de fuseau horaire.")
        return v

    @field_validator("description")
    @classmethod
    def description_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if len(v) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(f"La description ne doit pas dépasser {MAX_DESCRIPTION_LENGTH} caractères.")
        return v

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str) -> str:
        if v not in ALLOWED_STATUSES:
            raise ValueError(f"Statut invalide. Valeurs autorisées : {', '.join(ALLOWED_STATUSES)}.")
        return v

    @field_validator("reminder")
    @classmethod
    def reminder_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_REMINDERS:
            raise ValueError(f"Rappel invalide. Valeurs autorisées : {', '.join(ALLOWED_REMINDERS)}.")
        return v

    @field_validator("reminder_custom_unit")
    @classmethod
    def reminder_custom_unit_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_REMINDER_UNITS:
            raise ValueError(f"Unité invalide. Valeurs autorisées : {', '.join(ALLOWED_REMINDER_UNITS)}.")
        return v

    @model_validator(mode="after")
    def custom_reminder_requires_minutes(self) -> "AgendaEventCreate":
        if self.reminder == "custom" and not self.reminder_custom_minutes:
            raise ValueError("Un rappel personnalisé doit préciser une durée en minutes.")
        return self

    @model_validator(mode="after")
    def end_after_start(self) -> "AgendaEventCreate":
        if self.end_at and self.end_at < self.start_at:
            raise ValueError("La date de fin ne peut pas être avant la date de début.")
        return self


class AgendaEventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    status: Optional[str] = None
    reminder: Optional[str] = None
    reminder_custom_minutes: Optional[int] = None
    reminder_custom_unit: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Le titre est requis.")
        if len(v) > 150:
            raise ValueError("Le titre ne doit pas dépasser 150 caractères.")
        return v

    @field_validator("description")
    @classmethod
    def description_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if len(v) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(f"La description ne doit pas dépasser {MAX_DESCRIPTION_LENGTH} caractères.")
        return v

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_STATUSES:
            raise ValueError(f"Statut invalide. Valeurs autorisées : {', '.join(ALLOWED_STATUSES)}.")
        return v

    @field_validator("reminder")
    @classmethod
    def reminder_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_REMINDERS:
            raise ValueError(f"Rappel invalide. Valeurs autorisées : {', '.join(ALLOWED_REMINDERS)}.")
        return v

    @field_validator("reminder_custom_unit")
    @classmethod
    def reminder_custom_unit_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_REMINDER_UNITS:
            raise ValueError(f"Unité invalide. Valeurs autorisées : {', '.join(ALLOWED_REMINDER_UNITS)}.")
        return v

    @model_validator(mode="after")
    def custom_reminder_requires_minutes(self) -> "AgendaEventUpdate":
        if self.reminder == "custom" and not self.reminder_custom_minutes:
            raise ValueError("Un rappel personnalisé doit préciser une durée en minutes.")
        return self

    @model_validator(mode="after")
    def end_after_start(self) -> "AgendaEventUpdate":
        if self.end_at and self.start_at and self.end_at < self.start_at:
            raise ValueError("La date de fin ne peut pas être avant la date de début.")
        return self


class AgendaEventResponse(BaseModel):
    id: int
    user_id: int
    title: str
    description: Optional[str] = None
    start_at: datetime
    end_at: Optional[datetime] = None
    status: str
    reminder_sent: bool
    reminder: Optional[str] = None
    reminder_custom_minutes: Optional[int] = None
    reminder_custom_unit: Optional[str] = None

    class Config:
        from_attributes = True

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

ALLOWED_STATUSES = {"a_faire", "fait", "annule", "reporte"}
ALLOWED_REMINDERS = {"15_min", "1_hour", "1_day", "1_week", "custom"}
ALLOWED_REMINDER_UNITS = {"minutes", "hours", "days"}

MAX_DESCRIPTION_LENGTH = 1000


class AgendaEventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    start_at: datetime
    end_at: Optional[datetime] = None
    status: str = "a_faire"
    reminder: Optional[str] = "1_day"
    reminder_custom_minutes: Optional[int] = None
    reminder_custom_unit: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Le titre est requis.")
        if len(v) > 150:
            raise ValueError("Le titre ne doit pas dépasser 150 caractères.")
        return v

    @field_validator("start_at", "end_at")
    @classmethod
    def datetime_has_timezone(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        if v.tzinfo is None:
            raise ValueError("La date doit inclure une information de fuseau horaire.")
        return v

    @field_validator("description")
    @classmethod
    def description_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if len(v) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(f"La description ne doit pas dépasser {MAX_DESCRIPTION_LENGTH} caractères.")
        return v

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str) -> str:
        if v not in ALLOWED_STATUSES:
            raise ValueError(f"Statut invalide. Valeurs autorisées : {', '.join(ALLOWED_STATUSES)}.")
        return v

    @field_validator("reminder")
    @classmethod
    def reminder_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_REMINDERS:
            raise ValueError(f"Rappel invalide. Valeurs autorisées : {', '.join(ALLOWED_REMINDERS)}.")
        return v

    @field_validator("reminder_custom_unit")
    @classmethod
    def reminder_custom_unit_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_REMINDER_UNITS:
            raise ValueError(f"Unité invalide. Valeurs autorisées : {', '.join(ALLOWED_REMINDER_UNITS)}.")
        return v

    @model_validator(mode="after")
    def custom_reminder_requires_minutes(self) -> "AgendaEventCreate":
        if self.reminder == "custom" and not self.reminder_custom_minutes:
            raise ValueError("Un rappel personnalisé doit préciser une durée en minutes.")
        return self

    @model_validator(mode="after")
    def end_after_start(self) -> "AgendaEventCreate":
        if self.end_at and self.end_at < self.start_at:
            raise ValueError("La date de fin ne peut pas être avant la date de début.")
        return self


class AgendaEventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    status: Optional[str] = None
    reminder: Optional[str] = None
    reminder_custom_minutes: Optional[int] = None
    reminder_custom_unit: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Le titre est requis.")
        if len(v) > 150:
            raise ValueError("Le titre ne doit pas dépasser 150 caractères.")
        return v

    @field_validator("description")
    @classmethod
    def description_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if len(v) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(f"La description ne doit pas dépasser {MAX_DESCRIPTION_LENGTH} caractères.")
        return v

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_STATUSES:
            raise ValueError(f"Statut invalide. Valeurs autorisées : {', '.join(ALLOWED_STATUSES)}.")
        return v

    @field_validator("reminder")
    @classmethod
    def reminder_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_REMINDERS:
            raise ValueError(f"Rappel invalide. Valeurs autorisées : {', '.join(ALLOWED_REMINDERS)}.")
        return v

    @field_validator("reminder_custom_unit")
    @classmethod
    def reminder_custom_unit_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_REMINDER_UNITS:
            raise ValueError(f"Unité invalide. Valeurs autorisées : {', '.join(ALLOWED_REMINDER_UNITS)}.")
        return v

    @model_validator(mode="after")
    def custom_reminder_requires_minutes(self) -> "AgendaEventUpdate":
        if self.reminder == "custom" and not self.reminder_custom_minutes:
            raise ValueError("Un rappel personnalisé doit préciser une durée en minutes.")
        return self

    @model_validator(mode="after")
    def end_after_start(self) -> "AgendaEventUpdate":
        if self.end_at and self.start_at and self.end_at < self.start_at:
            raise ValueError("La date de fin ne peut pas être avant la date de début.")
        return self


class AgendaEventResponse(BaseModel):
    id: int
    user_id: int
    title: str
    description: Optional[str] = None
    start_at: datetime
    end_at: Optional[datetime] = None
    status: str
    reminder_sent: bool
    reminder: Optional[str] = None
    reminder_custom_minutes: Optional[int] = None
    reminder_custom_unit: Optional[str] = None

    class Config:
        from_attributes = True