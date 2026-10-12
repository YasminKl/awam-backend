from app.models.utilisateur import User
from app.models.agenda_event import AgendaEvent
from .farm import *
from .parcel import *
from .activity import *
from .alert import *
from .indice_reading import *
from .raster import *               
from .employee import *
from .farm_employee import *
from app.models.parcel_analysis import *
from app.models.crop import Crop
from app.models.crop_observation import CropObservation
from app.models.crop_diagnosis import CropDiagnosis
from app.models.weather_daily import *

__all__ = [
    "User", "Farm", "Parcel", "Activity",
    "Employee", "Alert", "IndiceReading",
    "Raster", "SpatialReference",
        "Crop", "CropObservation", "CropDiagnosis",
]