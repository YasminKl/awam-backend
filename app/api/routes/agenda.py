from typing import List


from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.utilisateur import User
from app.models.agenda_event import AgendaEvent
from app.schemas.agenda import (
    AgendaEventCreate,
    AgendaEventUpdate,
    AgendaEventResponse,
)

router = APIRouter(prefix="/agenda", tags=["agenda"])


@router.get("", response_model=List[AgendaEventResponse])
async def list_events(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    events = (
        db.query(AgendaEvent)
        .filter(AgendaEvent.user_id == user.id)
        .order_by(AgendaEvent.start_at)
        .all()
    )
    return events


@router.post("", response_model=AgendaEventResponse, status_code=http_status.HTTP_201_CREATED)
async def create_event(
    payload: AgendaEventCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    new_event = AgendaEvent(
        user_id=current_user.id,
        title=payload.title,
        description=payload.description,
        start_at=payload.start_at,
        end_at=payload.end_at,
        status=payload.status or "a_faire",
        reminder_sent=False,
        reminder=payload.reminder,
        reminder_custom_minutes=payload.reminder_custom_minutes,
    )
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    return new_event


@router.put("/{event_id}", response_model=AgendaEventResponse)
async def update_event(
    event_id: int,
    payload: AgendaEventUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = (
        db.query(AgendaEvent)
        .filter(AgendaEvent.id == event_id, AgendaEvent.user_id == user.id)
        .first()
    )
    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Événement introuvable.",
        )

    update_data = payload.model_dump(exclude_unset=True)

    reminder_related_fields = {
        "start_at",
        "end_at",
        "reminder",
        "reminder_custom_minutes",
    }
    if reminder_related_fields & update_data.keys():
        event.reminder_sent = False

    for field, value in update_data.items():
        setattr(event, field, value)

    db.commit()
    db.refresh(event)
    return event


@router.delete("/{event_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = (
        db.query(AgendaEvent)
        .filter(AgendaEvent.id == event_id, AgendaEvent.user_id == user.id)
        .first()
    )
    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Événement introuvable.",
        )

    db.delete(event)
    db.commit()
    return None
