"""
Route des types de tâche : les prédéfinis, puis les types personnalisés du
compte, calculés à partir de ses tâches et de ses équipes (voir
app/services/task_types.py). Aucune table dédiée : un type personnalisé
se crée en le tapant, pour une tâche ou une équipe.
"""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.utilisateur import User
from app.schemas.task_type import TaskTypeResponse
from app.services.task_types import PREDEFINED_TASK_TYPES, custom_types

router = APIRouter(prefix="/task-types", tags=["task-types"])

@router.get("", response_model=List[TaskTypeResponse])
async def list_task_types(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Types prédéfinis, puis types personnalisés du compte."""
    predefined = [
        TaskTypeResponse(code=code, label=label, predefined=True)
        for code, label in PREDEFINED_TASK_TYPES
    ]
    return predefined + [
        TaskTypeResponse(code=name, label=name, predefined=False)
        for name in custom_types(db, user.id)
    ]