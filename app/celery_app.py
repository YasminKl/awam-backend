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
        "app.tasks.weather_tasks",     # 🆕 historique météo
        "app.tasks.history_tasks",     # 🆕 historique Sentinel-2 sur 3 ans
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
# 4 QUEUES SÉPARÉES
#
# « historique » : tâches longues (3 ans d'images par parcelle). Elle a
# son PROPRE worker, pour ne jamais bloquer les indices du jour :
#
#   Worker principal (n'écoute PAS l'historique) :
#     celery -A app.celery_app worker --pool=solo -n principal@%h
#            -Q awam_queues:agenda,awam_queues:parcelles,awam_queues:alertes
#
#   Worker historique :
#     celery -A app.celery_app worker --pool=solo -n historique@%h
#            -Q awam_queues:historique
#
# ⚠️  Sans l'option -Q, un worker écoute TOUTES les files, y compris
#     l'historique : il faut donc la préciser sur le worker principal.
# ------------------------------------------------------------------
celery_app.conf.task_queues = {
    "awam_queues:agenda":     {"exchange": "awam_queues:agenda",     "routing_key": "awam_queues:agenda"},
    "awam_queues:parcelles":  {"exchange": "awam_queues:parcelles",  "routing_key": "awam_queues:parcelles"},
    "awam_queues:alertes":    {"exchange": "awam_queues:alertes",    "routing_key": "awam_queues:alertes"},
    "awam_queues:historique": {"exchange": "awam_queues:historique", "routing_key": "awam_queues:historique"},
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
    "weather.*": {"queue": "awam_queues:parcelles"},  # 🆕 météo
    "history.*": {"queue": "awam_queues:historique"},  # 🆕 historique, worker dédié

}

# ------------------------------------------------------------------
# BEAT (planificateur)
# ------------------------------------------------------------------
celery_app.conf.beat_schedule = {
    # ------------------------------------------------------------------
    # Historique : toutes les heures (à h15), les parcelles actives sans
    # historique sont repérées et leur historique lancé. Tâche légère
    # (une requête), sur le worker historique.
    # ------------------------------------------------------------------
    "history-check-missing": {
        "task": "history.check_missing",
        "schedule": crontab(minute=15),
        "options": {
            "queue": "awam_queues:historique",
            "expires": 3000,
        },
    },

    # ------------------------------------------------------------------
    # Rappels d'agenda (existant) — toutes les 15 min
    # ------------------------------------------------------------------
    "check-agenda-reminders": {
        "task": "app.tasks.agenda_tasks.check_and_send_reminders",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "awam_queues:agenda"},
    },
    

    "sync-weather-daily": {
        "task": "weather.sync_daily",
        "schedule": crontab(hour=5, minute=30),
        "options": {
            "queue": "awam_queues:parcelles",
            "expires": 3600,
        },
    },

    # ------------------------------------------------------------------
    # Recherche des nouvelles images et calcul des 15 indices
    # Toutes les 3 heures, de 6h à 21h (6h, 9h, 12h, 15h, 18h, 21h, heure
    # de Tunis) : une image publiée dans la journée apparaît au plus tard
    # 3 heures après, sans attendre le lendemain.
    #
    # Sans nouvelle image, rien n'est retéléchargé : la scène déjà en base
    # est reconnue et ignorée. Les diagnostics ne sont pas dupliqués (une
    # ligne par culture et par date d'image), et l'image de la carte n'est
    # régénérée que si elle est plus ancienne que les indices.
    # ------------------------------------------------------------------
    "ingest-parcel-indices-daily": {
        "task": "parcels.ingest_all_active_parcels",
        "schedule": crontab(minute=0, hour="6-21/3"),
        "options": {
            "queue": "awam_queues:parcelles",
            "expires": 3600,  # expire après 1h si pas exécutée (évite l'accumulation)
        },
    },
}