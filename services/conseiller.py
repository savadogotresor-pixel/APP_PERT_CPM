"""Conseiller IA maison : moteur de règles déterministe, sans service externe.

Les conseils sont calculés exclusivement à partir de :class:`~logic.pert.PERTResult`
et du graphe de tâches déjà validé par le moteur canonique. Aucun appel réseau,
aucune clé d'API, aucune estimation : chaque valeur affichée dans une ``preuve``
provient du calcul PERT/CPM (durée totale, marges, décomptes de tâches, de
chemins critiques et de successeurs). Les textes ne font jamais référence à un
modèle externe : le caractère vérifiable des conseils est la priorité.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

# Ordre de gravité, du plus urgent au plus informatif. Utilisé pour le tri
# stable des conseils (gravité puis identifiant de règle).
GRAVITES = ('critical', 'warning', 'info')
_RANG_GRAVITE = {gravite: rang for rang, gravite in enumerate(GRAVITES)}

# Seuils de déclenchement des règles : constantes de la règle, jamais des
# mesures du projet. Ils n'apparaissent volontairement dans aucune preuve.
SEUIL_SUCCESSEURS = 2
SEUIL_DENSITE = 1.5


def _propre(valeur):
    """Ramène un résidu numérique entier (ex. 7.0) à un entier (7)."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return valeur
    try:
        return int(valeur) if math.isfinite(float(valeur)) and float(valeur).is_integer() else valeur
    except (OverflowError, ValueError):
        return valeur


def _nombre(valeur):
    """Formate une valeur calculée sans virgule superflue."""
    valeur = _propre(valeur)
    if isinstance(valeur, float) and math.isfinite(valeur):
        return ('%.10f' % valeur).rstrip('0').rstrip('.').replace('.', ',')
    return str(valeur)


def _marge(resultat, cle):
    """Lit une marge calculée (avec repli sur l'alias historique ``marge``)."""
    if not isinstance(resultat, Mapping):
        return 0
    valeur = resultat.get(cle)
    if valeur is None and cle == 'marge_totale':
        valeur = resultat.get('marge')
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return 0
    try:
        if not math.isfinite(float(valeur)):
            return 0
    except (OverflowError, ValueError):
        return 0
    return _propre(valeur)


def _successeurs(successeurs, code):
    """Liste des successeurs calculés par le moteur pour une tâche."""
    if not isinstance(successeurs, Mapping):
        return []
    bruts = successeurs.get(code) or ()
    if isinstance(bruts, (str, bytes)) or not isinstance(bruts, (list, tuple, set)):
        return []
    return [str(element) for element in bruts]


def _duree_source(infos):
    """Durée source d'une tâche, ou ``None`` si elle est absente/invalide."""
    if not isinstance(infos, Mapping):
        return None
    valeur = infos.get('duree')
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return None
    try:
        return _propre(valeur) if math.isfinite(float(valeur)) else None
    except (OverflowError, ValueError):
        return None


def _liste(codes):
    """Énumération lisible de codes de tâches (données sources, pas des mesures)."""
    return ', '.join(str(code) for code in codes)


def _preuve(mesures):
    """Construit une preuve textuelle à partir de couples (libellé, valeur).

    Toutes les valeurs passées ici sont issues du résultat PERT/CPM ou d'un
    décompte de ses structures ; aucune n'est inventée par le conseiller.
    """
    return ' ; '.join(
        f'{libelle} = {_nombre(valeur)}' for libelle, valeur in mesures
    )


def _conseil(id_regle, titre, gravite, explication, preuve, action):
    """Construit un conseil conforme au contrat (six clés exactement)."""
    return {
        'id_regle': id_regle,
        'titre': titre,
        'gravite': gravite,
        'explication': explication,
        'preuve': preuve,
        'action': action,
    }


def generer_conseils(resultat_pert, taches):
    """Retourne les conseils déterministes d'un planning déjà calculé.

    Args:
        resultat_pert: :class:`logic.pert.PERTResult` issu de ``compute``.
        taches: graphe ``{code: {'duree': ..., 'depend_de': [...]}}``.

    Returns:
        Une liste de dictionnaires ``{id_regle, titre, gravite, explication,
        preuve, action}``, triée par gravité puis par identifiant de règle.
        Une liste vide si le résultat est absent ou si le graphe est vide.
    """
    if resultat_pert is None:
        return []

    taches = taches if isinstance(taches, Mapping) else {}
    resultats = getattr(resultat_pert, 'resultats', None) or {}
    successeurs = getattr(resultat_pert, 'successeurs', None) or {}
    chemins = getattr(resultat_pert, 'chemins_critiques', None) or []
    if not isinstance(resultats, Mapping):
        resultats = {}
    if not isinstance(successeurs, Mapping):
        successeurs = {}
    if not isinstance(chemins, (list, tuple)):
        chemins = []

    duree_totale = _propre(getattr(resultat_pert, 'duree_totale', 0) or 0)
    nb_taches = len(resultats) or len(taches)
    nb_chemins = len(chemins)
    nb_critiques = sum(
        1 for resultat in resultats.values()
        if isinstance(resultat, Mapping) and resultat.get('critique')
    )
    nb_liens = sum(len(_successeurs(successeurs, code)) for code in resultats)

    # Mesures dérivées des structures du moteur, réutilisées par plusieurs règles.
    codes = [str(code) for code in resultats]
    cibles = {successeur for code in codes
              for successeur in _successeurs(successeurs, code)}
    sources = sorted(code for code in codes if code not in cibles)

    jalons_terminaux = sorted(
        code for code in codes
        if not _successeurs(successeurs, code)
        and _duree_source(taches.get(code)) == 0
    )

    flottantes = sorted(
        code for code in codes
        if _marge(resultats.get(code), 'marge_libre') == 0
        and _marge(resultats.get(code), 'marge_totale') > 0
    )

    conseils = []

    # R01 — Chemin critique unique, sans marge de sécurité.
    if nb_chemins == 1 and nb_critiques and duree_totale > 0:
        chemin = chemins[0]
        marge_chemin = max(
            (_marge(resultats.get(code), 'marge_totale') for code in chemin),
            default=0,
        )
        conseils.append(_conseil(
            'R01_CHEMIN_CRITIQUE_UNIQUE',
            'Chemin critique unique sans marge de sécurité',
            'warning',
            "Le moteur ne détecte qu'un seul chemin critique ("
            + ' → '.join(str(code) for code in chemin)
            + "). Les tâches qui le composent n'ont aucune marge totale : "
              "tout retard sur l'une d'elles décale directement la fin calculée.",
            _preuve([
                ('durée totale', duree_totale),
                ('tâches critiques', nb_critiques),
                ('chemins critiques', nb_chemins),
                ('marge totale du chemin', marge_chemin),
            ]),
            "Surveiller en priorité les tâches de ce chemin et prévoir une marge "
            "de sécurité avant de s'engager sur une date de livraison.",
        ))

    # R02 — Plusieurs chemins critiques de même durée.
    if nb_chemins > 1:
        conseils.append(_conseil(
            'R02_CHEMINS_CRITIQUES_MULTIPLES',
            'Plusieurs chemins critiques',
            'critical',
            "Plusieurs enchaînements atteignent la même durée totale ("
            + ' | '.join(' → '.join(str(code) for code in chemin)
                         for chemin in chemins)
            + ") : chacun peut devenir le facteur limitant en cas de retard.",
            _preuve([
                ('chemins critiques', nb_chemins),
                ('tâches critiques', nb_critiques),
                ('durée totale', duree_totale),
            ]),
            "Traiter chaque chemin comme critique : répartir la surveillance "
            "et éviter de concentrer les ressources sur un seul enchaînement.",
        ))

    # R03 — Tâche critique avec plusieurs successeurs directs.
    critiques_bloquantes = sorted(
        code for code in codes
        if isinstance(resultats.get(code), Mapping)
        and resultats[code].get('critique')
        and len(_successeurs(successeurs, code)) >= SEUIL_SUCCESSEURS
    )
    if critiques_bloquantes:
        successeurs_max = max(
            len(_successeurs(successeurs, code)) for code in critiques_bloquantes
        )
        conseils.append(_conseil(
            'R03_TACHE_CRITIQUE_MULTI_SUCCESSEURS',
            'Tâche critique avec plusieurs successeurs',
            'critical',
            "Les tâches critiques " + _liste(critiques_bloquantes)
            + " précèdent directement plusieurs branches : leur achèvement "
              "conditionne le démarrage d'autres activités du planning.",
            _preuve([
                ('tâches critiques', nb_critiques),
                ('tâches bloquantes', len(critiques_bloquantes)),
                ('successeurs maximum', successeurs_max),
                ('durée totale', duree_totale),
            ]),
            "Sécuriser ces tâches en priorité et vérifier le séquencement de "
            "leurs successeurs avant toute modification de durée.",
        ))

    # R04 — Forte densité de dépendances.
    densite = (nb_liens / nb_taches) if nb_taches else 0
    if nb_taches and densite >= SEUIL_DENSITE:
        conseils.append(_conseil(
            'R04_FORTE_DENSITE_DEPENDANCES',
            'Forte densité de dépendances',
            'warning',
            f"Le graphe contient {_nombre(nb_liens)} liens de dépendance pour "
            f"{_nombre(nb_taches)} tâches, soit {_nombre(round(densite, 2))} "
            "lien(s) par tâche : les enchaînements sont nombreux et une "
            "modification locale peut se propager loin.",
            _preuve([
                ('liens de dépendance', nb_liens),
                ('tâches', nb_taches),
                ('durée totale', duree_totale),
            ]),
            "Documenter les dépendances et vérifier les enchaînements avant "
            "de réduire une durée ou de réaffecter une ressource.",
        ))

    # R05 — Jalons en fin de chaîne (durée nulle, aucun successeur).
    if jalons_terminaux:
        conseils.append(_conseil(
            'R05_JALONS_FIN_CHAINE',
            'Jalons en fin de chaîne',
            'info',
            "Les tâches " + _liste(jalons_terminaux)
            + " sont des jalons terminaux : leur durée est nulle et aucun "
              "successeur n'est calculé après elles.",
            _preuve([
                ('jalons terminaux', len(jalons_terminaux)),
                ('tâches', nb_taches),
                ('durée totale', duree_totale),
            ]),
            "Vérifier que ces jalons matérialisent bien une fin de phase et "
            "qu'ils sont reliés aux livrables attendus.",
        ))

    # R06 — Marge libre nulle mais marge totale positive.
    if flottantes:
        marge_totale_max = max(
            _marge(resultats.get(code), 'marge_totale') for code in flottantes
        )
        conseils.append(_conseil(
            'R06_MARGE_LIBRE_NULLE_MT_POSITIVE',
            'Marge libre nulle et marge totale positive',
            'warning',
            "Les tâches " + _liste(flottantes)
            + " démarrent dès la fin de leur prédécesseur (marge libre nulle) "
              "tout en disposant d'une marge totale : leur souplesse dépend "
              "du décalage de la chaîne, pas d'un démarrage différé.",
            _preuve([
                ('tâches concernées', len(flottantes)),
                ('marge libre', 0),
                ('marge totale maximale', marge_totale_max),
                ('durée totale', duree_totale),
            ]),
            "Ne pas consommer leur marge totale sans vérifier les tâches "
            "en aval ; privilégier une action sur le chemin critique.",
        ))

    # R07 — Plusieurs tâches sans prédécesseur (démarrages indépendants).
    if len(sources) > 1:
        conseils.append(_conseil(
            'R07_SOURCES_MULTIPLES',
            'Plusieurs démarrages indépendants',
            'info',
            "Les tâches " + _liste(sources)
            + " n'ont aucun prédécesseur : le planning comporte plusieurs "
              "points de démarrage qui convergent ensuite.",
            _preuve([
                ('tâches sans prédécesseur', len(sources)),
                ('tâches', nb_taches),
                ('durée totale', duree_totale),
            ]),
            "Vérifier que chaque démarrage indépendant dispose bien de ses "
            "propres ressources et jalons de contrôle.",
        ))

    # R08 — Durée totale nulle (planning composé de jalons).
    if nb_taches and duree_totale == 0:
        conseils.append(_conseil(
            'R08_DUREE_TOTALE_NULLE',
            'Durée totale nulle',
            'info',
            "Toutes les tâches terminales ont une date de fin égale à leur "
            "date de début : le calcul ne fait apparaître aucune durée.",
            _preuve([
                ('durée totale', duree_totale),
                ('tâches', nb_taches),
                ('jalons terminaux', len(jalons_terminaux)),
            ]),
            "Vérifier les durées saisies : un planning de jalons peut être "
            "valide, mais il ne dimensionne pas la charge de travail.",
        ))

    conseils.sort(key=lambda conseil: (_RANG_GRAVITE.get(conseil['gravite'], 99),
                                       conseil['id_regle']))
    return conseils
