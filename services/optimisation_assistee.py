"""Optimisation assistée : suggestions de scénarios recalculés (IA-OPT-01 à IA-OPT-06, §19.4).

Ce service ne prend aucune décision et n'écrit jamais dans le planning de
référence. Il identifie les tâches critiques à partir des résultats PERT/CPM
calculés (IA-OPT-01), propose plusieurs scénarios dont les hypothèses sont
explicites (IA-OPT-02), fait recalculer CHACUN d'eux par le moteur canonique
:func:`logic.pert.compute` via :func:`services.optimisation.simuler`
(IA-OPT-03), compare durées, chemins critiques et marges (IA-OPT-04), expose
les compromis, risques et limites de coût/ressources (IA-OPT-05) et laisse le
planning de référence inchangé tant que l'utilisateur n'a pas confirmé
(IA-OPT-06).

Le fast-tracking n'est qu'une hypothèse : aucune contrainte métier n'est
fournie au service, donc le chevauchement éventuel doit être validé par
l'équipe projet. Le crashing reste indicatif en l'absence de durées normales /
accélérées et de coûts. Aucun chiffre n'est inventé : tous ceux qui apparaissent
dans les textes proviennent de ``simuler()`` ou de ``compute()``.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping

from logic.pert import ValidationError, compute
from services.optimisation import LIMITES as LIMITES_SIMULATION
from services.optimisation import simuler

#: Nombre maximal de scénarios renvoyés (une place est réservée au fast-tracking
#: lorsqu'il est calculable, les autres allant aux meilleures compressions).
MAX_SCENARIOS = 6

LIMITE_NOMBRE_SCENARIOS = (
    f"Au plus {MAX_SCENARIOS} scénarios sont proposés (limite d'affichage du service)."
)
LIMITE_CRASHING = (
    "En l'absence de durées normales/accélérées et de coûts, le crashing reste "
    "indicatif : aucun surcoût ni besoin en ressources n'est évalué."
)
LIMITE_FAST_TRACKING = (
    "Limites du fast-tracking — aucune contrainte métier n'a été fournie : "
    "ce scénario est une hypothèse à valider, pas une recommandation."
)
RAPPEL_REFERENCE = (
    "Le planning de référence n'est pas modifié : toute application nécessite "
    "une confirmation explicite de l'utilisateur."
)


def _est_mapping(valeur) -> bool:
    return isinstance(valeur, Mapping)


def _nombre(valeur) -> str:
    """Formate un nombre calculé sans virgule superflue (30.0 -> 30)."""
    if isinstance(valeur, bool) or valeur is None:
        return str(valeur)
    if isinstance(valeur, int):
        return str(valeur)
    if isinstance(valeur, float):
        if not math.isfinite(valeur):
            return str(valeur)
        if valeur.is_integer():
            return str(int(valeur))
        return ("%.10f" % valeur).rstrip("0").rstrip(".").replace(".", ",")
    return str(valeur)


def _unite(valeur) -> str:
    try:
        return "jour" if abs(float(valeur) - 1.0) < 1e-9 else "jours"
    except (TypeError, ValueError):
        return "jours"


def _clair(valeur) -> float:
    """Clé de tri numérique défensive pour les gains."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return float("-inf")
    if not math.isfinite(valeur):
        return float("-inf")
    return float(valeur)


def _chemins_texte(chemins) -> str:
    if not chemins:
        return "aucun chemin critique"
    return "; ".join(
        " → ".join(str(code) for code in chemin)
        for chemin in chemins
        if isinstance(chemin, (list, tuple))
    )


def _resume(resultat) -> dict:
    """Extrait le même résumé public que :func:`services.optimisation.simuler`."""
    return {
        "duree_totale": resultat.duree_totale,
        "chemins_critiques": [list(chemin) for chemin in resultat.chemins_critiques],
        "resultats": {
            nom: dict(valeurs) for nom, valeurs in resultat.resultats.items()
        },
    }


def _duree_entiere(infos) -> int | float | None:
    """Retourne la durée entière exploitable, ou ``None`` si elle est invalide."""
    if not _est_mapping(infos):
        return None
    duree = infos.get("duree")
    if isinstance(duree, bool) or not isinstance(duree, (int, float)):
        return None
    if not math.isfinite(duree) or isinstance(duree, float) and not duree.is_integer():
        return None
    return duree


def _taches_critiques(taches, resultats) -> list[str]:
    """IA-OPT-01 : tâches critiques à durée positive, d'après les résultats fournis."""
    codes = []
    for code, valeurs in resultats.items():
        if not _est_mapping(valeurs) or not valeurs.get("critique"):
            continue
        if code not in taches or code in codes:
            continue
        duree = _duree_entiere(taches[code])
        if duree is not None and duree > 0:
            codes.append(code)
    return codes


def _reduction(duree) -> int:
    """Un jour, ou 10 % arrondi au plus proche (minimum 1) dès 10 jours."""
    if duree >= 10:
        reduction = int(math.floor(duree * 0.1 + 0.5))
    else:
        reduction = 1
    return max(1, min(reduction, int(duree)))


def _marge(valeurs):
    """Marge totale d'une tâche, compatible avec l'alias historique ``marge``."""
    if not _est_mapping(valeurs):
        return None
    if "marge_totale" in valeurs:
        return valeurs["marge_totale"]
    return valeurs.get("marge")


def _est_critique(valeurs) -> bool:
    return bool(_est_mapping(valeurs) and valeurs.get("critique"))


def _compromis(resultat) -> str:
    """Décrit le gain, les nouvelles criticités, les marges et les limites."""
    initial = resultat.get("initial", {})
    simule = resultat.get("simule", {})
    avant = initial.get("resultats", {}) if _est_mapping(initial) else {}
    apres = simule.get("resultats", {}) if _est_mapping(simule) else {}
    gain = resultat.get("gains")

    devenues_critiques = [
        code for code, valeurs in apres.items()
        if _est_critique(valeurs) and not _est_critique(avant.get(code))
    ]
    marges = []
    for code, valeurs in apres.items():
        avant_marge = _marge(avant.get(code))
        apres_marge = _marge(valeurs)
        if avant_marge != apres_marge:
            marges.append(
                f"{code} ({_nombre(avant_marge)} → {_nombre(apres_marge)} "
                f"{_unite(apres_marge)})"
            )
    if marges:
        effet_marges = "marges modifiées : " + ", ".join(marges)
    else:
        effet_marges = "aucune marge totale ne change"

    debut = initial.get("duree_totale") if _est_mapping(initial) else None
    fin = simule.get("duree_totale") if _est_mapping(simule) else None
    return " ".join((
        f"Gain obtenu : {_nombre(gain)} {_unite(gain)} "
        f"(durée totale {_nombre(debut)} → {_nombre(fin)} {_unite(fin)}).",
        "Tâches devenues critiques : "
        + (", ".join(devenues_critiques) if devenues_critiques else "aucune") + ".",
        f"Effet sur les marges : {effet_marges}.",
        "Compromis et risques : aucun coût ni ressource n'est modélisé ; "
        "la faisabilité, les besoins en ressources et le surcoût éventuel "
        "doivent être validés par l'équipe projet. " + RAPPEL_REFERENCE,
    ))


def _scenario_crashing(code, reductions, resultat) -> dict:
    """Construit un scénario de compression à partir du retour exact de ``simuler``."""
    modifications = resultat.get("modifications") or []
    avant_d = modifications[0]["avant"] if modifications else reductions[code]
    apres_d = modifications[0]["apres"] if modifications else reductions[code]
    titre = (
        f"Compression de {code} : {_nombre(avant_d)} → {_nombre(apres_d)} "
        f"{_unite(apres_d)}"
    )
    description = (
        f"Réduire la durée de la tâche « {code} » de {_nombre(avant_d)} à "
        f"{_nombre(apres_d)} {_unite(apres_d)}. Comparaison recalculée par le "
        f"moteur : durée totale {_nombre(resultat['initial']['duree_totale'])} → "
        f"{_nombre(resultat['simule']['duree_totale'])} "
        f"{_unite(resultat['simule']['duree_totale'])} ; chemins critiques "
        f"{_chemins_texte(resultat['initial']['chemins_critiques'])} → "
        f"{_chemins_texte(resultat['simule']['chemins_critiques'])}."
    )
    limites = " ".join((
        resultat["limites"], LIMITE_CRASHING,
        LIMITE_NOMBRE_SCENARIOS, RAPPEL_REFERENCE,
    ))
    return {
        "titre": titre,
        "description": description,
        "type": "crashing",
        "reductions": dict(reductions),
        "dependance_retiree": None,
        "hypotheses": resultat["hypotheses"],
        "limites": limites,
        "compromis": _compromis(resultat),
        "resultat": resultat,
        "planning_reference_modifie": False,
    }


def _premiere_arete(chemins):
    """Première arête (prédécesseur, successeur) du premier chemin utilisable."""
    for chemin in chemins:
        if isinstance(chemin, (list, tuple)) and len(chemin) >= 2:
            pred, succ = chemin[0], chemin[1]
            if isinstance(pred, str) and pred.strip() and isinstance(succ, str) and succ.strip():
                return pred.strip(), succ.strip()
    return None


def _hypothese_fast_tracking(pred, succ) -> str:
    return (
        f"Fast-tracking : on retire la dépendance « {pred} → {succ} » sur une copie "
        "du planning et on recalcule avec le moteur PERT/CPM ; ce scénario "
        "suppose que le chevauchement est autorisé par les contraintes métier "
        "(qui n'ont pas été fournies)."
    )


def _scenario_fast_tracking(planning, chemins):
    """Recalcule un chevauchement hypothétique, sans toucher au planning source."""
    arete = _premiere_arete(chemins)
    if arete is None:
        return None
    pred, succ = arete
    if succ not in planning or pred not in planning:
        return None

    infos = planning[succ]
    dependances = infos.get("depend_de") if _est_mapping(infos) else None
    if not isinstance(dependances, (list, tuple, set)):
        return None
    dependances = [d.strip() for d in dependances if isinstance(d, str) and d.strip()]
    if pred not in dependances:
        return None

    copie = copy.deepcopy(planning)
    copie[succ]["depend_de"] = [d for d in dependances if d != pred]
    try:
        avant = compute(copy.deepcopy(planning))
        apres = compute(copie)
    except (ValidationError, ValueError, TypeError, KeyError):
        return None

    resultat = {
        "initial": _resume(avant),
        "simule": _resume(apres),
        "gains": avant.duree_totale - apres.duree_totale,
        "modifications": [],
        "taches_accelerables": [
            code for code, valeurs in avant.resultats.items()
            if _est_critique(valeurs)
            and (_duree_entiere(planning.get(code)) or 0) > 0
        ],
        "hypotheses": _hypothese_fast_tracking(pred, succ),
        "limites": " ".join((
            LIMITE_FAST_TRACKING, LIMITES_SIMULATION,
            LIMITE_NOMBRE_SCENARIOS, RAPPEL_REFERENCE,
        )),
    }
    titre = f"Fast-tracking : retirer la dépendance {pred} → {succ}"
    description = (
        f"Retirer la dépendance « {pred} → {succ} » située sur un chemin critique "
        "sur une copie du planning (chevauchement supposé autorisé). Comparaison "
        f"recalculée par le moteur : durée totale "
        f"{_nombre(resultat['initial']['duree_totale'])} → "
        f"{_nombre(resultat['simule']['duree_totale'])} "
        f"{_unite(resultat['simule']['duree_totale'])} ; chemins critiques "
        f"{_chemins_texte(resultat['initial']['chemins_critiques'])} → "
        f"{_chemins_texte(resultat['simule']['chemins_critiques'])}."
    )
    return {
        "titre": titre,
        "description": description,
        "type": "fast_tracking",
        "reductions": {},
        "dependance_retiree": (pred, succ),
        "hypotheses": resultat["hypotheses"],
        "limites": resultat["limites"],
        "compromis": _compromis(resultat),
        "resultat": resultat,
        "planning_reference_modifie": False,
    }


def suggerer_scenarios(
    taches: dict,
    resultats: dict,
    duree_totale,
    chemins_critiques: list[list[str]],
    successeurs: dict | None = None,
) -> list[dict]:
    """Propose des scénarios d'optimisation, tous recalculés et non appliqués.

    ``taches`` et ``resultats`` suivent le format du moteur (voir
    :func:`logic.pert.compute`). Les entrées sont copiées en profondeur et ne
    sont jamais modifiées, pas plus que le planning de référence. Chaque
    scénario porte son propre résultat de simulation (contrat de
    :func:`services.optimisation.simuler`) et reste à valider par l'utilisateur.

    Retourne une liste vide si le projet est vide ou si aucune tâche critique à
    durée positive ne peut être identifiée ; sinon au plus ``MAX_SCENARIOS``
    scénarios, triés par gain décroissant puis par titre.
    """
    planning = copy.deepcopy(taches) if _est_mapping(taches) else {}
    if not planning:
        return []
    donnees = copy.deepcopy(resultats) if _est_mapping(resultats) else {}
    chemins = copy.deepcopy(chemins_critiques) if isinstance(chemins_critiques, (list, tuple)) else []
    # Copié lui aussi : ce service ne modifie aucun dictionnaire reçu, même
    # lorsqu'une information n'est pas directement utilisée.
    successeurs_copies = copy.deepcopy(successeurs) if _est_mapping(successeurs) else None

    critiques = _taches_critiques(planning, donnees)
    if not critiques:
        return []

    # Si les chemins critiques fournis sont incomplets, on retombe sur ceux du
    # moteur pour pouvoir tenter le fast-tracking (jamais sur une donnée inventée).
    if not chemins:
        try:
            chemins = compute(planning).chemins_critiques
        except (ValidationError, ValueError, TypeError, KeyError):
            chemins = []

    scenarios = []
    for code in critiques:
        duree = _duree_entiere(planning[code])
        if duree is None or duree <= 0:
            continue
        nouvelle_duree = int(duree) - _reduction(duree)
        if nouvelle_duree < 0:
            continue
        reductions = {code: nouvelle_duree}
        try:
            resultat = simuler(planning, reductions)
        except (ValueError, TypeError, KeyError):
            continue
        if not _est_mapping(resultat) or "gains" not in resultat:
            continue
        scenarios.append(_scenario_crashing(code, reductions, resultat))

    fast_tracking = _scenario_fast_tracking(planning, chemins)
    if fast_tracking is not None:
        scenarios.append(fast_tracking)

    compression = sorted(
        (s for s in scenarios if s["type"] == "crashing"),
        key=lambda s: (-_clair(s["resultat"].get("gains")), s["titre"]),
    )
    autres = [s for s in scenarios if s["type"] != "crashing"][:1]
    places = MAX_SCENARIOS - len(autres)
    retenus = compression[:places] + autres
    retenus.sort(key=lambda s: (-_clair(s["resultat"].get("gains")), s["titre"]))
    return retenus
