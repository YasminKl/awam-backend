# ======================================================================
# Filtrage des warnings AVANT tout import
# ======================================================================
import warnings
warnings.filterwarnings("ignore", message=".*DoesNotConformTo.*")

import ssl
from celery import Celery
from celery.schedules import crontab

from app.core.config import REDIS_URL


celery_app = Celery(
    "awam_queues",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "app.tasks.agenda_tasks",
        "app.tasks.parcel_tasks",
        "app.tasks.ingestion_tasks",   # 🆕 14 indices spectraux
    ],
)

# ------------------------------------------------------------------
# Sérialisation & suivi
# ------------------------------------------------------------------
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
)

# ------------------------------------------------------------------
# SSL (Upstash exige TLS)
# ------------------------------------------------------------------
celery_app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
celery_app.conf.redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}

# ------------------------------------------------------------------
# Reconnexion (Upstash ferme les connexions inactives après quelques
# minutes ; ces réglages accélèrent/fiabilisent la reconnexion au lieu
# d'attendre la prochaine tâche planifiée pour s'en apercevoir)
# ------------------------------------------------------------------
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.broker_transport_options = {
    "socket_keepalive": True,
    "retry_policy": {"timeout": 5.0},
}

# ------------------------------------------------------------------
# Timezone
# ------------------------------------------------------------------
celery_app.conf.timezone = "Africa/Tunis"
celery_app.conf.enable_utc = False

# ------------------------------------------------------------------
# 3 QUEUES SÉPARÉES
# ------------------------------------------------------------------
celery_app.conf.task_queues = {
    "awam_queues:agenda":    {"exchange": "awam_queues:agenda",    "routing_key": "awam_queues:agenda"},
    "awam_queues:parcelles": {"exchange": "awam_queues:parcelles", "routing_key": "awam_queues:parcelles"},
    "awam_queues:alertes":   {"exchange": "awam_queues:alertes",   "routing_key": "awam_queues:alertes"},
}

celery_app.conf.task_default_queue = "awam_queues:parcelles"

# ------------------------------------------------------------------
# ROUTING : chaque tâche → sa queue
#
# IMPORTANT : la casse doit correspondre EXACTEMENT au nom de tâche
# enregistré (celui affiché dans les logs sous [tasks]), sinon le
# pattern ne matche jamais et la tâche part silencieusement vers
# task_default_queue. Vérifie que ton dossier réel est bien
# `app/tasks/` (minuscules) — sur Windows ça passe inaperçu, sur
# Linux (déploiement) ça casserait l'import.
# ------------------------------------------------------------------
celery_app.conf.task_routes = {
    "app.tasks.agenda_tasks.*":  {"queue": "awam_queues:agenda"},
    "parcels.*": {"queue": "awam_queues:parcelles"},
    "app.tasks.parcel_tasks.*":  {"queue": "awam_queues:parcelles"},
    "app.tasks.ingestion_tasks.*": {"queue": "awam_queues:parcelles"},  # 🆕
}

# ------------------------------------------------------------------
# BEAT (planificateur)
# ------------------------------------------------------------------
celery_app.conf.beat_schedule = {
    # ------------------------------------------------------------------
    # Rappels d'agenda (existant) — toutes les 15 min
    # ------------------------------------------------------------------
    "check-agenda-reminders": {
        "task": "app.tasks.agenda_tasks.check_and_send_reminders",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "awam_queues:agenda"},
    },

    # ------------------------------------------------------------------
    # 🆕 Ingestion quotidienne des 14 indices spectraux
    #    Tous les jours à 6h du matin (heure Tunis)
    # ------------------------------------------------------------------
    "ingest-parcel-indices-daily": {
        "task": "parcels.ingest_all_active_parcels",
        "schedule": crontab(hour=6, minute=0),
        "options": {
            "queue": "awam_queues:parcelles",
            "expires": 3600,  # expire après 1h si pas exécutée (évite l'accumulation)
        },
    },
}