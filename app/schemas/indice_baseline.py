from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

#: Position de la mesure par rapport à la plage habituelle
BaselineVerdict = Literal["below", "within", "above"]
#: ok : plage calculée · insufficient : pas assez d'historique · no_data : aucune mesure
BaselineStatus = Literal["ok", "insufficient", "no_data"]


class IndiceBaselineResponse(BaseModel):
    """
    Plage habituelle d'un indice pour une parcelle, à la période de sa
    mesure la plus récente, calculée sur les années précédentes.
    Voir app/services/baseline_service.py pour la méthode.
    """

    indice_name: str
    reference_date: Optional[date] = None
    value: Optional[float] = None
    status: BaselineStatus
    #: 10e centile, médiane et 90e centile des années précédentes
    low: Optional[float] = None
    median: Optional[float] = None
    high: Optional[float] = None
    values_count: int = 0
    #: Années ayant fourni des mesures à cette période
    years: list[int] = []
    verdict: Optional[BaselineVerdict] = None
    message: str = ""
    #: Demi-largeur de la fenêtre, en jours, autour du même jour de l'année
    window_days: int
    #: Début de la plantation actuelle : seules les mesures postérieures
    #: servent de référence (None : tout l'historique de la parcelle)
    since: Optional[date] = None