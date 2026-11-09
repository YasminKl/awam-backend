"""
Réglages de la page « Climat et cultures » : un seul endroit pour les
modifier.

    CONDITIONS_NORMALS_START=2020
    CONDITIONS_NORMALS_END=2025

Chaque réglage peut être remplacé dans .env, comme ceux du climat.
"""

import os


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


#: Période des normales : la normale d'un mois est la moyenne du même mois
#: sur ces années (historique météo de la ferme, depuis 2020)
NORMALS_START = _int("CONDITIONS_NORMALS_START", 2020)
NORMALS_END = _int("CONDITIONS_NORMALS_END", 2025)

#: Jour sec : moins de 1 mm de pluie. Seuil de l'indice CDD (jours secs
#: consécutifs) du groupe d'experts ETCCDI de l'OMM
DRY_DAY_MM = _float("CONDITIONS_DRY_DAY_MM", 1.0)

#: Un mois compte pour la normale s'il lui manque au plus ce nombre de jours
MAX_MISSING_DAYS = _int("CONDITIONS_MAX_MISSING_DAYS", 3)

#: Premier mois de la saison, pour le cumul de l'eau de l'onglet Impacts :
#: septembre, début de la campagne agricole en Tunisie (de septembre à août)
SEASON_START_MONTH = _int("CONDITIONS_SEASON_START_MONTH", 9)
if NORMALS_START > NORMALS_END:
    raise ValueError("CONDITIONS_NORMALS_START doit précéder CONDITIONS_NORMALS_END")