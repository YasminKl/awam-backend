from typing import Optional
from pydantic import BaseModel, field_validator


class FarmCreate(BaseModel):
    name: str
    location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Le nom de la ferme est requis.")
        if len(v) > 150:
            raise ValueError("Le nom de la ferme ne doit pas dépasser 150 caractères.")
        return v

    @field_validator("location")
    @classmethod
    def location_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if len(v) > 255:
            raise ValueError("La localisation ne doit pas dépasser 255 caractères.")
        return v or None

    @field_validator("latitude")
    @classmethod
    def latitude_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not -90 <= v <= 90:
            raise ValueError("La latitude doit être comprise entre -90 et 90.")
        return v

    @field_validator("longitude")
    @classmethod
    def longitude_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not -180 <= v <= 180:
            raise ValueError("La longitude doit être comprise entre -180 et 180.")
        return v


class FarmUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Le nom de la ferme est requis.")
        if len(v) > 150:
            raise ValueError("Le nom de la ferme ne doit pas dépasser 150 caractères.")
        return v

    @field_validator("location")
    @classmethod
    def location_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if len(v) > 255:
            raise ValueError("La localisation ne doit pas dépasser 255 caractères.")
        return v or None

    @field_validator("latitude")
    @classmethod
    def latitude_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not -90 <= v <= 90:
            raise ValueError("La latitude doit être comprise entre -90 et 90.")
        return v

    @field_validator("longitude")
    @classmethod
    def longitude_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not -180 <= v <= 180:
            raise ValueError("La longitude doit être comprise entre -180 et 180.")
        return v


class FarmResponse(BaseModel):
    id: int
    name: str
    location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    class Config:
        from_attributes = True