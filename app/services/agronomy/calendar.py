"""
Calendrier phénologique tunisien.

Pour chaque espèce, la période de l'année de chaque stade, exprimée en
mois. C'est ce fichier qui répond à : « quel est le stade de cette
culture aujourd'hui ? », sans quoi aucun seuil ne peut être appliqué.

SOURCE : document de référence agronomique AWAM, « tableau des cycles
phénologiques des principales cultures de la plateforme AWAM (Sidi
Mechreg – Makther) », et sa feuille « Etat des plantes par stade
phénologique » qui fixe la nomenclature.

NOMENCLATURE — neuf stades, les mêmes pour toutes les espèces :
    dormance        activité physiologique minimale
    debourrement    nouveaux bourgeons et jeunes pousses
    croissance      développement des feuilles, tiges et rameaux
    floraison       ouverture des fleurs
    nouaison        formation des jeunes fruits après fécondation
    developpement   grossissement des fruits ou accumulation de biomasse
    maturation      les organes récoltés atteignent leur qualité
    recolte         collecte des fruits, feuilles ou fleurs
    apres_recolte   régénération, réserves, préparation du cycle suivant

STADES SIMULTANÉS — les périodes se chevauchent parce que la plante est
réellement dans plusieurs états à la fois : on récolte le thym pendant
qu'il mûrit et qu'il finit de fleurir. Un seul stade ne peut donc pas
décrire une date. `get_stages_at` renvoie un StageState : un stade
PRINCIPAL, choisi par STAGE_PRIORITY, et les stades SECONDAIRES.

Le principal est celui qui explique le signal satellite : la récolte
d'abord (coupe), l'après-récolte ensuite (repousse), puis la dormance
(couvert minimal), puis les stades de développement du couvert. La
floraison et la nouaison, invisibles dans un pixel de 10 m, viennent en
dernier. `get_stage_at` renvoie le principal et garde sa signature.

Les seuils, eux, ne dépendent PAS du stade : chaque règle du document
des indices porte sa propre période (thresholds.get_rules_at). Les deux
tableaux du document ne découpent pas l'année de la même façon ; le
stade sert à l'affichage et à la neutralisation des récoltes.

APRÈS-RÉCOLTE — le document décrit cette colonne en toutes lettres pour
les aromatiques (« reprise après coupe », « taille hivernale ») sans
donner de mois. Règle appliquée : l'après-récolte occupe les mois
qu'aucun stade daté ne couvre. Pour le thym elle donne août–novembre,
soit exactement la période qui avait été saisie à la main dans la
version précédente de ce fichier. Quand la colonne « dormance » du
document est elle aussi en texte et décrit l'hiver (« repos hivernal »,
« croissance ralentie en hiver »), les mois libres lui reviennent :
c'est le cas du romarin, de la menthe et du géranium.

Elle n'est PAS marquée `cultural_drop`. Le document y mentionne des
tailles, mais une taille est un événement d'un jour, pas un état qui
dure quatre mois : la marquer neutraliserait les alertes tout l'automne.
Les tailles sont traitées à leur date réelle, par les activités
déclarées (app/services/crop_monitoring.py).

Une période peut passer d'une année sur l'autre (novembre → février) :
`start_month` est alors supérieur à `end_month`.

DATE DE PLANTATION — elle est prise en compte de deux façons :
  - Avant la date de plantation, la culture n'a pas de stade : une image
    satellite antérieure ne la concerne pas.
  - Culture ANNUELLE (basilic) : le cycle est DÉCALÉ pour commencer au
    mois du semis, en gardant la durée de chaque stade. Le point d'ancrage
    est le débourrement et non la dormance, qui décrit chez le basilic les
    semences conservées et non la plante. Au-delà de la fin du cycle, la
    culture reste en après-récolte (« fin de cycle, récupération
    semences »). Limite : les durées du document correspondent à un semis
    de printemps ; hors saison, la croissance réelle peut être plus lente.
  - Culture PÉRENNE : le stade suit les saisons, le calendrier s'applique
    tel quel quelle que soit l'année de plantation ; l'âge agit sur les
    seuils, pas sur le stade. Après la récolte, le cycle reboucle sur
    l'après-récolte : on ne repart jamais de la plantation.

⚠️  GÉRANIUM — le document donne « 3 à 5 coupes/an » sans mois. Les deux
    saisons ci-dessous (juin–juillet, septembre–novembre) sont REPRISES
    DE LA VERSION PRÉCÉDENTE de ce fichier, faute de source. À confirmer
    par l'agronome.

⚠️  TAILLE DE L'OLIVIER ET DE L'AMANDIER — le document dit « Hiver »
    sans donner de mois. Aucune période ne la porte ici ; elle n'est
    reconnue que si elle est déclarée comme activité.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.services.agronomy.species import CropType, get_species


@dataclass(frozen=True)
class StagePeriod:
    stage: str
    start_month: int   # 1 = janvier
    end_month: int     # inclus ; < start_month si la période passe l'hiver
    #: Vrai si une baisse d'indice y est normale (coupe, récolte, taille)
    cultural_drop: bool = False

    def covers(self, month: int) -> bool:
        if self.start_month <= self.end_month:
            return self.start_month <= month <= self.end_month
        # Période à cheval sur deux années : novembre → février
        return month >= self.start_month or month <= self.end_month

    @property
    def label(self) -> str:
        months = ["janvier", "février", "mars", "avril", "mai", "juin",
                  "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
        if self.start_month == self.end_month:
            return months[self.start_month - 1]
        return f"{months[self.start_month - 1]}–{months[self.end_month - 1]}"


#: Rang du stade quand plusieurs couvrent la même date. Le plus petit
#: l'emporte et devient le stade PRINCIPAL.
STAGE_PRIORITY: dict[str, int] = {
    "recolte": 1,          # la coupe explique la chute d'indice
    "apres_recolte": 2,    # repousse, signal en remontée
    "dormance": 3,         # couvert minimal, indice bas normal
    "debourrement": 4,     # green-up, très visible
    "croissance": 5,
    "maturation": 6,
    "developpement": 7,
    "nouaison": 8,         # invisible sous le couvert
    "floraison": 9,        # invisible dans un pixel de 10 m
}


@dataclass(frozen=True)
class StageState:
    """
    État phénologique complet à une date : le stade qui commande, et
    ceux qui sont actifs en même temps.

    `principal` est ce que renvoie `get_stage_at` et ce qui s'affiche.
    `secondaires` évite de perdre les stades que la récolte recouvre —
    la maturation, notamment, qui occupe exactement les mêmes mois que
    la récolte chez l'amandier, la lavande, la verveine et le basilic.
    """

    principal: StagePeriod
    secondaires: tuple[StagePeriod, ...] = ()

    @property
    def stages(self) -> tuple[StagePeriod, ...]:
        """Tous les stades actifs, le principal en tête."""
        return (self.principal,) + self.secondaires

    @property
    def codes(self) -> tuple[str, ...]:
        """Codes des stades actifs, par priorité décroissante."""
        return tuple(p.stage for p in self.stages)

    def has(self, stage: str) -> bool:
        return stage in self.codes


PHENOLOGY: dict[str, tuple[StagePeriod, ...]] = {

    # ==================================================================
    # ARBORICULTURE
    # ==================================================================

    # Olivier — récolte oct-janv : table dès septembre-octobre, huile
    # jusqu'en janvier. Aucun mois libre, l'après-récolte se superpose
    # à la dormance et n'est donc pas datée. Débourrement en mars, comme
    # dans le document (et non mars-mai).
    "olivier": (
        StagePeriod("dormance", 12, 2),
        StagePeriod("debourrement", 3, 3),
        StagePeriod("croissance", 3, 5),
        StagePeriod("floraison", 4, 5),
        StagePeriod("nouaison", 5, 6),
        StagePeriod("developpement", 6, 9),
        StagePeriod("maturation", 10, 11),
        StagePeriod("recolte", 10, 1, cultural_drop=True),
    ),

    # Amandier — caduc : NDVI bas en hiver = normal. Récolte août-sept,
    # et non juin-août : juin et juillet sont le grossissement, où un
    # stress hydrique est déterminant pour le remplissage.
    "amandier": (
        StagePeriod("dormance", 12, 1),
        StagePeriod("debourrement", 1, 2),
        StagePeriod("croissance", 2, 3),
        StagePeriod("floraison", 1, 3),
        StagePeriod("nouaison", 3, 3),
        StagePeriod("developpement", 4, 8),
        StagePeriod("maturation", 8, 9),
        StagePeriod("recolte", 8, 9, cultural_drop=True),
        StagePeriod("apres_recolte", 10, 11),
    ),

    # Caroubier — floraison de fin d'été (août-oct), les gousses se
    # développent ensuite de novembre à août de l'année suivante.
    "caroubier": (
        StagePeriod("dormance", 12, 2),
        StagePeriod("debourrement", 3, 4),
        StagePeriod("croissance", 4, 5),
        StagePeriod("floraison", 8, 10),
        StagePeriod("nouaison", 10, 11),
        StagePeriod("developpement", 11, 8),
        StagePeriod("maturation", 8, 9),
        StagePeriod("recolte", 9, 10, cultural_drop=True),
        StagePeriod("apres_recolte", 11, 12),
    ),

    # ==================================================================
    # AROMATIQUES PÉRENNES
    # ==================================================================

    # Romarin — « plusieurs coupes : avril-oct ». Dormance = « croissance
    # ralentie en hiver », datée sur les mois libres (nov-janv).
    "romarin": (
        StagePeriod("dormance", 11, 1),
        StagePeriod("debourrement", 2, 3),
        StagePeriod("croissance", 3, 5),
        StagePeriod("floraison", 3, 5),
        StagePeriod("recolte", 4, 10, cultural_drop=True),
    ),

    # Lavande — après-récolte sept-nov (« taille après récolte »).
    "lavande": (
        StagePeriod("dormance", 12, 2),
        StagePeriod("debourrement", 3, 3),
        StagePeriod("croissance", 3, 5),
        StagePeriod("floraison", 6, 7),
        StagePeriod("maturation", 7, 7),
        StagePeriod("recolte", 6, 8, cultural_drop=True),
        StagePeriod("apres_recolte", 9, 11),
    ),

    # Sauge — « plusieurs récoltes : avril-oct », « taille automnale ».
    "sauge": (
        StagePeriod("dormance", 12, 2),
        StagePeriod("debourrement", 3, 3),
        StagePeriod("croissance", 3, 5),
        StagePeriod("floraison", 5, 6),
        StagePeriod("maturation", 6, 8),
        StagePeriod("recolte", 4, 10, cultural_drop=True),
        StagePeriod("apres_recolte", 11, 11),
    ),

    # Menthe — « plusieurs coupes : avril-oct », « repos hivernal »,
    # daté sur les mois libres (nov-janv).
    "menthe": (
        StagePeriod("dormance", 11, 1),
        StagePeriod("debourrement", 2, 3),
        StagePeriod("croissance", 3, 5),
        StagePeriod("floraison", 6, 8),
        StagePeriod("recolte", 4, 10, cultural_drop=True),
    ),

    # Géranium rosat — DEUX saisons reprises de la version précédente :
    # le document dit « 3 à 5 coupes/an » sans donner de mois (voir
    # l'avertissement en tête de module). Août, entre les deux coupes,
    # porte « régénération après coupe ».
    "geranium": (
        StagePeriod("dormance", 12, 2),
        StagePeriod("debourrement", 3, 3),
        StagePeriod("croissance", 3, 5),
        StagePeriod("floraison", 5, 9),
        StagePeriod("recolte", 6, 7, cultural_drop=True),
        StagePeriod("apres_recolte", 8, 8),
        StagePeriod("recolte", 9, 11, cultural_drop=True),
    ),

    # Verveine odorante (Louiza) — caduque. Après-récolte oct-nov
    # (« taille hivernale »).
    "verveine": (
        StagePeriod("dormance", 12, 2),
        StagePeriod("debourrement", 3, 3),
        StagePeriod("croissance", 3, 5),
        StagePeriod("floraison", 6, 7),
        StagePeriod("maturation", 7, 9),
        StagePeriod("recolte", 6, 9, cultural_drop=True),
        StagePeriod("apres_recolte", 10, 11),
    ),

    # Thym — après-récolte août-nov (« taille, régénération végétative,
    # repos hivernal »), qui retombe exactement sur la période saisie à
    # la main dans la version précédente.
    "thym": (
        StagePeriod("dormance", 12, 2),
        StagePeriod("debourrement", 2, 3),
        StagePeriod("croissance", 3, 5),
        StagePeriod("floraison", 4, 6),
        StagePeriod("nouaison", 5, 6),
        StagePeriod("developpement", 5, 7),
        StagePeriod("maturation", 6, 7),
        StagePeriod("recolte", 5, 7, cultural_drop=True),
        StagePeriod("apres_recolte", 8, 11),
    ),

    # Citronnelle — DEUX saisons de récolte (juin-juil puis sept-oct),
    # et non un bloc continu : entre les deux, la touffe repousse et
    # doit rester surveillée.
    "citronnelle": (
        StagePeriod("dormance", 11, 2),
        StagePeriod("debourrement", 3, 4),
        StagePeriod("croissance", 4, 9),
        StagePeriod("developpement", 5, 10),
        StagePeriod("floraison", 9, 10),
        StagePeriod("maturation", 10, 10),
        StagePeriod("recolte", 6, 7, cultural_drop=True),
        StagePeriod("recolte", 9, 10, cultural_drop=True),
    ),

    # ==================================================================
    # ANNUELLE
    # ==================================================================

    # Basilic — la « dormance » du document désigne les semences
    # conservées (janv-mars) ; la levée commence au débourrement
    # (avril-mai), qui sert d'ancrage au décalage sur la date de semis.
    # L'après-récolte oct-déc porte « fin de cycle, récupération
    # semences » et clôt le cycle.
    "basilic": (
        StagePeriod("dormance", 1, 3),
        StagePeriod("debourrement", 4, 5),
        StagePeriod("croissance", 5, 7),
        StagePeriod("floraison", 6, 8),
        StagePeriod("nouaison", 7, 9),
        StagePeriod("developpement", 6, 9),
        StagePeriod("maturation", 8, 9),
        StagePeriod("recolte", 6, 9, cultural_drop=True),
        StagePeriod("apres_recolte", 10, 12),
    ),
}


#: Stade qui clôt le cycle d'une culture annuelle (« fin de cycle,
#: récupération semences » dans le document)
END_OF_CYCLE_STAGE = "apres_recolte"

#: Stade de repli si aucune période ne couvre la date : la plante est
#: entre deux cycles, donc en régénération. Ne devrait pas se produire,
#: les douze cultures couvrant les douze mois.
FALLBACK_STAGE = "apres_recolte"


def _is_annual(species: str) -> bool:
    spec = get_species(species)
    return spec is not None and spec.crop_type == CropType.ANNUAL


def _shift(period: StagePeriod, months: int) -> StagePeriod:
    """La même période, décalée de `months` mois (avec passage d'année)."""
    wrap = lambda m: (m - 1 + months) % 12 + 1
    return StagePeriod(period.stage, wrap(period.start_month), wrap(period.end_month), period.cultural_drop)


def _months_between(start: date, end: date) -> int:
    """Nombre de mois calendaires de `start` à `end` (0 = même mois)."""
    return (end.year - start.year) * 12 + end.month - start.month


def _cycle_anchor(periods: tuple[StagePeriod, ...]) -> int:
    """
    Mois de départ du cycle d'une annuelle, sur lequel la date de semis
    est calée. C'est le premier stade VIVANT : chez le basilic, la
    dormance décrit les semences conservées, pas la plante — un semis
    de mars ne doit pas caler le cycle sur janvier.
    """
    for period in periods:
        if period.stage != "dormance":
            return period.start_month
    return periods[0].start_month


def get_periods(species: str, planting_date: date | None = None) -> tuple[StagePeriod, ...]:
    """
    Toutes les périodes d'une espèce, dans l'ordre du cycle.

    Culture annuelle avec date de semis : le cycle est décalé pour
    commencer au mois du semis (voir l'en-tête du module).
    """
    periods = PHENOLOGY.get(species.lower(), ())
    if not periods or planting_date is None or not _is_annual(species):
        return periods
    offset = planting_date.month - _cycle_anchor(periods)
    return tuple(_shift(p, offset) for p in periods)


def _rank(period: StagePeriod) -> int:
    """Rang de priorité du stade ; un stade inconnu passe en dernier."""
    return STAGE_PRIORITY.get(period.stage, 99)


def get_stages_at(
    species: str, when: date, planting_date: date | None = None
) -> StageState | None:
    """
    Tous les stades actifs à une date, le principal en tête.

    Les périodes se chevauchent volontairement : on récolte le thym
    pendant qu'il mûrit et qu'il finit de fleurir. Le principal est le
    stade de plus petite priorité dans STAGE_PRIORITY, les autres sont
    conservés en secondaires, eux aussi triés par priorité.

    None uniquement si l'espèce est inconnue ou si la date précède la
    plantation.
    """
    if planting_date is not None and when < planting_date:
        return None   # pas encore plantée à cette date

    periods = get_periods(species, planting_date)
    if not periods:
        return None

    if planting_date is not None and _is_annual(species):
        end = next((p for p in periods if p.stage == END_OF_CYCLE_STAGE), None)
        if end is not None:
            cycle_months = (end.end_month - _cycle_anchor(periods)) % 12
            if _months_between(planting_date, when) > cycle_months:
                return StageState(end)   # cycle terminé

    matching = sorted((p for p in periods if p.covers(when.month)), key=_rank)
    if not matching:
        return StageState(StagePeriod(FALLBACK_STAGE, when.month, when.month))

    return StageState(matching[0], tuple(matching[1:]))


def get_stage_at(
    species: str, when: date, planting_date: date | None = None
) -> StagePeriod | None:
    """
    Stade PRINCIPAL de l'espèce à une date donnée.

    Raccourci sur `get_stages_at().principal`, conservé pour que les
    appelants qui n'attendent qu'un stade restent inchangés. Pour les
    seuils, utiliser `get_stages_at().codes` : une règle écrite pour la
    maturation doit rester applicable même quand la récolte commande.
    """
    state = get_stages_at(species, when, planting_date)
    return state.principal if state else None


def is_cultural_period(
    species: str, when: date, planting_date: date | None = None
) -> bool:
    """
    Vrai si une baisse d'indice est attendue à cette date (coupe, récolte,
    taille). Sert à neutraliser les fausses alertes.
    """
    if planting_date is not None and when < planting_date:
        return False
    periods = get_periods(species, planting_date)
    if not periods:
        return False
    return any(p.covers(when.month) and p.cultural_drop for p in periods)

    

#: Stades qui marquent une récolte dans le calendrier
HARVEST_STAGES = ("recolte", "recoltes")


def first_harvest_period(species: str, planting_date: date | None = None) -> StagePeriod | None:
    """Première période de récolte de l'espèce dans le cycle."""
    for period in get_periods(species, planting_date):
        if period.stage in HARVEST_STAGES:
            return period
    return None


def _next_harvest_start(periods: list[StagePeriod], ref: date) -> date:
    """
    Prochaine date de récolte à partir de `ref`. Si `ref` tombe dans une
    saison de récolte, c'est `ref` lui-même : la plante est prête et la
    saison est ouverte. Sinon, le début de la saison suivante. Plusieurs
    saisons possibles (ex. géranium).
    """
    candidates = []
    for period in periods:
        if period.covers(ref.month):
            candidates.append(ref)
        elif period.start_month > ref.month:
            candidates.append(date(ref.year, period.start_month, 1))
        else:
            candidates.append(date(ref.year + 1, period.start_month, 1))
    return min(candidates)


def expected_harvest_date(
    species: str,
    planting_date: date | None,
    *,
    orchard: bool = False,
    in_production: bool = False,
    today: date | None = None,
) -> date | None:
    """
    Date de récolte prévue, calculée À PARTIR DE LA DATE DE PLANTATION.

    - Culture annuelle (basilic) : plantation + délai avant la première
      récolte (app/services/agronomy/species.py, FIRST_HARVEST_DELAYS).
    - Plante pérenne : plantation + délai avant la première récolte, puis
      première saison de récolte de l'espèce à partir de cette date. Si
      cette date est déjà passée (plante adulte), c'est la prochaine saison
      à partir d'aujourd'hui : une plante pérenne se récolte chaque année.
      Une jeune plantation est donc récoltée plus tard qu'une plante adulte.
    - Arbre fruitier : uniquement si le verger est en production (case
      saisie par l'utilisateur), prochaine saison de récolte. Sinon None :
      le système n'invente pas une année de première récolte.

    Sans date de plantation : None.
    """
    if planting_date is None:
        return None
    today = today or date.today()

    if orchard:
        if not in_production:
            return None
        periods = [p for p in get_periods(species, None) if p.stage in HARVEST_STAGES]
        return _next_harvest_start(periods, max(planting_date, today)) if periods else None

    from app.services.agronomy.species import first_harvest_delay, is_annual

    delay = first_harvest_delay(species)
    earliest = planting_date + timedelta(days=delay.days if delay else 0)

    # Culture annuelle : la date découle directement de la plantation
    if is_annual(species):
        return earliest

    periods = [p for p in get_periods(species, None) if p.stage in HARVEST_STAGES]
    if not periods:
        return None
    # Jamais avant la date au plus tôt, ni dans une saison déjà passée
    return _next_harvest_start(periods, max(earliest, today))

def next_harvest_after(
    species: str,
    after: date,
    *,
    planting_date: date | None = None,
) -> date | None:
    """
    Début de la prochaine saison de récolte STRICTEMENT après `after`.

    Sert au rebouclage des cultures pérennes : une récolte réelle ne
    termine pas la culture, elle ouvre le cycle suivant. La saison qui
    contient `after` est donc écartée — sinon la date prévue resterait
    collée à la récolte qui vient d'avoir lieu.

    Les espèces à plusieurs saisons (géranium) sont gérées : après une
    coupe de juin, c'est la saison de septembre qui est renvoyée, pas
    celle de juin l'année suivante.
    """
    periods = [
        p for p in get_periods(species, planting_date) if p.stage in HARVEST_STAGES
    ]
    if not periods:
        return None

    candidates = []
    for period in periods:
        if period.covers(after.month) or period.start_month <= after.month:
            # Saison en cours ou déjà passée : même saison l'année suivante
            candidates.append(date(after.year + 1, period.start_month, 1))
        else:
            candidates.append(date(after.year, period.start_month, 1))
    return min(candidates)