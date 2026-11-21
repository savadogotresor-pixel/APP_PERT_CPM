"""Moteur PERT/CPM canonique, pur et indépendant de Flask.

Représentation AON (activité-sur-nœud) : ``taches`` est un dictionnaire
``{nom: {"duree": ..., "depend_de": [...], ...}}``. Les durées sont exprimées
en jours calendaires ; une durée nulle est valide (tâche instantanée).

Conventions (voir docs/convention_calcul.md) :
    ES = 0 sans prédécesseur, sinon max(EF des prédécesseurs)
    EF = ES + d
    D  = max(EF des tâches terminales)
    LF = D pour une tâche terminale, sinon min(LS des successeurs)
    LS = LF - d
    marge totale  MT = LS - ES
    marge libre   ML = min(ES des successeurs) - EF, ou D - EF si terminale
    critique      MT ≈ 0 (EPSILON)
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field

EPSILON = 1e-9


class ValidationError(Exception):
    """Erreur bloquante sur le graphe de tâches (cycle, durée, dépendance...)."""


@dataclass
class PERTResult:
    """Résultat complet d'un calcul PERT/CPM.

    ``resultats[nom]`` contient ES, EF, LS, LF, marge, marge_totale,
    marge_libre et critique. ``marge`` est un alias de ``marge_totale``
    conservé pour la compatibilité ascendante avec l'application Flask.
    """

    duree_totale: float
    resultats: dict[str, dict[str, float | int | bool]]
    chemins_critiques: list[list[str]] = field(default_factory=list)
    successeurs: dict[str, list[str]] = field(default_factory=dict)
    ordre: list[str] = field(default_factory=list)


def _dependances_normalisees(taches):
    """Extrait ``{nom: [prédécesseurs]}`` en ignorant les entrées invalides.

    La validation a déjà signalé les dépendances mal formées ; cette fonction
    sert uniquement à garantir que le calcul ne manipule que des chaînes
    propres (sans espaces en bordure).
    """
    dependances = {}
    for nom, infos in taches.items():
        if not isinstance(infos, Mapping):
            dependances[nom] = []
            continue
        bruts = infos.get("depend_de", ())
        if bruts is None:
            bruts = ()
        if isinstance(bruts, (list, tuple, set)):
            dependances[nom] = [d.strip() for d in bruts
                                if isinstance(d, str) and d.strip()]
        else:
            dependances[nom] = []
    return dependances


def _extraire_cycle(restants_noms, dependances, noms_connus):
    """Reconstruit itérativement un cycle lisible à partir des nœuds bloqués.

    Les nœuds restants après Kahn ont tous au moins un prédécesseur restant
    (les prédécesseurs inexistants sont signalés par ``validate``). On remonte
    les prédécesseurs jusqu'à retomber sur un nœud déjà vu, puis on oriente le
    cycle dans le sens des dépendances et on le fait commencer par le plus
    petit identifiant pour obtenir un message déterministe.
    """
    restants = set(restants_noms)
    index = {}
    chemin = []
    courant = restants_noms[0]

    while courant not in index:
        index[courant] = len(chemin)
        chemin.append(courant)
        preds = [d for d in dependances.get(courant, ()) if d in restants]
        if not preds:  # défense en profondeur : ne peut arriver qu'après une donnée invalide
            break
        courant = preds[0]

    if courant in index:
        cycle = list(reversed(chemin[index[courant]:]))
    else:
        cycle = list(chemin)

    if not cycle:
        return [str(courant), str(courant)]

    position = min(range(len(cycle)), key=lambda i: cycle[i])
    cycle = cycle[position:] + cycle[:position]
    cycle.append(cycle[0])
    return cycle


def _kahn(noms, dependances):
    """Tri topologique de Kahn, itératif, O(V + E).

    Lève :class:`ValidationError` dès qu'un cycle empêche de traiter tous les
    nœuds ; aucun parcours récursif et aucune boucle sans borne.
    """
    noms_connus = set(noms)
    successeurs = {nom: [] for nom in noms}
    restants = {}

    for nom in noms:
        # Déduplication : une dépendance répétée ne doit pas bloquer Kahn.
        deps = list(dict.fromkeys(d for d in dependances.get(nom, ())
                                  if d in noms_connus))
        restants[nom] = len(deps)
        for dep in deps:
            successeurs[dep].append(nom)

    file = deque(nom for nom in noms if restants[nom] == 0)
    ordre = []
    while file:
        nom = file.popleft()
        ordre.append(nom)
        for succ in successeurs[nom]:
            restants[succ] -= 1
            if restants[succ] == 0:
                file.append(succ)

    if len(ordre) != len(noms):
        bloques = [nom for nom in noms if restants[nom] > 0]
        cycle = _extraire_cycle(bloques, dependances, noms_connus)
        raise ValidationError("Dépendance circulaire détectée : " + " → ".join(cycle))

    return ordre


def validate(taches):
    """Valide le graphe et retourne *tous* les messages d'erreur (vide si OK).

    Vérifie : identifiants vides/dupliqués (défense en profondeur),
    prédécesseurs inexistants, durées numériques finies non booléennes et non
    négatives, cycles. Une durée de 0 est acceptée : elle représente une tâche
    instantanée (jalon) et n'est pas une erreur.
    """
    erreurs = []

    if not isinstance(taches, Mapping):
        return ["Le graphe de tâches doit être un dictionnaire nom -> informations"]

    noms = list(taches.keys())
    noms_connus = set(noms)
    identifiants_vus = set()

    for nom in noms:
        if not isinstance(nom, str) or not nom.strip():
            erreurs.append(f"Identifiant de tâche vide ou invalide : {nom!r}")
            continue
        propre = nom.strip()
        if propre in identifiants_vus:
            erreurs.append(f"Identifiant de tâche en double : \"{propre}\"")
        identifiants_vus.add(propre)

    dependances = {}
    for nom, infos in taches.items():
        if not isinstance(infos, Mapping):
            erreurs.append(f"Informations invalides pour la tâche \"{nom}\" : dictionnaire attendu")
            dependances[nom] = []
            continue

        duree = infos.get("duree")
        if isinstance(duree, bool):
            erreurs.append(f"Durée invalide pour la tâche \"{nom}\" : un booléen n'est pas une durée")
        elif not isinstance(duree, (int, float)):
            erreurs.append(f"Durée invalide pour la tâche \"{nom}\" : {duree!r} n'est pas un nombre")
        elif not math.isfinite(duree):
            erreurs.append(f"Durée invalide pour la tâche \"{nom}\" : la durée doit être finie")
        elif duree < 0:
            erreurs.append(f"Durée invalide pour la tâche \"{nom}\" : {duree} est négative")

        bruts = infos.get("depend_de", ())
        if bruts is None:
            bruts = ()
        if isinstance(bruts, (str, bytes)) or not isinstance(bruts, (list, tuple, set)):
            erreurs.append(f"Prédécesseurs invalides pour la tâche \"{nom}\" : liste attendue")
            dependances[nom] = []
            continue

        propres = []
        for dep in bruts:
            if not isinstance(dep, str) or not dep.strip():
                erreurs.append(f"Prédécesseur vide ou invalide pour la tâche \"{nom}\" : {dep!r}")
                continue
            propre = dep.strip()
            propres.append(propre)
            if propre not in noms_connus:
                erreurs.append(f"La tâche \"{nom}\" dépend de \"{propre}\", qui n'existe pas")
        dependances[nom] = propres

    if not erreurs:
        try:
            _kahn(noms, dependances)
        except ValidationError as exc:
            erreurs.append(str(exc))

    return erreurs


def topological_order(taches):
    """Retourne l'ordre topologique (Kahn) ; lève ``ValidationError`` sinon."""
    erreurs = validate(taches)
    if erreurs:
        raise ValidationError("\n".join(erreurs))
    return _kahn(list(taches.keys()), _dependances_normalisees(taches))


def _propre(x):
    """Ramène un résidu numérique (≈ EPSILON) exactement à zéro."""
    return 0 if isinstance(x, (int, float)) and abs(x) <= EPSILON else x


def compute(taches):
    """Calcule le planning complet et lève ``ValidationError`` si invalide.

    Toutes les erreurs de :func:`validate` sont jointes dans l'exception.
    """
    erreurs = validate(taches)
    if erreurs:
        raise ValidationError("\n".join(erreurs))

    noms = list(taches.keys())
    dependances = _dependances_normalisees(taches)
    ordre = _kahn(noms, dependances)

    successeurs = {nom: [] for nom in noms}
    for nom in noms:
        for dep in dependances[nom]:
            successeurs[dep].append(nom)

    # Passe avant : ES / EF dans l'ordre topologique.
    avant = {}
    for nom in ordre:
        deps = dependances[nom]
        es = 0 if not deps else max(avant[dep]["EF"] for dep in deps)
        ef = es + taches[nom]["duree"]
        avant[nom] = {"ES": es, "EF": ef}

    terminaux = [nom for nom in noms if not successeurs[nom]]
    duree_totale = max((avant[nom]["EF"] for nom in terminaux), default=0)

    # Passe arrière : LF / LS en sens inverse de l'ordre topologique.
    arriere = {}
    for nom in reversed(ordre):
        succs = successeurs[nom]
        lf = duree_totale if not succs else min(arriere[s]["LS"] for s in succs)
        ls = lf - taches[nom]["duree"]
        arriere[nom] = {"LS": ls, "LF": lf}

    resultats = {}
    for nom in noms:
        es, ef = avant[nom]["ES"], avant[nom]["EF"]
        ls, lf = arriere[nom]["LS"], arriere[nom]["LF"]
        mt = _propre(ls - es)
        ml = _propre(min(avant[s]["ES"] for s in successeurs[nom]) - ef
                     if successeurs[nom] else duree_totale - ef)
        resultats[nom] = {
            "ES": es,
            "EF": ef,
            "LS": ls,
            "LF": lf,
            "marge": mt,           # alias historique
            "marge_totale": mt,
            "marge_libre": ml,
            "critique": abs(mt) <= EPSILON,
        }

    # Énumération de TOUS les chemins critiques : sous-graphe des tâches
    # critiques, de chaque source à chaque puits. Parcours itératif, dans
    # l'ordre topologique, donc sans récursion et sans explosion de pile.
    critique = {nom: resultats[nom]["critique"] for nom in noms}
    chemins_vers = {}
    for nom in ordre:
        if not critique[nom]:
            continue
        preds_critiques = [p for p in dependances[nom] if critique[p]]
        if preds_critiques:
            chemins_vers[nom] = [chemin + [nom]
                                 for p in preds_critiques
                                 for chemin in chemins_vers.get(p, ())]
        else:
            chemins_vers[nom] = [[nom]]

    puits = [nom for nom in ordre
             if critique[nom] and not any(critique[s] for s in successeurs[nom])]
    chemins_critiques = [chemin for nom in puits
                         for chemin in chemins_vers.get(nom, ())]

    return PERTResult(
        duree_totale=duree_totale,
        resultats=resultats,
        chemins_critiques=chemins_critiques,
        successeurs=successeurs,
        ordre=ordre,
    )


def liste_chemins_critiques(taches):
    """Retourne tous les chemins critiques (listes ordonnées de noms)."""
    return compute(taches).chemins_critiques


if __name__ == '__main__':
    try:
        from .data import taches
    except ImportError:  # exécution directe : python logic/pert.py
        from data import taches

    resultat = compute(taches)
    # ASCII pour les flèches : la console Windows utilise souvent cp1252.
    print(f"Durée totale : {resultat.duree_totale} jour(s)")
    print("Ordre topologique :", " -> ".join(resultat.ordre))
    print(f"{'Tâche':<8}{'ES':>6}{'EF':>6}{'LS':>6}{'LF':>6}"
          f"{'MT':>6}{'ML':>6}  Critique")
    for nom in resultat.ordre:
        r = resultat.resultats[nom]
        print(f"{nom:<8}{r['ES']:>6}{r['EF']:>6}{r['LS']:>6}{r['LF']:>6}"
              f"{r['marge_totale']:>6}{r['marge_libre']:>6}  "
              f"{'oui' if r['critique'] else 'non'}")
    for chemin in resultat.chemins_critiques:
        print("Chemin critique :", " -> ".join(chemin))
