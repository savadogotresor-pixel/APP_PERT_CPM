"""Simulation déterministe de réductions de durées (cahier des charges OPT-01..05).

Le service ne prédit rien : il recalcule deux fois le planning avec le moteur
canonique :logic.pert.compute, une fois tel quel et une fois avec les durées
remplacées. Le planning transmis n'est jamais modifié (copie profonde), et
aucune écriture en base n'est effectuée ici : l'application ne peut appliquer
une simulation au planning de référence qu'après confirmation explicite.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping

from logic.pert import ValidationError, compute

LIMITES = (
    "Limites de la simulation : aucun coût, aucun budget ni aucune contrainte "
    "de ressources n'est pris en compte (les surcoûts éventuels d'accélération "
    "ne sont pas évalués) ; seules les relations Finish-to-Start (FS) sont "
    "supposées, les autres types de liaison ne sont pas modélisés ; une "
    "réduction de durée n'est pas garantie réalisable et doit être validée "
    "par l'équipe projet ; le planning de référence reste inchangé, la "
    "simulation est calculée sur une copie et n'est appliquée qu'après "
    "confirmation explicite de l'utilisateur."
)


def _valider_reductions(taches, reductions) -> dict:
    """Vérifie la demande et renvoie les nouvelles durées sous forme d'entiers."""
    if not isinstance(taches, Mapping):
        raise ValueError("Le graphe de tâches est invalide : un dictionnaire est attendu.")

    if not isinstance(reductions, Mapping) or not reductions:
        raise ValueError(
            "Aucune réduction demandée : indiquez au moins une tâche et sa "
            "nouvelle durée."
        )

    validees = {}
    for code, duree in reductions.items():
        if code not in taches:
            raise ValueError(
                f"Tâche inconnue « {code} » : elle n'existe pas dans le planning."
            )
        if isinstance(duree, bool) or not isinstance(duree, (int, float)):
            raise ValueError(
                f"Durée invalide pour la tâche « {code} » : un entier supérieur "
                f"ou égal à 0 est attendu (reçu : {duree!r})."
            )
        if isinstance(duree, float) and (
            not math.isfinite(duree) or not duree.is_integer()
        ):
            raise ValueError(
                f"Durée invalide pour la tâche « {code} » : un entier est "
                f"attendu (reçu : {duree!r})."
            )
        if duree < 0:
            raise ValueError(
                f"Durée invalide pour la tâche « {code} » : une durée négative "
                f"n'est pas autorisée (reçu : {duree!r})."
            )
        validees[code] = int(duree)
    return validees


def _calculer(taches):
    """Appelle le moteur canonique et convertit ses erreurs en ValueError."""
    try:
        return compute(taches)
    except ValidationError as exc:
        raise ValueError(f"Graphe de tâches invalide : {exc}") from exc


def _resume(resultat) -> dict:
    """Extrait les données publiques d'un résultat PERT, sans alias mutable."""
    return {
        "duree_totale": resultat.duree_totale,
        "chemins_critiques": [list(chemin) for chemin in resultat.chemins_critiques],
        "resultats": {
            nom: dict(valeurs) for nom, valeurs in resultat.resultats.items()
        },
    }


def _hypotheses(modifications) -> str:
    """Décrit explicitement ce que la simulation suppose et ce qu'elle fige."""
    codes = ", ".join(f"« {m['code']} »" for m in modifications)
    return (
        "Simulation déterministe : on suppose que les durées des tâches "
        f"{codes} sont remplacées par les nouvelles durées indiquées ; toutes "
        "les autres durées, les dépendances et l'ordre des tâches restent "
        "strictement identiques au planning de référence. Le planning initial "
        "et le planning simulé sont tous deux recalculés par le moteur PERT/CPM "
        "avec les mêmes conventions (durées en jours, dépendances FS). Le gain "
        "est la différence entre la durée totale initiale et la durée totale "
        "simulée. Les tâches accélérables sont les tâches critiques du planning "
        "initial dont la durée est strictement supérieure à zéro."
    )


def simuler(taches: dict, reductions: dict[str, int]) -> dict:
    """Simule une réduction de durée sans toucher au planning de référence.

    ``taches`` utilise le format de :func:`logic.pert.compute`
    (``{code: {'duree': ..., 'depend_de': [...]}}``). ``reductions`` associe un
    code de tâche à sa NOUVELLE durée entière >= 0 (et non au nombre de jours
    retranchés). Le dictionnaire ``taches`` reçu est copié en profondeur et
    n'est jamais modifié.

    Retourne un dictionnaire contenant ``initial`` et ``simule`` (chacun avec
    ``duree_totale``, ``chemins_critiques`` et ``resultats``), ``gains``,
    ``modifications`` (code, avant, apres), ``taches_accelerables``,
    ``hypotheses`` et ``limites``.

    Lève :class:`ValueError` (en français) si aucune réduction n'est demandée,
    si un code est inconnu, si une nouvelle durée n'est pas un entier >= 0 ou
    si le graphe de tâches est invalide.
    """
    reductions_validees = _valider_reductions(taches, reductions)

    planning = copy.deepcopy(taches)
    initial = _calculer(planning)

    for code, nouvelle_duree in reductions_validees.items():
        planning[code]["duree"] = nouvelle_duree
    simule = _calculer(planning)

    modifications = [
        {"code": code, "avant": taches[code]["duree"], "apres": nouvelle_duree}
        for code, nouvelle_duree in reductions_validees.items()
    ]
    accelerables = [
        code for code, valeurs in initial.resultats.items()
        if valeurs["critique"] and taches[code]["duree"] > 0
    ]

    return {
        "initial": _resume(initial),
        "simule": _resume(simule),
        "gains": initial.duree_totale - simule.duree_totale,
        "modifications": modifications,
        "taches_accelerables": accelerables,
        "hypotheses": _hypotheses(modifications),
        "limites": LIMITES,
    }
