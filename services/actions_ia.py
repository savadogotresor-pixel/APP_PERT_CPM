"""Actions contrôlées en langage naturel (lot IA-4, cahier §19.6).

Ce module ne fait que COMPRENDRE et VALIDER des propositions : aucune écriture,
aucun appel réseau, aucune décision d'accès. L'exécution et la journalisation
appartiennent aux routes, qui doivent appliquer les étapes du cahier :

1. compréhension (:func:`preparer_action`) ;
2. préparation lisible (champ ``changements``) ;
3. validation serveur (:func:`valider_action`) ;
4. confirmation explicite (``CONFIRMER`` / ``SUPPRIMER``) ;
5. exécution des seules opérations autorisées et confirmées ;
6. recalcul par :func:`logic.pert.compute` ;
7. traçabilité dans la route.

Les données transmises (texte, tâches, résultats PERT) sont NON FIABLES : seules
les formulations explicitement reconnues produisent une action, et toute valeur
numérique proposée provient du texte ou des tâches fournis. Aucune durée n'est
inventée : le cas échéant, la valeur par défaut est signalée comme hypothèse.
"""

from __future__ import annotations

import copy
import math
import re
import unicodedata
from collections.abc import Mapping

from logic.pert import ValidationError, compute

TYPES_ACTIONS = (
    "modifier_duree",
    "ajouter_tache",
    "supprimer_tache",
    "ajouter_predecesseur",
    "inconnu",
)

CODE = r"[A-Za-z0-9_][A-Za-z0-9_-]{0,29}"

MOTS_NOMBRES = {
    "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4,
    "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
    "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15,
    "seize": 16, "vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50,
}
NOMBRE = r"(?:\d+|" + "|".join(
    sorted(MOTS_NOMBRES, key=len, reverse=True)
) + r")"

# Mots qui ne peuvent pas être des codes de tâche lorsqu'ils suivent un nom.
STOPWORDS = {
    "a", "au", "aux", "avant", "avec", "apres", "ce", "ces", "code", "comme",
    "d", "dans", "de", "des", "du", "duree", "elle", "en", "et", "hors",
    "jour", "jours", "la", "le", "les", "libellee", "nommee", "nouveau",
    "nouvelle", "phase", "pour", "puis", "sans", "sur", "tache", "taches",
    "activite", "activites", "appellee", "intitulee", "jalon", "projet",
}

VERBES_AUGMENTE = (
    "augmente", "augmenter", "allonge", "allonger", "prolonge", "prolonger",
    "rallonge", "rallonger", "gagne", "gagner",
)
VERBES_DIMINUE = (
    "diminue", "diminuer", "reduis", "reduire", "raccourci", "raccourcir",
    "baisse", "baisser", "retire", "retirer", "enleve", "enlever",
)
VERBES_FIXE = (
    "fixe", "fixer", "etablis", "etablir", "met", "mets", "mettre",
    "definis", "definir", "egale", "vaut",
)
VERBES_AJOUTE = (
    "ajoute", "ajouter", "cree", "creer", "genere", "generer",
    "nouvelle", "nouveau",
)
VERBES_SUPPRIME = (
    "supprime", "supprimer", "suppression", "efface", "effacer",
    "detruis", "detruire",
)

FORMULATIONS_COMPRISES = (
    "Formulations comprises : « augmente la durée de la tâche B de 2 jours » "
    "ou « durée B +2 » ; « diminue/réduis la durée de B de 2 jours » ; "
    "« fixe la durée de B à 5 jours » ; « ajoute une tâche C après B » ou "
    "« ajoute une phase de tests après B » ; « supprime la tâche X » ; "
    "« ajoute A comme prédécesseur de B » ou « ajoute un prédécesseur A à B »."
)


# ---------------------------------------------------------------------------
# Utilitaires de lecture (jamais d'écriture sur les données reçues)
# ---------------------------------------------------------------------------

def _normalise(texte) -> str:
    """Minuscules et accents retirés, sans altérer le texte d'origine."""
    chaine = str(texte or "").replace("’", "'").lower()
    chaine = unicodedata.normalize("NFD", chaine)
    return "".join(c for c in chaine if unicodedata.category(c) != "Mn")


def _contient(texte_normalise: str, mots) -> bool:
    return any(
        re.search(r"\b" + re.escape(mot) + r"\b", texte_normalise)
        for mot in mots
    )


def _nombre(brut) -> int | None:
    """Convertit un chiffre ou un mot français en entier, sinon ``None``."""
    chaine = str(brut or "").strip()
    if chaine.isdigit():
        return int(chaine)
    return MOTS_NOMBRES.get(_normalise(chaine))


def _affiche(valeur) -> str:
    """Format lisible d'une valeur calculée, sans chiffre arbitraire."""
    if isinstance(valeur, bool) or valeur is None:
        return str(valeur)
    if isinstance(valeur, float) and valeur.is_integer():
        return str(int(valeur))
    return str(valeur)


def _unite(valeur) -> str:
    return "jour" if _nombre(valeur) == 1 else "jours"


def _duree_entiere(valeur) -> bool:
    """Vrai pour un entier (ou un flottant exact), faux pour un booléen."""
    if isinstance(valeur, bool):
        return False
    if isinstance(valeur, int):
        return True
    if isinstance(valeur, float):
        return math.isfinite(valeur) and valeur.is_integer()
    return False


def _nettoye(texte) -> str:
    return str(texte or "").strip().strip("\"'«»‘’").strip()


def _resoudre_code(candidat, taches: Mapping) -> str | None:
    """Retourne le code réel du planning (insensible à la casse) ou le saisi."""
    code = _nettoye(candidat)
    if not code:
        return None
    if code in taches:
        return code
    for cle in taches:
        if str(cle).lower() == code.lower():
            return str(cle)
    return code


def _candidat_code(candidat) -> bool:
    """Filtre les mots français capturés par les expressions régulières."""
    chaine = _nettoye(candidat)
    if not chaine or not re.fullmatch(CODE, chaine):
        return False
    return chaine.lower() not in STOPWORDS and chaine.lower() not in MOTS_NOMBRES


def _est_code_explicite(candidat, taches: Mapping) -> bool:
    """Un code est explicite s'il est majuscule ou connu du planning."""
    if not _candidat_code(candidat):
        return False
    chaine = _nettoye(candidat)
    if re.fullmatch(r"[A-Z][A-Z0-9_-]*", chaine):
        return True
    return any(str(cle).lower() == chaine.lower() for cle in taches)


def _codes_dans(chunk: str, taches: Mapping) -> list[str]:
    """Codes mentionnés dans un fragment, dans leur ordre d'apparition."""
    codes: list[str] = []

    def ajouter(candidat) -> None:
        code = _resoudre_code(candidat, taches)
        if code and code not in codes:
            codes.append(code)

    for trouve in re.finditer(r"[\"«'‘’](" + CODE + r")[\"»'’]", chunk or ""):
        ajouter(trouve.group(1))
    for trouve in re.finditer(
        r"\b(?:t[aâ]che|tache|activit[eé]|activite|phase|jalon|projet)\s+("
        + CODE + r")", chunk or "", re.IGNORECASE
    ):
        if _candidat_code(trouve.group(1)):
            ajouter(trouve.group(1))
    for trouve in re.finditer(r"\b[A-Z][A-Z0-9_-]*\b", chunk or ""):
        ajouter(trouve.group(0))
    for cle in taches:
        motif = r"(?<![A-Za-z0-9_-])" + re.escape(str(cle)) + r"(?![A-Za-z0-9_-])"
        if re.search(motif, chunk or "", re.IGNORECASE):
            ajouter(cle)
    return codes


def _cible_depuis_texte(texte, taches: Mapping, projet: bool = False) -> str | None:
    """Extrait la tâche visée par une modification ou une suppression."""
    noms = r"(?:t[aâ]che|tache|activit[eé]|activite|phase|jalon"
    if projet:
        noms += r"|projet"
    noms += r")"
    motifs = (
        r"\b(?:de|sur|pour|du)\s+(?:la\s+|le\s+|les\s+|l')?" + noms
        + r"\s+[\"«]?(" + CODE + r")",
        r"\b" + noms + r"\s+[\"«]?(" + CODE + r")",
        r"\b(?:dur[eé]e|duree)\s+[\"«]?(" + CODE + r")",
    )
    for motif in motifs:
        for trouve in re.finditer(motif, str(texte or ""), re.IGNORECASE):
            if _candidat_code(trouve.group(1)):
                return _resoudre_code(trouve.group(1), taches)
    codes = _codes_dans(str(texte or ""), taches)
    return codes[0] if codes else None


def _dependants(cible: str, taches: Mapping) -> list[str]:
    """Tâches qui déclarent ``cible`` comme prédécesseur."""
    resultats = []
    for code, infos in taches.items():
        if code == cible or not isinstance(infos, Mapping):
            continue
        bruts = infos.get("depend_de") or ()
        if isinstance(bruts, str):
            bruts = [bruts]
        if isinstance(bruts, (list, tuple, set)) and cible in bruts:
            resultats.append(str(code))
    return resultats


def _est_massif(texte_normalise: str) -> bool:
    """Détecte une suppression/écrasement massif nécessitant une garde-fou."""
    marqueurs = (
        "toutes les taches", "tous les taches", "tous les projets",
        "tout le projet", "supprime tout", "supprimer tout", "ecrase",
        "ecraser", "ecrasement", "remplace toutes", "remplacer toutes",
        "toute la base",
    )
    return any(marqueur in texte_normalise for marqueur in marqueurs)


def _copie(taches: Mapping) -> dict:
    """Copie profonde : le moteur ne doit jamais voir le planning d'origine."""
    return copy.deepcopy(dict(taches))


def _resultat(
    type_action: str,
    cible: str | None,
    parametres: dict,
    changements: list[str],
    description: str,
    destructrice: bool = False,
    avertissements: list[str] | None = None,
    confiance: str = "elevee",
) -> dict:
    """Construit la structure de retour, toujours complète et sans effet."""
    return {
        "type": type_action,
        "cible": cible,
        "parametres": dict(parametres),
        "changements": list(changements),
        "description": str(description),
        "destructrice": bool(destructrice),
        "confirmation_requise": "SUPPRIMER" if destructrice else "CONFIRMER",
        "confiance": confiance,
        "avertissements": list(avertissements or []),
    }


def _inconnu(raison: str) -> dict:
    return _resultat(
        "inconnu", None, {},
        ["Aucune modification n'est envisagée : la demande n'a pas été comprise."],
        "Demande non comprise : aucune action n'est proposée.",
        avertissements=[raison, FORMULATIONS_COMPRISES],
        confiance="faible",
    )


# ---------------------------------------------------------------------------
# 1. Compréhension et 2. préparation (aperçu lisible, aucune écriture)
# ---------------------------------------------------------------------------

def _preparer_duree(texte, taches: Mapping, q: str) -> dict:
    cible = _cible_depuis_texte(texte, taches)
    absolu = _contient(q, VERBES_FIXE) or bool(
        re.search(r"\bduree\b[^.?!]{0,40}=\s*\d", q)
    )
    diminue = _contient(q, VERBES_DIMINUE) or bool(
        re.search(r"\s-\s*\d", str(texte or ""))
    ) or "moins" in q
    augmente = (
        _contient(q, VERBES_AUGMENTE)
        or "+" in str(texte or "")
        or bool(re.search(
            r"\bajoute\s+(?:des\s+)?" + NOMBRE + r"\s*jours?\s+"
            r"(?:a\s+)?(?:la\s+)?duree", q
        ))
    )
    sens = 0 if absolu else (-1 if diminue else (1 if augmente else None))
    if sens is None:
        return _inconnu("Modification de durée détectée mais sans sens explicite.")

    nombre = None
    if absolu:
        motifs = (
            r"\b(?:fixe|fixer|met|mets|mettre|etablis|etablir|definis|definir|"
            r"egale|vaut)\b[^.?!]{0,100}?\b(?:a|=)\s*(" + NOMBRE + r")\s*(?:jours?)?\b",
            r"\bduree\b[^.?!]{0,60}?=\s*(" + NOMBRE + r")\b",
            r"\b(?:a|=)\s*(" + NOMBRE + r")\s*jours?\b",
        )
    else:
        motifs = (
            r"\b(?:de|d)\s+(" + NOMBRE + r")\s*jours?\b",
            r"\+\s*(" + NOMBRE + r")",
            r"\s-\s*(" + NOMBRE + r")",
            r"\b(?:plus|moins)\s+(" + NOMBRE + r")",
            r"\b(" + NOMBRE + r")\s*jours?\b",
        )
    for motif in motifs:
        trouve = re.search(motif, q)
        if trouve:
            nombre = _nombre(trouve.group(1))
            if nombre is not None:
                break

    avertissements: list[str] = []
    nouvelle = None
    actuelle = None
    if not cible:
        avertissements.append("Aucune tâche cible n'a pu être identifiée dans la demande.")
    elif cible not in taches:
        avertissements.append(
            f'La tâche "{cible}" est introuvable dans le planning : '
            "la proposition ne peut pas être validée."
        )
    else:
        infos = taches.get(cible)
        actuelle = infos.get("duree") if isinstance(infos, Mapping) else None
        if not _duree_entiere(actuelle):
            avertissements.append(
                f'La durée actuelle de la tâche "{cible}" est indisponible : '
                "aucune nouvelle durée n'est calculable."
            )
        elif nombre is None:
            avertissements.append("Aucun nombre de jours n'a pu être identifié.")
        else:
            nouvelle = int(nombre) if absolu else int(actuelle) + sens * int(nombre)

    if nombre is not None and nouvelle is None and cible:
        avertissements.append(
            "Une valeur a été comprise, mais la durée actuelle est nécessaire "
            "pour calculer la nouvelle durée."
        )
    if nouvelle is not None and nouvelle < 0:
        avertissements.append(
            "La nouvelle durée serait négative : la validation la refusera."
        )

    if nouvelle is not None:
        changements = [
            f'La durée de la tâche "{cible}" passe de {_affiche(actuelle)} '
            f'à {_affiche(nouvelle)} {_unite(nouvelle)}.'
        ]
        description = (
            f'Modifier la durée de la tâche "{cible}" : '
            f'{_affiche(actuelle)} → {_affiche(nouvelle)} {_unite(nouvelle)}.'
        )
    elif not cible:
        changements = ["Modification de durée demandée, mais sans tâche cible identifiable."]
        description = "Modification de durée demandée sans cible identifiable."
    elif nombre is not None:
        changements = [
            f'Modifier la durée de la tâche "{cible}" de '
            f'{_affiche(abs(nombre))} {_unite(abs(nombre))}, mais la durée '
            "actuelle est indisponible."
        ]
        description = f'Modification de la durée de la tâche "{cible}" proposée.'
    else:
        changements = ["Modification de durée demandée, mais sans valeur exploitable."]
        description = "Modification de durée demandée sans valeur exploitable."

    return _resultat(
        "modifier_duree", cible,
        {"nouvelle_duree": nouvelle},
        changements, description, avertissements=avertissements,
    )


def _preparer_ajout_tache(texte, taches: Mapping, q: str) -> dict:
    brut = str(texte or "")
    debut = re.search(
        r"\b(?:ajoute|ajouter|cree|creer|genere|generer|nouvelle|nouveau)\b",
        brut, re.IGNORECASE,
    )
    suite = brut[debut.end():] if debut else brut
    avertissements: list[str] = []

    # Description libre, éventuellement introduite par « intitulée ».
    description = None
    libre = re.search(
        r"\b(?:intitul[eé]e?|appel[eé]e?|nomm[eé]e?|libell[eé]e?|description)"
        r"\s+([^,.;!?]+)", suite, re.IGNORECASE
    )
    if libre:
        description = _nettoye(
            re.split(r"\b(?:apr[eè]s|avant|pendant)\b", libre.group(1),
                     maxsplit=1, flags=re.IGNORECASE)[0]
        ) or None

    # Code explicite : « code C », « tâche C », ou guillemets.
    code = None
    explicite = re.search(r"\bcode\s+[\"«]?(" + CODE + r")", suite, re.IGNORECASE)
    if explicite:
        code = _resoudre_code(explicite.group(1), taches)
    else:
        apres_nom = re.search(
            r"\b(?:t[aâ]che|tache|activit[eé]|activite|phase|jalon)\s+"
            r"[\"«]?(" + CODE + r")[\"»]?", suite, re.IGNORECASE
        )
        if apres_nom and _est_code_explicite(apres_nom.group(1), taches):
            code = _resoudre_code(apres_nom.group(1), taches)

    if code is None:
        existants = {str(cle).lower() for cle in taches}
        numero = 1
        while f"NOUVEAU_{numero}".lower() in existants:
            numero += 1
        code = f"NOUVEAU_{numero}"
        avertissements.append(
            f'Aucun code explicite fourni : le code "{code}" a été généré de '
            "façon stable et reste à confirmer."
        )
    elif code in taches:
        avertissements.append(
            f'Le code "{code}" est déjà utilisé : la validation refusera '
            "cette proposition."
        )

    # Libellé libre lorsque la tâche est décrite sans code explicite.
    if description is None:
        apres_nom = re.search(
            r"\b(?:t[aâ]che|tache|activit[eé]|activite|phase|jalon)\b\s*(.*)$",
            suite, re.IGNORECASE,
        )
        if apres_nom:
            fragment = re.split(
                r"\b(?:apr[eè]s|avant|pendant|de\s+dur[eé]e|dur[eé]e)\b|[.;!?]",
                apres_nom.group(1), maxsplit=1, flags=re.IGNORECASE,
            )[0].strip()
            if fragment.lower().startswith(code.lower()):
                fragment = fragment[len(code):].strip()
            fragment = re.sub(
                r"^(?:de|d|des|du|la|le|les)\s+", "", fragment, flags=re.IGNORECASE
            )
            fragment = _nettoye(fragment)
            # « de 3 jours » est une durée, pas un libellé de tâche.
            if re.fullmatch(r"(?:" + NOMBRE + r")\s*jours?", fragment, re.IGNORECASE):
                fragment = ""
            description = fragment or None
    if description is None:
        description = "Nouvelle tâche"

    # Prédécesseurs : tout ce qui suit « après » dans la demande.
    predecesseurs: list[str] = []
    apres = re.search(r"\bapr[eè]s\b\s*(.*)$", brut, re.IGNORECASE)
    if apres:
        fragment = re.split(r"[.;!?]", apres.group(1), maxsplit=1)[0]
        predecesseurs = [
            candidat for candidat in _codes_dans(fragment, taches)
            if candidat != code
        ]
    for candidat in predecesseurs:
        if candidat not in taches:
            avertissements.append(
                f'Le prédécesseur "{candidat}" est introuvable dans le planning : '
                "la proposition ne peut pas être validée."
            )

    # Durée : nombre suivi de « jour(s) », sinon hypothèse explicite.
    duree = None
    nombre = re.search(r"\b(" + NOMBRE + r")\s*jours?\b", q)
    if nombre:
        duree = _nombre(nombre.group(1))
    if duree is None:
        duree = 1
        avertissements.append(
            "Hypothèse : aucune durée n'est précisée, une durée par défaut de "
            "1 jour est proposée."
        )

    elements = [f'Créer la tâche "{code}"']
    if predecesseurs:
        elements.append("après " + ", ".join(predecesseurs))
    else:
        elements.append("sans prédécesseur")
    elements.append(f"durée {_affiche(duree)} {_unite(duree)}")
    elements.append(f"description « {description} »")
    changements = [", ".join(elements) + "."]
    resume = (
        f'Ajouter la tâche "{code}"'
        + (" après " + ", ".join(predecesseurs) if predecesseurs else "")
        + f" (durée {_affiche(duree)} {_unite(duree)})."
    )
    return _resultat(
        "ajouter_tache", code,
        {
            "predecesseurs": predecesseurs,
            "duree": duree,
            "description": description,
        },
        changements, resume, avertissements=avertissements,
    )


def _preparer_suppression(texte, taches: Mapping, q: str) -> dict:
    massif = _est_massif(q)
    cible = None if massif else _cible_depuis_texte(texte, taches, projet=True)
    avertissements: list[str] = []
    if massif:
        changements = [
            "Suppression massive ou suppression de projet demandée : aucune "
            "modification ne sera appliquée sans cible explicite et "
            "confirmation renforcée."
        ]
        avertissements.append(
            "Suppression massive : action destructrice non exécutable par ce "
            "service sans cible explicite ; confirmation renforcée requise."
        )
        description = "Demande de suppression massive, non exécutable en l'état."
    elif not cible:
        changements = [
            "Suppression demandée, mais aucune tâche cible n'a pu être identifiée."
        ]
        avertissements.append("Aucune tâche cible n'a pu être identifiée dans la demande.")
        description = "Suppression demandée sans cible identifiable."
    else:
        if cible not in taches:
            avertissements.append(
                f'La tâche "{cible}" est introuvable dans le planning : '
                "la proposition ne peut pas être validée."
            )
        infos = taches.get(cible)
        morceaux = [f'Supprimer la tâche "{cible}"']
        if isinstance(infos, Mapping):
            duree = infos.get("duree")
            if _duree_entiere(duree) and duree >= 0:
                morceaux.append(f"durée {_affiche(duree)} {_unite(duree)}")
            libelle = _nettoye(infos.get("description"))
            if libelle:
                morceaux.append(f"description « {libelle} »")
        changements = [", ".join(morceaux) + "."]
        description = f'Supprimer la tâche "{cible}" (action destructrice).'
        dependants = _dependants(cible, taches)
        if dependants:
            avertissements.append(
                f'Attention : {len(dependants)} autre(s) tâche(s) dépendent de '
                f'"{cible}" ({", ".join(dependants)}) : confirmation renforcée requise.'
            )
            changements.append(
                f'{", ".join(dependants)} dépendent de "{cible}" : '
                "confirmation renforcée requise."
            )
    return _resultat(
        "supprimer_tache", cible, {}, changements, description,
        destructrice=True, avertissements=avertissements,
    )


def _preparer_predecesseur(texte, taches: Mapping) -> dict:
    brut = str(texte or "")
    predecesseur = None
    cible = None
    motifs = (
        r"\b(" + CODE + r")\s+comme\s+pr[eé]d[eé]cesseur\s+(?:de|pour|[aà])\s+"
        r"(?:la\s+)?(?:t[aâ]che\s+)?(" + CODE + r")",
        r"\bpr[eé]d[eé]cesseur\s+(" + CODE + r")\s+(?:[aà]|de|pour)\s+"
        r"(?:la\s+)?(?:t[aâ]che\s+)?(" + CODE + r")",
    )
    for motif in motifs:
        trouve = re.search(motif, brut, re.IGNORECASE)
        if trouve:
            predecesseur = _resoudre_code(trouve.group(1), taches)
            cible = _resoudre_code(trouve.group(2), taches)
            break
    codes = _codes_dans(brut, taches)
    if not cible:
        seul = re.search(
            r"\bpr[eé]d[eé]cesseur\s+(?:de|pour|[aà])\s+"
            r"(?:la\s+)?(?:t[aâ]che\s+)?(" + CODE + r")",
            brut, re.IGNORECASE,
        )
        if seul:
            cible = _resoudre_code(seul.group(1), taches)
    if predecesseur is None and codes:
        predecesseur = codes[0]
    if cible is None and len(codes) > 1:
        cible = codes[-1]

    avertissements: list[str] = []
    if not cible or not predecesseur:
        avertissements.append(
            "La tâche cible ou le prédécesseur n'a pas pu être identifié."
        )
    else:
        if cible not in taches:
            avertissements.append(
                f'La tâche "{cible}" est introuvable dans le planning : '
                "la proposition ne peut pas être validée."
            )
        if predecesseur not in taches:
            avertissements.append(
                f'Le prédécesseur "{predecesseur}" est introuvable dans le planning : '
                "la proposition ne peut pas être validée."
            )
        if predecesseur == cible:
            avertissements.append(
                "Une tâche ne peut pas être son propre prédécesseur : cycle détecté."
            )
        elif cible in taches and predecesseur in taches:
            copie = _copie(taches)
            infos = copie.get(cible)
            if isinstance(infos, Mapping):
                bruts = infos.get("depend_de") or ()
                dependances = list(bruts) if isinstance(bruts, (list, tuple, set)) else []
                if predecesseur not in dependances:
                    dependances.append(predecesseur)
                infos["depend_de"] = dependances
                try:
                    compute(copie)
                except ValidationError as exc:
                    avertissements.append(
                        "Cette dépendance créerait un cycle : la validation la refusera ("
                        + str(exc) + ")."
                    )

    if cible and predecesseur:
        changements = [
            f'Ajouter "{predecesseur}" comme prédécesseur de la tâche "{cible}".'
        ]
        description = f'Ajouter le prédécesseur "{predecesseur}" à la tâche "{cible}".'
    else:
        changements = [
            "Ajout de prédécesseur demandé, mais les codes sont incomplets."
        ]
        description = "Ajout de prédécesseur demandé sans codes exploitables."
    return _resultat(
        "ajouter_predecesseur", cible,
        {"predecesseur": predecesseur},
        changements, description, avertissements=avertissements,
    )


def preparer_action(texte: str, taches: dict, resultats: dict) -> dict:
    """Comprend une demande en français et prépare une proposition lisible.

    ``resultats`` est accepté pour la symétrie avec les autres services IA mais
    n'est pas nécessaire : aucune valeur n'est inventée à partir de chiffres
    calculés qui n'auraient pas été demandés.
    """
    planning = taches if isinstance(taches, Mapping) else {}
    q = _normalise(texte)
    if not q.strip():
        return _inconnu("La demande est vide.")

    # Les suppressions sont examinées en premier pour garantir la confirmation
    # renforcée, même si la phrase contient aussi un verbe d'ajout.
    if _contient(q, VERBES_SUPPRIME):
        return _preparer_suppression(texte, planning, q)

    if "predecesseur" in q:
        if not (_contient(q, VERBES_AJOUTE) or "est" in q or "ajoute" in q):
            return _inconnu(
                "Seul l'ajout d'un prédécesseur est pris en charge, pas sa consultation."
            )
        return _preparer_predecesseur(texte, planning)

    modification = (
        _contient(q, VERBES_AUGMENTE)
        or _contient(q, VERBES_DIMINUE)
        or _contient(q, VERBES_FIXE)
        or bool(re.search(r"[+-]\s*\d", str(texte or "")))
        or bool(re.search(r"\bduree\b[^.?!]{0,40}=\s*\d", q))
        or bool(re.search(
            r"\bajoute\s+(?:des\s+)?" + NOMBRE + r"\s*jours?\s+"
            r"(?:a\s+)?(?:la\s+)?duree", q
        ))
    )
    if modification:
        return _preparer_duree(texte, planning, q)

    if _contient(q, VERBES_AJOUTE) and re.search(
        r"\b(?:t[aâ]che|tache|activit[eé]|activite|phase|jalon)\b",
        q,
    ):
        return _preparer_ajout_tache(texte, planning, q)

    return _inconnu("Aucune formulation d'action connue n'a été reconnue.")


# ---------------------------------------------------------------------------
# 3. Validation côté serveur (droits vérifiés par la route, jamais ici)
# ---------------------------------------------------------------------------

def valider_action(proposition: dict, taches: dict) -> tuple[bool, list[str]]:
    """Valide une proposition sans jamais modifier ``taches``.

    Les droits d'accès sont contrôlés par la route avant cet appel. Les
    avertissements (par exemple les dépendants d'une suppression) sont joints
    aux messages tout en laissant ``ok`` à ``True`` : ils imposent une
    confirmation renforcée, pas un rejet automatique.
    """
    if not isinstance(proposition, Mapping):
        return False, [
            "Action non comprise : la proposition doit être un dictionnaire."
        ]
    type_action = proposition.get("type")
    if type_action not in TYPES_ACTIONS or type_action == "inconnu":
        return False, [
            "Action non comprise : cette demande n'est pas une action autorisée ; "
            "aucune modification ne peut être validée."
        ]

    planning = taches if isinstance(taches, Mapping) else {}
    parametres = proposition.get("parametres")
    parametres = parametres if isinstance(parametres, Mapping) else {}
    brut_cible = proposition.get("cible")
    cible = brut_cible.strip() if isinstance(brut_cible, str) else None

    erreurs: list[str] = []
    avertissements: list[str] = []

    if type_action == "ajouter_tache":
        if not cible:
            erreurs.append("Code de la nouvelle tâche absent ou invalide.")
        elif not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", cible):
            erreurs.append(
                f'Code de tâche invalide : "{cible}" (caractères autorisés : '
                "lettres, chiffres, tiret et tiret bas)."
            )
        elif cible in planning:
            erreurs.append(
                f'Le code "{cible}" est déjà utilisé : aucune tâche ne peut '
                "être écrasée sans confirmation explicite."
            )

        duree = parametres.get("duree")
        if not _duree_entiere(duree) or duree < 0:
            erreurs.append(
                "Durée invalide : un entier positif ou nul est requis."
            )

        bruts_preds = parametres.get("predecesseurs", [])
        predecesseurs: list[str] = []
        if isinstance(bruts_preds, (str, bytes)) or not isinstance(
            bruts_preds, (list, tuple, set)
        ):
            erreurs.append(
                "Prédécesseurs invalides : une liste de codes est attendue."
            )
        else:
            for candidat in bruts_preds:
                if not isinstance(candidat, str) or not candidat.strip():
                    erreurs.append("Prédécesseur vide ou invalide.")
                    continue
                propre = candidat.strip()
                if propre not in planning:
                    erreurs.append(
                        f'Le prédécesseur "{propre}" n\'existe pas dans le planning.'
                    )
                else:
                    predecesseurs.append(propre)

        if not erreurs and cible and cible not in planning:
            copie = _copie(planning)
            copie[cible] = {
                "description": str(parametres.get("description") or "Nouvelle tâche"),
                "duree": int(duree),
                "depend_de": predecesseurs,
            }
            try:
                compute(copie)
            except ValidationError as exc:
                erreurs.append(
                    "Le planning modifié serait invalide d'après le moteur "
                    "PERT/CPM : " + str(exc)
                )

    else:
        if not cible:
            erreurs.append("Cible absente : aucune tâche à modifier n'est précisée.")
        elif cible not in planning:
            erreurs.append(
                f'La tâche "{cible}" est introuvable dans le planning.'
            )

        if type_action == "modifier_duree":
            valeur = parametres.get("nouvelle_duree", parametres.get("duree"))
            if not _duree_entiere(valeur) or valeur < 0:
                erreurs.append(
                    "Durée invalide : un entier positif ou nul est requis."
                )
            infos = planning.get(cible)
            if isinstance(infos, Mapping):
                actuelle = infos.get("duree")
                if not _duree_entiere(actuelle) or actuelle < 0:
                    erreurs.append(
                        "La durée actuelle de la tâche cible est invalide : "
                        "aucune modification ne peut être validée."
                    )

        elif type_action == "ajouter_predecesseur":
            brut_pred = parametres.get("predecesseur")
            predecesseur = (
                brut_pred.strip() if isinstance(brut_pred, str) else None
            )
            if not predecesseur:
                erreurs.append("Prédécesseur absent ou invalide.")
            else:
                if predecesseur not in planning:
                    erreurs.append(
                        f'Le prédécesseur "{predecesseur}" n\'existe pas dans '
                        "le planning."
                    )
                if cible and predecesseur == cible:
                    erreurs.append(
                        "Une tâche ne peut pas être son propre prédécesseur : "
                        "cycle détecté."
                    )
                if not erreurs and cible in planning:
                    copie = _copie(planning)
                    infos = copie.get(cible)
                    if not isinstance(infos, Mapping):
                        erreurs.append(
                            f'Informations invalides pour la tâche "{cible}".'
                        )
                    else:
                        bruts = infos.get("depend_de") or ()
                        dependances = list(bruts) if isinstance(
                            bruts, (list, tuple, set)
                        ) else []
                        if predecesseur not in dependances:
                            dependances.append(predecesseur)
                        infos["depend_de"] = dependances
                        try:
                            compute(copie)
                        except ValidationError as exc:
                            erreurs.append(
                                "Cette dépendance créerait un cycle ou rendrait "
                                "le planning invalide : " + str(exc)
                            )

        elif type_action == "supprimer_tache":
            if cible in planning:
                dependants = _dependants(cible, planning)
                if dependants:
                    avertissements.append(
                        f"Attention : d'autres tâches dépendent de la tâche "
                        f'"{cible}" ({", ".join(dependants)}) ; la suppression '
                        "nécessite une confirmation renforcée."
                    )

    return (not erreurs), (erreurs + avertissements)
