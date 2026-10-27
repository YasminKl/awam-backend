"""
Types de tâche : la liste qui nomme à la fois les TÂCHES (activity) et les
ÉQUIPES (farm_employee.team).

Deux sortes de types :
  - PRÉDÉFINIS : ceux du référentiel, écrits ci-dessous ;
  - PERSONNALISÉS : créés par l'utilisateur au moment où il s'en sert, en
    tapant un nouveau nom pour une tâche ou une équipe.

Aucune table dédiée : un type personnalisé existe tant qu'au moins une
tâche (son type, activity.activity_type, ou son équipe chargée,
activity.team) ou une équipe (farm_employee.team) le porte, dans l'une
des fermes du compte. La liste se calcule donc à partir de ces trois
colonnes, et reste propre toute seule.

Un nom tapé est RAMENÉ à son écriture existante : « récolte » donne le
code prédéfini « recolte », « paillage » reprend « Paillage » s'il existe
déjà. Aucun doublon ne peut donc apparaître.
"""

from __future__ import annotations

import re
import unicodedata

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.farm import Farm
from app.models.farm_employee import FarmEmployee
from app.models.parcel import Parcel

#: Types prédéfinis : (code, libellé). Les codes sont ceux des activités.
PREDEFINED_TASK_TYPES: list[tuple[str, str]] = [
    ("plantation", "Plantation"),
    ("irrigation", "Irrigation"),
    ("traitement", "Traitement"),
    ("fertilisation", "Fertilisation"),
    ("recolte", "Récolte"),
    ("taille", "Taille"),
    ("desherbage", "Désherbage"),
]

PREDEFINED_CODES = {code for code, _ in PREDEFINED_TASK_TYPES}

#: Longueur maximale d'un nom (colonne farm_employee.team)
MAX_NAME_LENGTH = 60


def normalize(name: str) -> str:
    """Minuscules, sans accents ni espaces superflus : « Récolte » = « recolte »."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


#: Forme normalisée → code prédéfini (code et libellé acceptés)
_PREDEFINED_BY_FORM = {normalize(code): code for code, _ in PREDEFINED_TASK_TYPES} | {
    normalize(label): code for code, label in PREDEFINED_TASK_TYPES
}


def custom_types(db: Session, user_id: int) -> list[str]:
    """
    Types personnalisés du compte : les noms portés par ses tâches et ses
    équipes, dans toutes ses fermes, hors types prédéfinis.
    """
    def activity_column(column):
        return (
            db.query(column)
            .join(Parcel, Activity.parcel_id == Parcel.id)
            .join(Farm, Parcel.farm_id == Farm.id)
            .filter(Farm.user_id == user_id, column.isnot(None))
            .distinct()
            .all()
        )

    activity_names = activity_column(Activity.activity_type) + activity_column(Activity.team)
    team_names = (
        db.query(FarmEmployee.team)
        .join(Farm, FarmEmployee.farm_id == Farm.id)
        .filter(Farm.user_id == user_id, FarmEmployee.team.isnot(None))
        .distinct()
        .all()
    )
    seen: dict[str, str] = {}
    for (name,) in [*activity_names, *team_names]:
        if not name or normalize(name) in _PREDEFINED_BY_FORM:
            continue
        seen.setdefault(normalize(name), name)
    return sorted(seen.values(), key=lambda n: normalize(n))


def canonical_code(db: Session, user_id: int, name: str) -> str:
    """
    Code à enregistrer pour un nom de tâche ou d'équipe : le code prédéfini
    s'il en désigne un, l'écriture existante d'un type personnalisé, sinon
    le nom tel que tapé (un nouveau type personnalisé).
    """
    cleaned = re.sub(r"\s+", " ", name).strip()
    if not cleaned:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Indiquez un nom de tâche ou d'équipe.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Le nom ne doit pas dépasser {MAX_NAME_LENGTH} caractères.",
        )
    form = normalize(cleaned)
    if form in _PREDEFINED_BY_FORM:
        return _PREDEFINED_BY_FORM[form]
    for existing in custom_types(db, user_id):
        if normalize(existing) == form:
            return existing
    return cleaned