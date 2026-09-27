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

# Texte affiché dans l'email pour chaque valeur de reminder préréglée
REMINDER_LABELS = {
    "15_min": "dans 15 minutes",
    "1_hour": "dans 1 heure",
    "1_day": "demain",
    "1_week": "dans 1 semaine",
}

# Pluriel pour l'unité d'un rappel personnalisé
CUSTOM_UNIT_LABELS = {
    "minutes": "minute(s)",
    "hours": "heure(s)",
    "days": "jour(s)",
}


def get_reminder_delta(event: AgendaEvent) -> timedelta | None:
    if not event.reminder:
        return None
    if event.reminder == "custom":
        if event.reminder_custom_minutes is None:
            return None
        return timedelta(minutes=event.reminder_custom_minutes)
    return REMINDER_DELTAS.get(event.reminder)


def get_reminder_label(event: AgendaEvent) -> str:
    """
    Texte lisible du délai réel choisi, pour l'email de rappel.

    Utilise getattr(..., None) plutôt qu'un accès direct : si le modèle
    AgendaEvent perd/renomme une colonne de rappel, on retombe sur une
    valeur par défaut au lieu de planter toute la tâche Celery.
    """
    reminder = getattr(event, "reminder", None)
    if not reminder:
        return "bientôt"

    if reminder == "custom":
        minutes = getattr(event, "reminder_custom_minutes", None)
        if not minutes:
            return "bientôt"
        unit = getattr(event, "reminder_custom_unit", None) or "minutes"
        unit_label = CUSTOM_UNIT_LABELS.get(unit, unit)
        return f"dans {minutes} {unit_label}"

    return REMINDER_LABELS.get(reminder, "bientôt")


@celery_app.task(name="app.tasks.agenda_tasks.check_and_send_reminders")
def check_and_send_reminders():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # Plus de filtre sur `status` : un rappel est envoyé pour
        # n'importe quel statut (a_faire, fait, reporte, annule...),
        # tant que le délai de rappel est atteint et qu'il n'a pas déjà
        # été envoyé.
        events = (
            db.query(AgendaEvent)
            .filter(
                AgendaEvent.reminder_sent == False,
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
            reminder_label = get_reminder_label(event)

            try:
                send_agenda_reminder_email(
                    user.email,
                    event.title,
                    event_datetime_str,
                    reminder_label,
                )
                event.reminder_sent = True
                db.commit()
                sent_count += 1
            except Exception as e:
                print(f"🔴 Échec de l'envoi du rappel pour l'événement {event.id} : {e}")

        print(f"🔵 Vérification des rappels effectuée : {sent_count} email(s) envoyé(s).")
    finally:
        db.close()