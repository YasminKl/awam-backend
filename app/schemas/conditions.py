"""Schémas de la page « Climat et cultures »."""

from datetime import date
from typing import Optional

from pydantic import BaseModel


class ParameterValue(BaseModel):
    """Un paramètre du mois : sa valeur, sa normale et l'écart entre les deux."""

    value: Optional[float] = None
    normal: Optional[float] = None
    #: Différence (même unité que la valeur) ou pourcentage si anomaly_unit vaut « % »
    anomaly: Optional[float] = None
    anomaly_unit: Optional[str] = None


class MonthConditions(BaseModel):
    """Les chiffres d'un mois pour une ferme (onglet « Paramètres »)."""

    farm_id: int
    #: « AAAA-MM »
    month: str
    #: Jours de météo connus sur le mois
    days: int
    days_in_month: int
    #: Mois en cours : calculé sur ses jours déjà passés
    partial: bool
    normals_period: list[int]
    #: Nombre d'années complètes utilisées pour la normale
    normals_years: int
    parameters: dict[str, ParameterValue]
    #: Plus longue suite de jours secs (indice CDD de l'OMM)
    longest_dry_spell: Optional[int] = None
    wind_speed_max_kmh: Optional[float] = None
    windiest_day: Optional[date] = None
    

class YearRow(BaseModel):
    """Un mois d'une année, dans la comparaison des années."""

    year: int
    days: int
    #: Mois incomplet (mois en cours, ou jours manquants)
    partial: bool
    values: dict[str, Optional[float]]


class YearComparison(BaseModel):
    """Le même mois pour chaque année, et sa normale."""

    farm_id: int
    #: Numéro du mois, de 1 à 12
    month: int
    normals_period: list[int]
    #: De l'année la plus récente à la plus ancienne
    years: list[YearRow]
    normal: dict[str, Optional[float]]