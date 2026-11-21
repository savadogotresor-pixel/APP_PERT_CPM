"""Analyse assistée des risques d'un planning PERT/CPM (IA-RISK-01 à IA-RISK-05).

Les alertes sont TOUJOURS calculées par des règles déterministes à partir des
résultats du moteur. Le service IA ne peut qu'ajouter une *appréciation*
textuelle dans ``appreciation_ia`` : la recommandation, la justification, la
preuve et le niveau indicatif restent intégralement pilotés par les règles.

Aucune date absolue n'est inférée : les ES/EF/LS/LF sont des jours relatifs et
le niveau des alertes est toujours indicatif (pas de prédiction de fin).
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping

from services import ia as service_ia

AVERTISSEMENT_INDICATIF = (
    "Niveau indicatif : aucune histoire statistique validée, "
    "pas de prédiction de date d'achèvement."
)

SEUIL_MARGE_FAIBLE = 1
SEUIL_SUCCESSEURS = 2
SEUIL_PREDECESSEURS = 3

CLES_DATE = ('date_debut', 'date_debut_absolue', 'debut', 'date_reference')


def _est_mapping(valeur) -> bool:
    return isinstance(valeur, Mapping)


def _propre(valeur):
    """Ramène un nombre entier exact (0.0) à un entier (0) pour les preuves."""
    if isinstance(valeur, bool):
        return valeur
    if isinstance(valeur, float) and math.isfinite(valeur) and valeur.is_integer():
        return int(valeur)
    return valeur


def _nombre(valeur) -> str:
    """Formate une valeur calculée sans virgule superflue."""
    valeur = _propre(valeur)
    if isinstance(valeur, float) and math.isfinite(valeur):
        return ("%.10f" % valeur).rstrip('0').rstrip('.').replace('.', ',')
    return str(valeur)


def _unite(valeur) -> str:
    nombre = _propre(valeur)
    return "jour" if nombre in (0, 1) else "jours"


def _liste(codes) -> str:
    return ", ".join(str(code) for code in codes)


def _valeurs(resultat, cle):
    if not _est_mapping(resultat):
        return None
    return _propre(resultat.get(cle))


def _marge(resultat):
    """Marge totale calculée, avec repli sur l'alias historique ``marge``."""
    if not _est_mapping(resultat):
        return None
    valeur = resultat.get('marge_totale')
    if valeur is None:
        valeur = resultat.get('marge')
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return None
    if not math.isfinite(float(valeur)):
        return None
    return _propre(valeur)


def _successeurs(successeurs, code) -> list[str]:
    if not _est_mapping(successeurs):
        return []
    bruts = successeurs.get(code) or ()
    if isinstance(bruts, (str, bytes)) or not isinstance(bruts, (list, tuple, set)):
        return []
    return [str(element) for element in bruts if element is not None]


def _predecesseurs(infos) -> list[str]:
    if not _est_mapping(infos):
        return []
    bruts = infos.get('depend_de') or ()
    if isinstance(bruts, (str, bytes)) or not isinstance(bruts, (list, tuple, set)):
        return []
    return [str(element) for element in bruts if element is not None]


def _nouvelle_alerte(code_tache, titre, justification, elements,
                     recommandation, preuve) -> dict:
    """Construit une alerte conforme au contrat, avec mention indicative."""
    return {
        'code_tache': code_tache,
        'titre': titre,
        'niveau': 'indicatif',
        'justification': (
            f"{str(justification).strip()} {AVERTISSEMENT_INDICATIF}".strip()
        ),
        'origine': 'regle',
        'recommandation': str(recommandation),
        'appreciation_ia': None,
        'preuve': dict(preuve),
        'elements_justificatifs': [str(element) for element in elements],
    }


def _alerte_marge(code, resultat, marge) -> dict:
    """IA-RISK-01 : marge nulle (tâche critique) ou marge faible (<= 1 jour)."""
    nulle = marge <= 0
    nature = "nulle" if nulle else "faible"
    preuve = {
        'marge': _propre(marge),
        'ES': _valeurs(resultat, 'ES'),
        'EF': _valeurs(resultat, 'EF'),
        'LS': _valeurs(resultat, 'LS'),
        'LF': _valeurs(resultat, 'LF'),
    }
    elements = [
        f"marge totale = {_nombre(marge)} {_unite(marge)}",
        f"ES = {_nombre(preuve['ES'])}",
        f"EF = {_nombre(preuve['EF'])}",
        f"LS = {_nombre(preuve['LS'])}",
        f"LF = {_nombre(preuve['LF'])}",
    ]
    if nulle:
        justification = (
            f"La marge totale calculée de la tâche {code} est de "
            f"{_nombre(marge)} {_unite(marge)} : cette tâche est critique, "
            "tout retard sur elle retarde la fin du projet."
        )
        recommandation = (
            f"Surveiller la tâche {code} en priorité et prévoir une marge de "
            "sécurité ou une réduction de durée dès qu'un retard apparaît."
        )
    else:
        justification = (
            f"La marge totale calculée de la tâche {code} est de "
            f"{_nombre(marge)} {_unite(marge)} : sa tolérance avant de retarder "
            "la fin du projet est très faible."
        )
        recommandation = (
            f"Surveiller la tâche {code} et identifier une tâche voisine dont "
            "la durée pourrait être réduite si un retard apparaît."
        )
    return _nouvelle_alerte(
        code, f"Marge {nature} sur la tâche {code}", justification, elements,
        recommandation, preuve,
    )


def _alerte_bloquante(code, successeurs_code) -> dict:
    """IA-RISK-02 : plusieurs successeurs dépendent de cette activité."""
    elements = [
        f"{len(successeurs_code)} successeurs : {_liste(successeurs_code)}",
    ]
    justification = (
        f"La tâche {code} précède {len(successeurs_code)} autres tâches "
        f"({_liste(successeurs_code)}) : elle joue un rôle de blocage, sa durée "
        "et sa date de fin conditionnent plusieurs branches du planning."
    )
    recommandation = (
        f"Vérifier les dépendances sortantes de {code} et séquencer ses "
        "successeurs avant toute modification de sa durée."
    )
    return _nouvelle_alerte(
        code, f"Tâche bloquante : {code}", justification, elements,
        recommandation, {'successeurs': list(successeurs_code)},
    )


def _alerte_convergence(code, predecesseurs) -> dict:
    """IA-RISK-02 : plusieurs dépendances convergent vers cette activité."""
    elements = [
        f"{len(predecesseurs)} prédécesseurs : {_liste(predecesseurs)}",
    ]
    justification = (
        f"La tâche {code} dépend de {len(predecesseurs)} prédécesseurs "
        f"({_liste(predecesseurs)}) : un retard sur l'un d'eux retarde son début."
    )
    recommandation = (
        f"Vérifier la livraison des prédécesseurs de {code} et prévoir un "
        "point de contrôle avant son démarrage."
    )
    return _nouvelle_alerte(
        code, f"Convergence de dépendances sur {code}", justification, elements,
        recommandation, {'predecesseurs': list(predecesseurs)},
    )


def _alerte_date_manquante(duree_totale) -> dict:
    """Alerte globale : aucune date de début absolue n'est disponible."""
    justification = (
        "La durée totale calculée est fournie mais aucune date de début absolue "
        "n'est disponible : les valeurs ES/EF/LS/LF sont des jours relatifs. "
        "Aucune date d'achèvement n'est déduite."
    )
    recommandation = (
        "Renseigner une date de début absolue dans le projet pour convertir "
        "les jours relatifs en dates calendaires."
    )
    return _nouvelle_alerte(
        None, "Date de début non renseignée", justification,
        ['aucune date de début fournie'], recommandation,
        {'duree_totale': _propre(duree_totale), 'date_debut': None},
    )


def _date_debut_fournie(taches, resultats) -> bool:
    """Vrai si une date de début explicite figure dans les données reçues."""
    for source in (taches, resultats):
        if not _est_mapping(source):
            continue
        valeurs = source.values() if source is taches else (source,)
        for valeur in valeurs:
            if not _est_mapping(valeur):
                continue
            for cle in CLES_DATE:
                if valeur.get(cle) not in (None, ''):
                    return True
    return False


def _question_risque(alerte) -> str:
    """Question minimale et non fiable envoyée au fournisseur IA."""
    return (
        "Commenter ce risque : " + alerte['titre'] + ". "
        + "Éléments calculés : " + "; ".join(alerte['elements_justificatifs']) + "."
    )


def _texte_ia(reponse) -> str | None:
    """Extrait le texte d'une réponse de :func:`services.ia.repondre`."""
    if isinstance(reponse, Mapping):
        source = reponse.get('source')
        if source not in (None, 'ia'):
            return None  # repli déterministe du service : pas une appréciation IA
        texte = reponse.get('texte')
    else:
        texte = reponse
    if texte is None:
        return None
    texte = str(texte).strip()
    return texte or None


def _enrichir(alerte, contexte, taches, resultats, duree_totale,
              chemins_critiques, successeurs) -> None:
    """Ajoute uniquement ``appreciation_ia``, sans jamais remplacer la règle."""
    try:
        reponse = service_ia.repondre(
            _question_risque(alerte),
            copy.deepcopy(taches),
            copy.deepcopy(resultats),
            duree_totale,
            copy.deepcopy(chemins_critiques),
            None,
            copy.deepcopy(successeurs) if successeurs is not None else None,
        )
        appreciation = _texte_ia(reponse)
        if appreciation is None:
            return
        if not service_ia.verifier_nombres(appreciation, contexte):
            return
    except Exception:
        # Toute panne IA (réseau, garde-fou, service) retombe sur la règle seule.
        return
    alerte['appreciation_ia'] = appreciation
    alerte['origine'] = 'regle+ia'


def analyser_risques(taches: dict, resultats: dict, duree_totale,
                     chemins_critiques: list[list[str]], successeurs: dict | None = None,
                     avec_ia: bool = True) -> list[dict]:
    """Analyse les risques d'un planning à partir des résultats du moteur.

    Retourne une liste d'alertes : règles déterministes toujours exécutées,
    appréciation IA optionnelle et vérifiée. Les dictionnaires reçus ne sont
    jamais modifiés. Un projet vide ne produit aucune alerte.
    """
    taches = taches if _est_mapping(taches) else {}
    if not taches:
        return []
    resultats = resultats if _est_mapping(resultats) else {}
    chemins_critiques = chemins_critiques or []
    if not isinstance(chemins_critiques, (list, tuple)):
        chemins_critiques = []

    alertes = []
    for code, infos in taches.items():
        code = str(code)
        resultat = resultats.get(code) if _est_mapping(resultats) else None
        marge = _marge(resultat)
        if marge is not None and marge <= 0:
            alertes.append(_alerte_marge(code, resultat, marge))
        elif marge is not None and marge <= SEUIL_MARGE_FAIBLE:
            alertes.append(_alerte_marge(code, resultat, marge))

        successeurs_code = _successeurs(successeurs, code)
        if len(successeurs_code) >= SEUIL_SUCCESSEURS:
            alertes.append(_alerte_bloquante(code, successeurs_code))

        predecesseurs = _predecesseurs(infos)
        if len(predecesseurs) >= SEUIL_PREDECESSEURS:
            alertes.append(_alerte_convergence(code, predecesseurs))

    if duree_totale is not None and not _date_debut_fournie(taches, resultats):
        alertes.append(_alerte_date_manquante(duree_totale))

    if avec_ia:
        try:
            disponible = bool(service_ia.est_disponible())
        except Exception:
            disponible = False
        if disponible:
            try:
                contexte = service_ia.construire_contexte(
                    taches, resultats, duree_totale, chemins_critiques, successeurs
                )
            except Exception:
                contexte = None
            if contexte is not None:
                for alerte in alertes:
                    _enrichir(
                        alerte, contexte, taches, resultats, duree_totale,
                        chemins_critiques, successeurs,
                    )

    return alertes
