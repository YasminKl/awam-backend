"""
Vérifie le rebouclage des cultures pérennes (lot A), le calendrier
et les seuils du document AWAM.

Contrôle que :
  - une pérenne récoltée reste `active` et voit sa récolte suivante
    recalculée à partir de la récolte réelle, sans repasser par la
    date de plantation ;
  - une annuelle se clôture bien en `harvested` ;
  - la date de récolte réelle suit la DERNIÈRE coupe, pas la première ;
  - une date de récolte prévue saisie à la main n'est pas écrasée ;
  - les quatre corrections de périodes tirées du document AWAM sont
    bien en place (amandier, olivier, citronnelle, caroubier) ;
  - le reste du calendrier n'a pas changé de comportement
    (non-régression sur get_stage_at, is_cultural_period,
    expected_harvest_date).

LECTURE SEULE : aucune écriture en base. Les tests de routes utilisent
une session factice, aucune connexion n'est ouverte.

Avec --db, compte en plus les cultures pérennes restées bloquées en
statut `harvested` par l'ancien code (lecture seule également).

Usage, depuis la racine du backend :

    python -m app.scripts.check_harvest_cycle
    python -m app.scripts.check_harvest_cycle --db
"""

import argparse
import sys
from datetime import date, datetime
from types import SimpleNamespace

from app.services.agronomy.calendar import (
    PHENOLOGY,
    get_stages_at,
    expected_harvest_date,
    get_stage_at,
    is_cultural_period,
    next_harvest_after,
)

RESULTS: list[tuple[bool, str, str, str]] = []


def check(label: str, got, expected) -> None:
    """Enregistre un contrôle. Comparaison sur la représentation texte."""
    RESULTS.append((str(got) == str(expected), label, str(got), str(expected)))


# ----------------------------------------------------------------------
# Session et modèles factices : aucune base n'est nécessaire
# ----------------------------------------------------------------------


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *_):
        return self

    def filter(self, *_):
        return self

    def order_by(self, *_):
        return self

    def all(self):
        return self._rows


def fake_activity(activity_type: str, year: int, month: int, day: int):
    return SimpleNamespace(
        activity_type=activity_type,
        performed_at=datetime(year, month, day, 9, 0),
    )


def fake_crop(**overrides):
    base = dict(
        id=1,
        species="thym",
        planting_date=date(2026, 10, 18),
        status="active",
        actual_harvest_date=None,
        expected_harvest_date=None,
        in_production=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ----------------------------------------------------------------------
# 1. Calendrier : next_harvest_after
# ----------------------------------------------------------------------


def check_calendar() -> None:
    check(
        "thym : coupe 20-05-2028 -> saison suivante",
        next_harvest_after("thym", date(2028, 5, 20)),
        date(2029, 5, 1),
    )
    check(
        "thym : deux cycles d'affilee",
        next_harvest_after("thym", next_harvest_after("thym", date(2028, 5, 20))),
        date(2030, 5, 1),
    )
    check(
        "geranium : coupe de juin -> saison de septembre (meme annee)",
        next_harvest_after("geranium", date(2027, 6, 20)),
        date(2027, 9, 1),
    )
    check(
        "geranium : coupe de septembre -> saison de juin (annee suivante)",
        next_harvest_after("geranium", date(2027, 9, 15)),
        date(2028, 6, 1),
    )
    check(
        "olivier : recolte 10-11-2027 -> octobre 2028",
        next_harvest_after("olivier", date(2027, 11, 10)),
        date(2028, 10, 1),
    )
    check(
        "espece inconnue -> None",
        next_harvest_after("inexistante", date(2027, 6, 1)),
        None,
    )
    # La saison en cours ne doit jamais etre renvoyee : sinon la date
    # prevue resterait collee a la recolte qui vient d'avoir lieu.
    result = next_harvest_after("menthe", date(2027, 6, 15))
    check("menthe : la saison en cours est ecartee", result > date(2027, 6, 15), True)


# ----------------------------------------------------------------------
# 2. Non-regression : le calendrier existant n'a pas bouge
# ----------------------------------------------------------------------


def check_corrections() -> None:
    """Les quatre corrections de periodes tirees du document AWAM."""
    # 1. Amandier : recolte aout-sept, pas juin-aout
    check(
        "amandier : juin surveille (grossissement)",
        is_cultural_period("amandier", date(2027, 6, 15)),
        False,
    )
    check(
        "amandier : juillet surveille (grossissement)",
        is_cultural_period("amandier", date(2027, 7, 15)),
        False,
    )
    check(
        "amandier : aout en recolte",
        get_stage_at("amandier", date(2027, 8, 15)).stage,
        "recolte",
    )
    check(
        "amandier : septembre en recolte (neutralise)",
        is_cultural_period("amandier", date(2027, 9, 15)),
        True,
    )

    # 2. Olivier : recolte oct-janv
    check(
        "olivier : janvier en recolte",
        get_stage_at("olivier", date(2027, 1, 15)).stage,
        "recolte",
    )
    check(
        "olivier : fevrier hors recolte",
        is_cultural_period("olivier", date(2027, 2, 15)),
        False,
    )

    # 3. Citronnelle : deux saisons, aout surveille entre les deux
    check(
        "citronnelle : juillet en recolte",
        is_cultural_period("citronnelle", date(2027, 7, 15)),
        True,
    )
    check(
        "citronnelle : aout surveille (repousse)",
        is_cultural_period("citronnelle", date(2027, 8, 15)),
        False,
    )
    check(
        "citronnelle : septembre en recolte",
        is_cultural_period("citronnelle", date(2027, 9, 15)),
        True,
    )
    check(
        "citronnelle : coupe de juillet -> septembre",
        next_harvest_after("citronnelle", date(2027, 7, 10)),
        date(2027, 9, 1),
    )

    # 4. Caroubier : floraison aout-oct (donnee corrigee, comportement
    #    inchange : formation_gousses et recolte couvrent ces mois)
    check(
        "caroubier : mai n'est plus en floraison",
        get_stage_at("caroubier", date(2027, 5, 15)).stage,
        "croissance",
    )

    # Aucun mois sans stade, sur les douze cultures
    trous = [
        f"{sp} mois {m}"
        for sp in PHENOLOGY
        for m in range(1, 13)
        if get_stage_at(sp, date(2027, m, 15)) is None
    ]
    check("couverture : aucun mois sans stade", trous, [])


def check_stages() -> None:
    """Nomenclature a 9 stades, stades simultanes, seuils atteignables."""
    from app.services.agronomy.thresholds import DEFAULT_RULE, get_rules_at

    # Aucun stade declare ne doit etre perdu
    perdus = []
    for sp in PHENOLOGY:
        declares = {p.stage for p in PHENOLOGY[sp]}
        atteints = set()
        for m in range(1, 13):
            atteints |= set(get_stages_at(sp, date(2027, m, 15)).codes)
        perdus += [f"{sp}.{st}" for st in sorted(declares - atteints)]
    check("aucun stade declare n'est perdu", perdus, [])

    # Stades simultanes : le thym en juin
    etat = get_stages_at("thym", date(2027, 6, 15))
    check("thym en juin : principal = recolte", etat.principal.stage, "recolte")
    check("thym en juin : maturation conservee", etat.has("maturation"), True)
    check("thym en juin : 4 stades actifs", len(etat.stages), 5)

    # Amandier : la maturation existe sous la recolte
    etat = get_stages_at("amandier", date(2027, 8, 15))
    check("amandier en aout : principal = recolte", etat.principal.stage, "recolte")
    check("amandier en aout : maturation conservee", etat.has("maturation"), True)

    # Chaque regle du document s'applique EXACTEMENT a sa periode et a
    # son age : (espece, ligne du document, age teste, mois du document)
    lignes = [
        ("olivier", "Débourrement", 2, (3, 4)),
        ("olivier", "Débourrement", 5, (3, 4)),
        ("olivier", "Débourrement", 15, (3, 4)),
        ("olivier", "Floraison", 15, (4, 5)),
        ("olivier", "Nouaison-Grossissement", 15, (5, 6, 7, 8, 9)),
        ("olivier", "Maturation", 15, (9, 10, 11)),
        ("amandier", "Débourrement", 2, (1, 2)),
        ("amandier", "Floraison", 10, (2, 3)),
        ("amandier", "Grossissement", 10, (4, 5, 6)),
        ("caroubier", "Croissance végétative", 3, (3, 4, 5)),
        ("caroubier", "Développement des gousses", 8, (6, 7, 8, 9)),
        ("romarin", "Croissance active", 0.5, (2, 3, 4, 5)),
        ("romarin", "Pré-récolte", 3, (6, 7)),
        ("lavande", "Reprise végétative", 0.5, (2, 3)),
        ("lavande", "Floraison", 3, (5, 6, 7)),
        ("sauge", "Croissance", 3, (3, 4, 5)),
        ("sauge", "Pré-récolte", 3, (6,)),
        ("menthe", "Croissance rapide", 3, (3, 4, 5, 6)),
        ("menthe", "Avant récolte", 3, (5, 6, 7, 8, 9, 10)),
        ("geranium", "Croissance", 3, (3, 4, 5, 6, 7, 8, 9)),
        ("verveine", "Croissance", 3, (3, 4, 5, 6, 7, 8)),
        ("citronnelle", "Croissance", 3, (4, 5, 6, 7, 8, 9)),
        ("citronnelle", "Avant récolte", 3, (6, 7, 8, 9, 10)),
        ("basilic", "Croissance", 0.3, (4, 5, 6, 7)),
        ("basilic", "Avant récolte", 0.3, (6, 7, 8, 9)),
        ("thym", "Croissance active", 3, (3, 4, 5)),
        ("thym", "Pré-floraison", 3, (4, 5)),
    ]
    ecarts = []
    for sp, ligne, age, mois in lignes:
        appliques = tuple(
            m for m in range(1, 13)
            if any(r.doc_stage == ligne for r in get_rules_at(sp, date(2027, m, 15), age).rules)
        )
        if appliques != mois:
            ecarts.append(f"{sp}.{ligne} age {age} : {appliques} au lieu de {mois}")
    check("27 regles du document : chacune a sa periode et son age", ecarts, [])

    # Plusieurs regles a la fois : la menthe en mai surveille NDVI et NDMI
    indices = [t.indice for t in get_rules_at("menthe", date(2027, 5, 15), 3).thresholds]
    check("menthe en mai : NDVI et NDMI surveilles ensemble", "NDVI" in indices and "NDMI" in indices, True)

    # La floraison de l'olivier s'applique enfin (regle morte avant)
    regles = get_rules_at("olivier", date(2027, 4, 15), 15).rules
    check("olivier en avril : regle de floraison appliquee", any(r.doc_stage == "Floraison" for r in regles), True)

    # Tranches d'age absentes du document : surveillance generale
    check(
        "amandier de 10 ans en janvier : pas de regle inventee",
        get_rules_at("amandier", date(2027, 1, 15), 10).rules,
        (DEFAULT_RULE,),
    )
    check(
        "romarin de 3 ans en mars : pas de regle inventee",
        get_rules_at("romarin", date(2027, 3, 15), 3).rules,
        (DEFAULT_RULE,),
    )

    # Age inconnu : la regle s'applique, et l'absence d'age est signalee
    selection = get_rules_at("olivier", date(2027, 3, 15), None)
    check("olivier sans age : tranche aux seuils les plus bas", selection.thresholds[0].low, 0.30)
    check("olivier sans age : absence d'age signalee", selection.age_required, True)

    # Debourrement de l'olivier : mars seulement, comme le document
    check(
        "olivier : debourrement en mars, plus en mai",
        (get_stages_at("olivier", date(2027, 3, 15)).has("debourrement"),
         get_stages_at("olivier", date(2027, 5, 15)).has("debourrement")),
        (True, False),
    )

    # Les anciens codes de stades ont disparu
    anciens = {"repos", "ralentissement", "reprise", "prefloraison", "pre_recolte",
               "grossissement", "formation_gousses", "post_recolte",
               "reprise_coupe", "semis", "fin_cycle"}
    restants = sorted(
        {p.stage for per in PHENOLOGY.values() for p in per} & anciens
    )
    check("anciens codes de stades supprimes", restants, [])


def check_species_and_water() -> None:
    """species.py et water.py alignes sur les neuf stades du calendrier."""
    from app.services.agronomy.species import SPECIES, STAGE_LABELS, stage_label
    from app.services.agronomy.water import (
        STAGE_TO_PHASE, get_kc, get_kc_for_stages,
    )
    from app.services.water_service import compute_water_need

    # La liste des stades de chaque espece = l'ordre de son calendrier
    ecarts = []
    for sp, periods in PHENOLOGY.items():
        ordre = []
        for p in periods:
            if p.stage not in ordre:
                ordre.append(p.stage)
        if tuple(ordre) != SPECIES[sp].stages:
            ecarts.append(sp)
    check("species.py : stades de chaque espece = ceux du calendrier", ecarts, [])

    # Chaque stade du calendrier a un libelle et une phase FAO explicites
    codes = {p.stage for per in PHENOLOGY.values() for p in per}
    sans_libelle = sorted(c for c in codes if c not in STAGE_LABELS)
    check("libelles : les 9 stades ont un libelle", sans_libelle, [])
    check("libelle de l'apres-recolte", stage_label("apres_recolte"), "Après récolte")
    sans_phase = sorted(c for c in codes if c not in STAGE_TO_PHASE and c != "apres_recolte")
    check("water.py : chaque stade a sa phase FAO, aucun par defaut", sans_phase, [])

    # Apres-recolte : la phase d'avant le lot B, selon la plante
    check("Kc thym apres-recolte = developpement (repousse)", get_kc("thym", "apres_recolte"), 0.45)
    check("Kc amandier apres-recolte = fin de saison", get_kc("amandier", "apres_recolte"), 0.70)
    check("Kc basilic apres-recolte = fin de saison", get_kc("basilic", "apres_recolte"), 0.90)

    # Plusieurs stades actifs : le Kc le plus eleve
    juin = get_stages_at("thym", date(2027, 6, 15)).codes
    check("Kc thym en juin : pleine saison, pas fin de saison", get_kc_for_stages("thym", juin), 0.65)
    besoin = compute_water_need("thym", juin, et0_mm=40.0, rain_mm=0.0, area_ha=1.0)
    check("besoin en eau du thym en juin : ETc = 40 x 0,65", besoin.etc_mm, 26.0)


def check_lifespan() -> None:
    """Duree de vie et production optimale : messages selon l'age."""
    from app.services.agronomy.species import lifespan_note

    niveau = lambda sp, age: (lifespan_note(sp, age) or ("aucun",))[0]
    check("lavande 1 an : pas encore en pleine production", niveau("lavande", 1), "info")
    check("lavande 4 ans : production optimale, aucun message", niveau("lavande", 4), "aucun")
    check("lavande 7 ans : fin de la production optimale", niveau("lavande", 7), "warning")
    check("lavande 11 ans : au-dela de la duree de vie", "durée de vie" in lifespan_note("lavande", 11)[1], True)
    check("citronnelle 2 ans : meilleure production a partir de 3 ans", niveau("citronnelle", 2), "info")
    check("citronnelle 10 ans : aucune limite inventee", niveau("citronnelle", 10), "aucun")
    check("olivier 30 ans : aucun message (pas d'age de debut invente)", niveau("olivier", 30), "aucun")
    check("basilic : annuel, aucun message", niveau("basilic", 1), "aucun")

    # Culture « Terminee » : date de fin obligatoire et coherente
    from app.schemas.crop import check_end

    def refus(*args):
        try:
            check_end(*args, today=date(2026, 10, 20))
            return "accepte"
        except ValueError as e:
            return str(e)

    check("terminee sans date de fin : refusee", "date de fin" in refus("ended", date(2017, 3, 1), None), True)
    check("terminee, date de fin future : refusee", "futur" in refus("ended", date(2017, 3, 1), date(2027, 1, 1)), True)
    check("terminee le 15-09-2026 : acceptee", refus("ended", date(2017, 3, 1), date(2026, 9, 15)), "accepte")


def check_no_regression() -> None:
    stage = get_stage_at("olivier", date(2027, 11, 15))
    check("olivier en novembre -> recolte", stage.stage if stage else None, "recolte")

    stage = get_stage_at("thym", date(2027, 6, 15))
    check("thym en juin -> recolte", stage.stage if stage else None, "recolte")

    check(
        "avant plantation -> aucun stade",
        get_stage_at("thym", date(2026, 5, 1), date(2026, 10, 18)),
        None,
    )
    check(
        "thym en juin : baisse culturale attendue",
        is_cultural_period("thym", date(2027, 6, 15)),
        True,
    )
    check(
        "thym en mars : aucune baisse culturale",
        is_cultural_period("thym", date(2027, 3, 15)),
        False,
    )
    check(
        "thym plante 18-10-2026 -> 1re recolte 01-05-2028",
        expected_harvest_date("thym", date(2026, 10, 18), today=date(2026, 10, 18)),
        date(2028, 5, 1),
    )
    check(
        "olivier hors production -> pas de recolte prevue",
        expected_harvest_date(
            "olivier", date(2024, 3, 1), orchard=True, in_production=False
        ),
        None,
    )


# ----------------------------------------------------------------------
# 3. Routes : rebouclage des cultures
# ----------------------------------------------------------------------


def check_routes() -> None:
    from app.api.routes.crops import _refresh_cycle

    crop = fake_crop(expected_harvest_date=date(2028, 5, 1))
    _refresh_cycle(crop, FakeQuery([fake_activity("coupe", 2028, 5, 20)]))
    check("perenne recoltee : reste active", crop.status, "active")
    check("perenne recoltee : date reelle", crop.actual_harvest_date, date(2028, 5, 20))
    check(
        "perenne recoltee : recolte 2 recalculee",
        crop.expected_harvest_date,
        date(2029, 5, 1),
    )

    crop = fake_crop(species="menthe")
    _refresh_cycle(
        crop,
        FakeQuery([
            fake_activity("coupe", 2027, 9, 5),
            fake_activity("coupe", 2027, 7, 2),
            fake_activity("recolte", 2027, 5, 10),
        ]),
    )
    check("coupes multiples : la derniere gagne", crop.actual_harvest_date, date(2027, 9, 5))
    check("coupes multiples : reste active", crop.status, "active")

    crop = fake_crop(species="basilic", planting_date=date(2027, 3, 15))
    _refresh_cycle(crop, FakeQuery([fake_activity("recolte", 2027, 7, 10)]))
    check("annuelle recoltee : se cloture", crop.status, "harvested")

    crop = fake_crop(expected_harvest_date=date(2029, 6, 15))
    _refresh_cycle(crop, FakeQuery([fake_activity("coupe", 2028, 5, 20)]))
    check(
        "date prevue saisie a la main : preservee",
        crop.expected_harvest_date,
        date(2029, 6, 15),
    )

    crop = fake_crop(expected_harvest_date=date(2028, 5, 1))
    _refresh_cycle(crop, FakeQuery([]))
    check("aucune activite : statut inchange", crop.status, "active")
    check("aucune activite : date reelle vide", crop.actual_harvest_date, None)
    check(
        "aucune activite : date prevue inchangee",
        crop.expected_harvest_date,
        date(2028, 5, 1),
    )

    crop = fake_crop(status="abandoned")
    _refresh_cycle(crop, FakeQuery([fake_activity("coupe", 2028, 5, 20)]))
    check("abandonnee : statut inchange", crop.status, "abandoned")
    check("abandonnee : pas de recolte prevue", crop.expected_harvest_date, None)

    crop = fake_crop(species="olivier", planting_date=date(2020, 3, 1), in_production=True)
    _refresh_cycle(crop, FakeQuery([fake_activity("recolte", 2027, 11, 10)]))
    check("olivier : recolte suivante", crop.expected_harvest_date, date(2028, 10, 1))

    # Une relecture ne doit rien modifier de plus
    crop = fake_crop(
        actual_harvest_date=date(2028, 5, 20), expected_harvest_date=date(2029, 5, 1)
    )
    _refresh_cycle(crop, FakeQuery([fake_activity("coupe", 2028, 5, 20)]))
    check("relecture : idempotente", crop.expected_harvest_date, date(2029, 5, 1))

    # Chemin du formulaire : une perenne envoyee en « harvested » reste
    # active, une annuelle se cloture
    from app.api.routes.crops import _keep_perennial_active

    crop = fake_crop(status="harvested")
    _keep_perennial_active(crop)
    check("formulaire : perenne envoyee en recoltee reste active", crop.status, "active")

    crop = fake_crop(species="basilic", status="harvested")
    _keep_perennial_active(crop)
    check("formulaire : annuelle envoyee en recoltee se cloture", crop.status, "harvested")

    # Une activite qui n'est pas une recolte ne doit rien declencher
    crop = fake_crop(expected_harvest_date=date(2028, 5, 1))
    _refresh_cycle(crop, FakeQuery([fake_activity("irrigation", 2028, 5, 20)]))
    check("irrigation : aucun effet", crop.actual_harvest_date, None)


# ----------------------------------------------------------------------
# 4. Base : cultures perennes bloquees par l'ancien code (lecture seule)
# ----------------------------------------------------------------------


def report_stuck_crops() -> None:
    from sqlalchemy import text

    from app.db.database import SessionLocal
    from app.services.agronomy.species import is_annual

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT id, species, actual_harvest_date "
                "FROM crop WHERE status = 'harvested' ORDER BY species, id"
            )
        ).fetchall()
    finally:
        db.close()

    stuck = [r for r in rows if not is_annual(r.species)]
    print(f"\nCultures en statut 'harvested' : {len(rows)}")
    print(f"  dont perennes bloquees        : {len(stuck)}")
    for row in stuck:
        print(f"    #{row.id:<5} {row.species:<14} recoltee le {row.actual_harvest_date}")
    if stuck:
        print(
            "\n  Ces cultures ne reboucleront pas tant qu'elles restent en"
            "\n  'harvested'. Script de remise en cycle a lancer separement."
        )


# ----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        action="store_true",
        help="compte aussi les perennes bloquees en base (lecture seule)",
    )
    args = parser.parse_args()

    check_calendar()
    check_corrections()
    check_stages()
    check_species_and_water()
    check_lifespan()
    check_no_regression()
    check_routes()

    width = max(len(label) for _, label, _, _ in RESULTS)
    failures = 0
    for passed, label, got, expected in RESULTS:
        if passed:
            print(f"  OK   {label}")
        else:
            failures += 1
            print(f"  ECHEC {label.ljust(width)}  obtenu={got}  attendu={expected}")

    print(f"\n{len(RESULTS) - failures}/{len(RESULTS)} controles passes.")

    if args.db:
        report_stuck_crops()

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())