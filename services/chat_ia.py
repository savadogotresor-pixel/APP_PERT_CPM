"""Espace de discussion libre — moteur conversationnel maison, sans service externe.

Ce module est le pendant conversationnel de :mod:`services.conseiller`. Il ne
consulte aucun réseau, ne lit aucune clé et n'invente aucun chiffre : chaque
nombre mis dans une réponse provient exclusivement du :class:`PERTResult`
calculé par :func:`logic.pert.compute` et du graphe de tâches transmis.

Contrat public ::

    repondre(message, taches, resultat, projet=None, historique=None)
        -> {'texte': str, 'intention': str, 'sources': list[str]}

``sources`` est la trace vérifiable de la réponse : chaque libellé contient le
nombre réellement utilisé, tel qu'il a été lu dans le résultat PERT/CPM. Les
intentions sans chiffre (salutation, aide, remerciement, hors cadre) renvoient
une liste vide plutôt qu'une source factice.

L'historique est fourni par le serveur (session Flask), jamais par le client.
Il est ici re-borné une seconde fois : 20 tours maximum, 1000 caractères par
message, afin qu'un appel direct au service ne puisse pas contourner la borne.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping

from services.conseiller import generer_conseils

# Bornes contractuelles : historique de session et longueur d'un message.
HISTORIQUE_MAX = 20
MESSAGE_MAX = 1000

# Toutes les intentions que le moteur sait produire. L'ordre n'est pas celui
# de la détection (qui est plus spécifique) mais sert de référence aux tests.
INTENTIONS = (
    'salutation',
    'chemin_critique',
    'duree_totale',
    'marges',
    'tache_precise',
    'optimisation',
    'dependances',
    'risques',
    'explication_pert',
    'aide',
    'projet',
    'scenario_date',
    'remerciement',
    'hors_cadre',
    'tache_inconnue',
    'sans_taches',
)

_SALUTATION_SEULE = re.compile(
    r'^(?:bonjour|bonsoir|salut|coucou|hello|hey|bonne journee|bonsoir a tous)'
    r'[\s!?.]*$'
)
_REMERCIEMENT_SEUL = re.compile(
    r'^(?:merci|merci beaucoup|grand merci|super merci|nickel|parfait|top)'
    r'[\s!?.]*$'
)
_REPRISE = re.compile(
    r'^(?:et\b|alors\b|pourquoi\b|comment\b|explique\b|precise\b|encore\b|'
    r'la suite\b|du coup\b|maintenant\b|idem\b|pareil\b|plus de details\b)'
)
_CODE_EXPLICITE_RE = re.compile(
    r"(?:tache|taches|activite|activites)\s+[\"«']?([A-Za-z0-9_-]{1,30})",
    re.IGNORECASE,
)
_CODE_MAJUSCULE_RE = re.compile(r'\b[A-Z][A-Z0-9_-]{0,19}\b')
_CODES_RESERVES = {
    'IA', 'PERT', 'CPM', 'UI', 'UX', 'ES', 'EF', 'LS', 'LF', 'MT', 'ML',
    'API', 'CSV', 'JSON', 'PDF', 'ISO', 'WBS', 'RACM', 'SMART',
}
_MOTS_COMMUNS = {
    'est', 'la', 'le', 'les', 'de', 'du', 'des', 'critique', 'critiques',
    'marge', 'marges', 'duree', 'durees', 'suivante', 'precedente', 'qui',
    'que', 'avec', 'sans', 'et', 'ou', 'en', 'pour', 'dans', 'sur', 'son',
    'ses', 'un', 'une', 'plus', 'moins', 'bien', 'exactement', 'alors',
    'ainsi', 'aussi', 'principale', 'seconde', 'premiere', 'finale', 'initiale',
}


def _normalise(texte) -> str:
    """Compare les intentions sans tenir compte des accents ni de la casse."""
    chaine = unicodedata.normalize('NFD', str(texte or '').lower())
    return ''.join(c for c in chaine if unicodedata.category(c) != 'Mn')


def _propre(valeur):
    """Ramène un résidu numérique entier (7.0) à un entier (7)."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return 0
    try:
        if not math.isfinite(float(valeur)):
            return 0
        return int(valeur) if float(valeur).is_integer() else valeur
    except (OverflowError, ValueError):
        return 0


def _nombre(valeur) -> str:
    """Formate une valeur calculée sans virgule superflue."""
    valeur = _propre(valeur)
    if isinstance(valeur, float):
        return ('%.10f' % valeur).rstrip('0').rstrip('.').replace('.', ',')
    return str(valeur)


def _unite(valeur) -> str:
    return 'jour' if _propre(valeur) == 1 else 'jours'


def _mapping(valeur) -> dict:
    return valeur if isinstance(valeur, Mapping) else {}


def _taches_propres(taches) -> dict:
    return _mapping(taches)


def _resultats(resultat) -> dict:
    return _mapping(getattr(resultat, 'resultats', None))


def _duree_totale(resultat):
    return _propre(getattr(resultat, 'duree_totale', 0) or 0)


def _chemins(resultat) -> list:
    brut = getattr(resultat, 'chemins_critiques', None) or []
    if not isinstance(brut, (list, tuple)):
        return []
    return [list(chemin) for chemin in brut
            if isinstance(chemin, (list, tuple))]


def _successeurs(resultat) -> dict:
    brut = getattr(resultat, 'successeurs', None)
    if not isinstance(brut, Mapping):
        return {}
    return {
        str(code): [str(s) for s in (succs or ())]
        for code, succs in brut.items()
        if isinstance(succs, (list, tuple, set))
    }


def _predecesseurs(taches, code) -> list:
    infos = _mapping(taches.get(code))
    brut = infos.get('depend_de') or ()
    if isinstance(brut, str):
        brut = [brut]
    if not isinstance(brut, (list, tuple, set)):
        return []
    return [str(d) for d in brut if str(d).strip()]


def _marge(resultat_tache, cle):
    if not isinstance(resultat_tache, Mapping):
        return 0
    valeur = resultat_tache.get(cle)
    if valeur is None and cle == 'marge_totale':
        valeur = resultat_tache.get('marge')
    return _propre(valeur)


def _chemins_texte(chemins) -> str:
    if not chemins:
        return 'aucun chemin critique calculé'
    return ' | '.join(' → '.join(str(code) for code in chemin)
                      for chemin in chemins[:4]) + (
        ' | …' if len(chemins) > 4 else ''
    )


def _liste(codes) -> str:
    return ', '.join(str(code) for code in codes) if codes else 'aucune'


class _Sources:
    """Construit le texte et sa trace en une seule opération.

    ``nombre()`` est le seul endroit où un chiffre calculé est transformé en
    chaîne : le libellé enregistré reprend exactement la même valeur. Ainsi une
    réponse ne peut pas contenir de nombre qui n'ait pas sa source.
    """

    def __init__(self):
        self.sources = []

    def nombre(self, libelle, valeur, unite=' j'):
        propre = _propre(valeur)
        texte = _nombre(propre)
        self.sources.append(f'{libelle} = {texte}{unite}')
        return texte

    def fait(self, libelle):
        self.sources.append(libelle)
        return libelle

    def liste(self):
        # Dédoublonnage stable : une même mesure peut être citée deux fois.
        return list(dict.fromkeys(self.sources))


def _historique_borne(historique) -> list:
    """Ne garde que les 20 derniers tours, à 1000 caractères par message."""
    brut = historique if isinstance(historique, (list, tuple)) else []
    messages = []
    for tour in brut[-HISTORIQUE_MAX:]:
        if not isinstance(tour, Mapping):
            continue
        role = tour.get('role')
        if role not in ('user', 'assistant'):
            continue
        valeur = ''
        for cle in ('texte', 'contenu', 'content', 'text'):
            if cle in tour:
                valeur = tour.get(cle)
                break
        valeur = str(valeur or '').strip()[:MESSAGE_MAX]
        if valeur:
            messages.append({'role': role, 'texte': valeur})
    return messages[-HISTORIQUE_MAX:]


def _message_analyse(message, historique) -> str:
    """Résout les reprises (« et alors ? », « pourquoi ? ») côté serveur."""
    texte = str(message or '').strip()[:MESSAGE_MAX]
    normalise = _normalise(texte)
    if len(normalise) < 25 and _REPRISE.match(normalise):
        for tour in reversed(_historique_borne(historique)):
            if tour['role'] != 'user':
                continue
            precedent = _normalise(tour['texte'])
            if precedent and precedent != normalise:
                return precedent + ' ' + texte
            break
    return texte


def _resoudre_code(candidat, taches) -> str | None:
    """Retrouve le code exact du graphe, quelle que soit la casse saisie."""
    for code in taches:
        if str(code) == candidat:
            return str(code)
    for code in taches:
        if str(code).lower() == str(candidat).lower():
            return str(code)
    return None


def _codes_mentionnes(message, taches) -> list:
    """Codes de tâches effectivement présents dans le planning."""
    codes = []
    # La forme « la tâche B » est cherchée sur le texte sans accents : « âche »
    # ne doit pas empêcher de reconnaître le mot « tâche ».
    for trouve in _CODE_EXPLICITE_RE.finditer(_normalise(message)):
        code = _resoudre_code(trouve.group(1).strip('"\'«»'), taches)
        if code is not None and code not in codes:
            codes.append(code)
    for trouve in _CODE_MAJUSCULE_RE.finditer(str(message or '')):
        code = _resoudre_code(trouve.group(0), taches)
        if code is not None and code not in codes:
            codes.append(code)
    return codes


def _code_inconnu(message, taches) -> str | None:
    """Code explicitement attribué à une tâche mais absent du planning.

    Un simple mot français après « tâche » (« tâche critique », « tâches ont »)
    n'est pas un code : seuls un identifiant écrit en majuscules ou un terme
    entre guillemets sont considérés comme une référence explicite.
    """
    normalise = _normalise(message)
    if 'tache' not in normalise and 'activite' not in normalise:
        return None
    for trouve in _CODE_EXPLICITE_RE.finditer(normalise):
        candidat = trouve.group(1).strip('"\'«»')
        if _resoudre_code(candidat, taches) is not None:
            continue
        if candidat.lower() in _MOTS_COMMUNS:
            continue
        entre_guillemets = re.search(
            r'["«\']' + re.escape(candidat) + r'["»\']', str(message or ''), re.I
        )
        en_majuscules = re.search(
            r'\b' + re.escape(candidat.upper()) + r'\b', str(message or '')
        )
        if entre_guillemets or en_majuscules:
            return candidat.upper()
    return None


def _contient(question, *fragments) -> bool:
    return any(fragment in question for fragment in fragments)


def _detecter(message, taches, projet) -> str:
    """Associe un message libre à une intention, sans jamais consulter le réseau.

    L'ordre va du plus précis au plus général : une question sur une tâche
    nommée est traitée avant une question globale sur les marges ou le chemin
    critique, et une salutation pure avant le reste.
    """
    question = _normalise(message)
    code_connu = bool(_codes_mentionnes(message, taches))

    if _code_inconnu(message, taches):
        return 'tache_inconnue'
    if _SALUTATION_SEULE.match(question):
        return 'salutation'
    if _REMERCIEMENT_SEUL.match(question):
        return 'remerciement'

    if code_connu and _contient(
        question, 'critique', 'marge', 'duree', 'retard', 'bloc',
        'predecesseur', 'successeur', 'depend', 'date', 'es', 'ef', 'ls', 'lf',
    ):
        return 'tache_precise'
    if _contient(question, 'aide', 'que peux-tu', 'qu peux-tu', 'que sais-tu',
                 'quelles questions', 'comment utiliser', 'help',
                 'presente-toi', 'présente-toi', 'comment ca marche'):
        return 'aide'
    if _contient(question, 'pert', 'cpm', 'explique', 'qu est ce que',
                 'qu est-ce', 'comment calcule', 'comment est calculee',
                 'comment fonctionne', 'early start', 'late start'):
        return 'explication_pert'
    if _contient(question, 'date cible', 'date de fin souhaitee', 'deadline',
                 'echeance', 'scenario', 'simulation', 'et si', 'hypothese',
                 'avant le', 'finir le', 'livrer le', 'retard de'):
        return 'scenario_date'
    if _contient(question, 'raccourcir', 'optimiser', 'optimisation', 'reduire',
                 'accelerer', 'gagner du temps', 'finir plus tot', 'conseil',
                 'ameliorer', 'racourcir', 'desengorger', 'soulager'):
        return 'optimisation'
    if _contient(question, 'dependance', 'depend', 'predecesseur', 'successeur',
                 'bloque', 'enchainement', 'parallele', 'simultan',
                 'en meme temps', 'vient avant', 'vient apres', 'precede'):
        return 'dependances'
    if _contient(question, 'risque', 'retard', 'derapage', 'alerte', 'fragile',
                 'probleme', 'incertitude', 'peur', 'danger'):
        return 'risques'
    if _contient(question, 'marge', 'flottement', 'flotte', 'souplesse',
                 'latitude', 'glisser'):
        return 'marges'
    if _contient(question, 'chemin critique', 'chemin', 'critique',
                 'critical path'):
        return 'chemin_critique'
    if _contient(question, 'duree', 'combien de temps', 'combien de jours',
                 'delai', 'temps', 'quand', 'jour', 'fin du projet', 'terme'):
        return 'duree_totale'
    if _contient(question, 'projet', 'nom', 'description', 'parle-moi',
                 'parle moi', 'presente', 'combien de taches', 'taches'):
        return 'projet'
    if _contient(question, 'bonjour', 'bonsoir', 'salut', 'coucou', 'hello', 'hey'):
        return 'salutation'
    if _contient(question, 'merci', 'nickel', 'parfait'):
        return 'remerciement'
    return 'hors_cadre'


def _reponse_sans_taches(intention) -> dict:
    return {
        'texte': (
            "Ce projet ne contient pas encore de tâches : je ne peux donc "
            "calculer ni durée totale, ni chemin critique, ni marge, ni "
            "dépendance. Ajoutez d'abord au moins une tâche avec une durée, "
            "puis reposez-moi votre question."
        ),
        'intention': intention if intention in ('salutation', 'remerciement') else 'sans_taches',
        'sources': [],
    }


def _finaliser(texte, intention, sources) -> dict:
    """Construit le contrat public, en garantissant un texte non vide."""
    return {
        'texte': str(texte).strip(),
        'intention': intention,
        'sources': list(sources),
    }


def _reponse_intention(intention, message, taches, resultat, projet, sources):
    """Délègue la réponse à la branche correspondant à l'intention détectée."""
    resultats = _resultats(resultat)
    chemins = _chemins(resultat)
    codes = list(taches)
    critiques = [code for code in codes if resultats.get(code, {}).get('critique')]
    duree = _duree_totale(resultat)

    if intention == 'salutation':
        return _finaliser(
            "Bonjour ! Je suis l'assistant intégré de ce planning PERT/CPM. "
            "Je réponds uniquement à partir des calculs du moteur : chemin "
            "critique, durée totale, marges, dépendances et conseils "
            "d'optimisation. Que souhaitez-vous savoir ?",
            intention, [],
        )

    if intention == 'remerciement':
        return _finaliser(
            "Avec plaisir ! Si vous voulez creuser un point (marges, "
            "dépendances, optimisation), posez-moi une autre question.",
            intention, [],
        )

    if intention == 'aide':
        duree_texte = sources.nombre('durée totale', duree)
        chemin_texte = sources.nombre('chemins critiques', len(chemins), '')
        return _finaliser(
            "Je peux vous expliquer, chiffres à l'appui : la durée totale "
            f"({duree_texte} {_unite(duree)}), les {chemin_texte} chemin(s) "
            "critique(s), la criticité et les marges de chaque tâche, les "
            "dépendances du graphe et les conseils d'optimisation calculés "
            "par le moteur. Par exemple : « Quel est le chemin critique ? » "
            "ou « Quelles tâches ont le plus de marge ? ». Je suis un outil "
            "local : pas de service externe, pas de quota. Limites : aucune "
            "date de début absolue et aucune contrainte de ressources.",
            intention, sources.liste(),
        )

    if intention == 'explication_pert':
        duree_texte = sources.nombre('durée totale', duree)
        marge_critique = sources.nombre('marge totale du chemin critique', 0)
        chemin_texte = _chemins_texte(chemins)
        sources.fait(f'chemin critique = {chemin_texte}')
        return _finaliser(
            "Le PERT/CPM calcule pour chaque tâche des dates au plus tôt "
            "(ES/EF) et au plus tard (LS/LF), puis une marge totale (LS − ES). "
            f"Une tâche est critique quand cette marge vaut {marge_critique} "
            f"{_unite(0)}. Ici la durée totale calculée est de {duree_texte} "
            f"{_unite(duree)} et le chemin critique est {chemin_texte}. "
            "ES/EF/LS/LF sont des jours relatifs : aucune date de début "
            "absolue n'est fournie par le moteur.",
            intention, sources.liste(),
        )

    if intention == 'duree_totale':
        duree_texte = sources.nombre('durée totale', duree)
        nb_chemins = sources.nombre('chemins critiques', len(chemins), '')
        return _finaliser(
            f"La durée totale calculée par le moteur PERT/CPM est de "
            f"{duree_texte} {_unite(duree)} calendaires. Le moteur détecte "
            f"{nb_chemins} chemin(s) critique(s) : {_chemins_texte(chemins)}. "
            "Ce résultat est issu du calcul ; aucune estimation ni date de "
            "début absolue n'est ajoutée.",
            intention, sources.liste(),
        )

    if intention == 'chemin_critique':
        nb_chemins = sources.nombre('chemins critiques', len(chemins), '')
        nb_critiques = sources.nombre('tâches critiques', len(critiques), '')
        duree_texte = sources.nombre('durée totale', duree)
        marge_texte = sources.nombre(
            'marge totale de ' + (critiques[0] if critiques else 'la tâche critique'), 0
        )
        return _finaliser(
            f"Le moteur détecte {nb_chemins} chemin(s) critique(s) : "
            f"{_chemins_texte(chemins)}. Il y a {nb_critiques} tâche(s) "
            f"critique(s) : {_liste(critiques)} ; leur marge totale calculée "
            f"est de {marge_texte} {_unite(0)}. Tout retard sur l'une d'elles "
            f"décale la fin calculée, estimée à {duree_texte} {_unite(duree)}.",
            intention, sources.liste(),
        )

    if intention == 'marges':
        non_critiques = sorted(
            ((code, _marge(resultats.get(code), 'marge_totale')) for code in codes
             if not resultats.get(code, {}).get('critique')),
            key=lambda element: (-element[1], str(element[0])),
        )
        morceaux = []
        for code, marge in non_critiques[:4]:
            mt = sources.nombre(f'marge totale de {code}', marge)
            ml = sources.nombre(f'marge libre de {code}',
                                _marge(resultats.get(code), 'marge_libre'))
            morceaux.append(f"{code} (totale {mt} {_unite(marge)}, libre {ml} {_unite(_marge(resultats.get(code), 'marge_libre'))})")
        marge_critique = sources.nombre('marge totale des tâches critiques', 0)
        if morceaux:
            detail = ('Les tâches qui disposent de la plus grande marge totale '
                      'sont : ' + ', '.join(morceaux) + '.')
        else:
            detail = 'Aucune tâche non critique ne dispose de marge calculée.'
        return _finaliser(
            "Marges calculées par le moteur PERT/CPM : " + detail
            + f" Les tâches critiques ont une marge totale de {marge_critique} "
              f"{_unite(0)}. La marge libre mesure le glissement possible sans "
              "décaler le démarrage d'un successeur.",
            intention, sources.liste(),
        )

    if intention == 'dependances':
        succ = _successeurs(resultat)
        nb_liens = sources.nombre('liens de dépendance',
                                  sum(len(succ.get(code, ())) for code in codes), '')
        paires = []
        for code in codes:
            for suivant in succ.get(code, ())[:3]:
                paires.append(f'{code} → {suivant}')
        sources.fait('dépendances calculées du graphe')
        return _finaliser(
            f"Le graphe contient {nb_liens} lien(s) de dépendance calculé(s). "
            "Enchaînements visibles : " + (_liste(paires) if paires else 'aucun')
            + ". Les tâches sans prédécesseur sont : "
            + _liste([code for code in codes if not _predecesseurs(taches, code)])
            + ". « En parallèle » signifie seulement qu'aucune dépendance "
              "calculée ne relie deux tâches : le moteur ne modélise ni les "
              "ressources ni les disponibilités.",
            intention, sources.liste(),
        )

    if intention == 'tache_precise':
        codes_mentionnes = _codes_mentionnes(message, taches)
        code = codes_mentionnes[0] if codes_mentionnes else codes[0]
        infos = _mapping(taches.get(code))
        succ = _successeurs(resultat)
        r = _mapping(resultats.get(code))
        critique = bool(r.get('critique'))
        marge_totale = _marge(r, 'marge_totale')
        marge_libre = _marge(r, 'marge_libre')
        duree_tache_brute = _propre(infos.get('duree', 0) or 0)
        mt = sources.nombre(f'marge totale de {code}', marge_totale)
        ml = sources.nombre(f'marge libre de {code}', marge_libre)
        duree_tache = sources.nombre(f'duree de {code}', duree_tache_brute)
        dates = []
        for cle in ('ES', 'EF', 'LS', 'LF'):
            dates.append(
                f"{cle} = {sources.nombre(cle + ' de ' + code, r.get(cle, 0))}"
            )
        etat = 'est critique' if critique else "n'est pas critique"
        return _finaliser(
            f"La tâche {code} {etat} : sa marge totale calculée est de "
            f"{mt} {_unite(marge_totale)} et sa marge libre de "
            f"{ml} {_unite(marge_libre)}. Ses dates calculées "
            f"(jours relatifs) sont {', '.join(dates)} ; sa durée saisie est "
            f"de {duree_tache} {_unite(duree_tache_brute)}. "
            f"Prédécesseurs : {_liste(_predecesseurs(taches, code))} ; "
            f"successeurs : {_liste(succ.get(code, ()))}.",
            intention, sources.liste(),
        )

    if intention == 'optimisation':
        duree_texte = sources.nombre('durée totale', duree)
        nb_critiques = sources.nombre('tâches critiques', len(critiques), '')
        actions = generer_conseils(resultat, taches)
        lignes = [f"{conseil['titre']} : {conseil['action']}" for conseil in actions[:6]]
        sources.fait('actions suggérées par le conseiller IA maison')
        if lignes:
            pistes = '\n'.join('- ' + ligne for ligne in lignes)
            corps = (
                f"D'après la durée totale calculée de {duree_texte} "
                f"{_unite(duree)} et {nb_critiques} tâche(s) critique(s), "
                "le conseiller maison suggère :\n" + pistes
            )
        else:
            corps = (
                f"Le conseiller ne dégage pas d'action supplémentaire pour ce "
                f"planning ; la durée totale calculée est de {duree_texte} "
                f"{_unite(duree)}."
            )
        return _finaliser(
            corps + "\nCe sont des recommandations vérifiables, pas des "
            "résultats : je ne promets aucun gain de temps chiffré sans "
            "recalculer un scénario.",
            intention, sources.liste(),
        )

    if intention == 'risques':
        nb_critiques = sources.nombre('tâches critiques', len(critiques), '')
        duree_texte = sources.nombre('durée totale', duree)
        marge_texte = sources.nombre(
            'marge totale de ' + (critiques[0] if critiques else 'la tâche critique'), 0
        )
        faibles = sorted(
            ((code, _marge(resultats.get(code), 'marge_totale')) for code in codes
             if not resultats.get(code, {}).get('critique')
             and _marge(resultats.get(code), 'marge_totale') > 0),
            key=lambda element: (element[1], str(element[0])),
        )
        detail = ''
        if faibles:
            code, marge = faibles[0]
            mt = sources.nombre(f'marge totale de {code}', marge)
            detail = (f" La marge positive la plus faible est celle de {code} "
                      f"({mt} {_unite(marge)}).")
        return _finaliser(
            "Points de vigilance calculés par le moteur : "
            f"{nb_critiques} tâche(s) critique(s) ({_liste(critiques)}) avec "
            f"une marge totale de {marge_texte} {_unite(0)} ; la fin calculée "
            f"est à {duree_texte} {_unite(duree)}." + detail
            + " Le moteur ne fournit aucune probabilité de retard : ces "
              "éléments sont des faits calculés, pas une prévision chiffrée.",
            intention, sources.liste(),
        )

    if intention == 'scenario_date':
        duree_texte = sources.nombre('durée totale', duree)
        return _finaliser(
            "Le moteur exprime ses résultats en jours relatifs : la durée "
            f"totale calculée est de {duree_texte} {_unite(duree)}. Aucune "
            "date de fin absolue ni résultat de scénario n'est disponible "
            "dans ce calcul ; je ne peux donc pas vous donner de date de "
            "livraison chiffrée. La page Optimisation permet de simuler une "
            "date cible à partir du planning réel.",
            intention, sources.liste(),
        )

    if intention == 'projet':
        nb_taches = sources.nombre('tâches', len(taches), '')
        nb_chemins = sources.nombre('chemins critiques', len(chemins), '')
        duree_texte = sources.nombre('durée totale', duree)
        nom = ''
        description = ''
        if isinstance(projet, Mapping):
            brut_nom = str(projet.get('name') or projet.get('nom') or '').strip()
            brut_description = str(projet.get('description') or '').strip()
            # Un texte saisi peut contenir des chiffres qui ne viennent pas du
            # moteur : on ne le reproduit pas pour préserver la règle absolue.
            nom = brut_nom if brut_nom and not re.search(r'\d', brut_nom) else ''
            description = (
                brut_description
                if brut_description and not re.search(r'\d', brut_description)
                else ''
            )
        presentation = 'Le projet sélectionné'
        if nom or description:
            sources.fait('nom et description du projet')
        if nom:
            presentation += f" est « {nom} »"
        if description:
            presentation += f". Description déclarée : {description}"
        return _finaliser(
            presentation + f". Son planning contient {nb_taches} tâche(s), "
            f"pour une durée totale calculée de {duree_texte} {_unite(duree)} "
            f"et {nb_chemins} chemin(s) critique(s).",
            intention, sources.liste(),
        )

    if intention == 'tache_inconnue':
        code = _code_inconnu(message, taches)
        sources.fait('codes des tâches du planning')
        return _finaliser(
            f"Je ne trouve pas la tâche {code} dans ce planning : je ne peux "
            "donc rien calculer pour elle. Les tâches connues sont : "
            + _liste(codes) + ".",
            intention, sources.liste(),
        )

    # Hors cadre : réponse polie, sans chiffre, qui oriente vers le planning.
    return _finaliser(
        "Je suis l'assistant de ce planning PERT/CPM : je ne réponds qu'aux "
        "questions sur les tâches, le chemin critique, les durées, les marges, "
        "les dépendances et les conseils d'optimisation du projet sélectionné. "
        "Par exemple : « Quel est le chemin critique ? ».",
        'hors_cadre', [],
    )


def repondre(message, taches, resultat, projet=None, historique=None) -> dict:
    """Répond en français au message libre, uniquement à partir des calculs.

    Args:
        message: texte saisi par l'utilisateur (au plus ``MESSAGE_MAX`` caractères).
        taches: graphe ``{code: {'duree': ..., 'depend_de': [...]}}``.
        resultat: :class:`logic.pert.PERTResult` issu de ``compute(taches)``.
        projet: mapping facultatif avec le nom et la description (données
            déclaratives, jamais une source de chiffres).
        historique: tours de conversation fournis par le serveur ; rebornés ici.

    Returns:
        ``{'texte': str, 'intention': str, 'sources': list[str]}``. Ne lève
        jamais d'exception pour une question hors cadre ou un projet vide.
    """
    taches = _taches_propres(taches)
    texte = str(message or '').strip()[:MESSAGE_MAX]
    messages = _historique_borne(historique)

    if not texte:
        return _finaliser(
            "Votre message est vide : écrivez une question sur le planning "
            "(par exemple « Quel est le chemin critique ? »).",
            'hors_cadre', [],
        )

    analyse = _message_analyse(texte, messages)
    intention = _detecter(analyse, taches, projet)
    if not taches:
        return _reponse_sans_taches(intention)
    return _reponse_intention(intention, analyse, taches, resultat, projet, _Sources())
