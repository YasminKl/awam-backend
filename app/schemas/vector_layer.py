from typing import Any, List, Literal, Optional
from pydantic import BaseModel


class VectorFeature(BaseModel):
    type: Literal["Feature"]
    geometry: Optional[dict[str, Any]] = None
    # Varie selon la couche interrogée :
    #   - "parcelles"      -> {"id": ..., "nom": ..., "farm_id": ...}
    #   - "points_interet" -> {"id": ..., "nom": ...}
    properties: dict[str, Any]


class VectorFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"]
    features: List[VectorFeature]