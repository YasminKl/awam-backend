from datetime import datetime, timedelta, timezone

from app.celery_app import celery_app
from app.db.database import SessionLocal
from app.models.agenda_event import AgendaEvent
from app.models.utilisateur import User
from app.services.email_service import send_agenda_reminder_email


REMINDER_DELTAS = {
    "15_min": timedelta(minutes=15),
    "1_hour": timedelta(hours=1),
    "1_day": timedelta(days=1),
    "1_week": timedelta(weeks=1),
}


def get_reminder_delta(event: AgendaEvent) -> timedelta | None:
    if not event.reminder:
        return None
    if event.reminder == "custom":
        if event.reminder_custom_minutes is None:
            return None
        return timedelta(minutes=event.reminder_custom_minutes)
    return REMINDER_DELTAS.get(event.reminder)


@celery_app.task(name="app.tasks.agenda_tasks.check_and_send_reminders")
def check_and_send_reminders():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        events = (
            db.query(AgendaEvent)
            .filter(
                AgendaEvent.reminder_sent == False,
                AgendaEvent.status == "a_faire",
                AgendaEvent.start_at > now,
            )
            .all()
        )

        sent_count = 0

        for event in events:
            delta = get_reminder_delta(event)
            if delta is None:
                continue

            remind_at = event.start_at - delta
            if remind_at > now:
                continue

            user = db.query(User).filter(User.id == event.user_id).first()
            if not user:
                continue

            event_datetime_str = event.start_at.strftime("%d/%m/%Y à %H:%M")

            try:
                send_agenda_reminder_email(user.email, event.title, event_datetime_str)
                event.reminder_sent = True
                db.commit()
                sent_count += 1
            except Exception as e:
                print(f"🔴 Échec de l'envoi du rappel pour l'événement {event.id} : {e}")

        print(f"🔵 Vérification des rappels effectuée : {sent_count} email(s) envoyé(s).")
    finally:
        db.close()