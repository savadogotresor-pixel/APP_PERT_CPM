"""Génération déterministe d'une WBS (IA-GEN-01 à IA-GEN-06, §19.3).

Ce service est PUREMENT LOCAL : aucune clé API, aucun réseau et aucun
enregistrement ne sont nécessaires. Il analyse l'objectif et les contraintes
fournis par mots-clés, propose des phases / livrables / tâches / durées et
dépendances, puis vérifie le graphe avec le moteur canonique avant toute
création éventuelle.

Les durées produites sont des PROPOSITIONS indicatives : elles ne sont jamais
présentées comme des résultats calculés par le moteur PERT/CPM. La validation
:func:`valider_proposition` ne crée rien : l'enregistrement reste subordonné à
une confirmation explicite de l'utilisateur (IA-GEN-06).
"""

from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Mapping

from logic.pert import ValidationError, compute

# Champ de sortie : textes explicites sur le caractère indicatif des estimations.
HYPOTHESES = (
    "Hypothèses issues de l'analyse de l'objectif, du périmètre et des "
    "contraintes fournis (IA-GEN-01) : les durées proposées sont des durées "
    "indicatives non garanties, estimations à caractère indicatif issues de "
    "règles et non de calculs du moteur PERT/CPM. Aucune ressource/coût "
    "modélisé. Relations FS uniquement (chaque prédécesseur doit être terminé). "
    "Proposition à modifier et valider avant enregistrement : le responsable "
    "doit confirmer explicitement chaque durée, dépendance et jalon."
)

LIMITES = (
    "Limites : durées indicatives non garanties (aucun historique de "
    "productivité n'est utilisé) ; aucune ressource/coût modélisé ; relations "
    "FS uniquement, sans chevauchement ni délai. Cette proposition est à "
    "modifier et valider avant enregistrement ; aucun enregistrement n'est "
    "effectué par ce service (confirmation explicite requise, IA-GEN-06). "
    "Les faits calculés par le moteur et les recommandations proposées ici "
    "sont distincts : seule la validation du moteur constitue un fait."
)

AVERTISSEMENTS = (
    "Proposition générée par règles déterministes : aucune clé API ni appel "
    "réseau requis (IA-GEN-01 à IA-GEN-03).",
    "Durées indicatives non garanties : à modifier, valider puis confirmer "
    "explicitement avant tout enregistrement (IA-GEN-04, IA-GEN-06).",
)

# Profils déterministes. Chaque phase porte un préfixe de code stable, deux
# tâches réelles puis un jalon (durée nulle) qui verrouille la fin de phase.
_PROFILS: dict[str, list[dict]] = {
    "web": [
        {
            "nom": "Cadrage", "prefixe": "C",
            "taches": [
                ("Cadrer l'objectif et le périmètre : {objectif}", 3),
                ("Formaliser les exigences et les contraintes{contraintes}", 5),
            ],
            "livrables": ["Périmètre cadré", "Liste des exigences"],
        },
        {
            "nom": "Conception", "prefixe": "A",
            "taches": [
                ("Concevoir l'architecture fonctionnelle et les maquettes", 5),
                ("Définir le modèle de données et les interfaces", 8),
            ],
            "livrables": ["Maquettes", "Architecture technique"],
        },
        {
            "nom": "Développement", "prefixe": "D",
            "taches": [
                ("Développer l'interface utilisateur", 8),
                ("Développer les services et l'intégration", 13),
            ],
            "livrables": ["Interface utilisateur", "Services intégrés"],
        },
        {
            "nom": "Tests", "prefixe": "T",
            "taches": [
                ("Réaliser les tests unitaires et d'intégration", 5),
                ("Réaliser les tests fonctionnels et la recette", 8),
            ],
            "livrables": ["Rapport de tests", "Recette validée"],
        },
        {
            "nom": "Déploiement", "prefixe": "M",
            "taches": [
                ("Préparer l'environnement de production", 3),
                ("Mettre en production et vérifier le service", 5),
            ],
            "livrables": ["Environnement de production", "Service mis en production"],
        },
    ],
    "mobile": [
        {
            "nom": "Cadrage", "prefixe": "C",
            "taches": [
                ("Cadrer l'objectif mobile et le périmètre : {objectif}", 3),
                ("Formaliser les exigences et les contraintes{contraintes}", 5),
            ],
            "livrables": ["Périmètre mobile cadré", "Liste des exigences"],
        },
        {
            "nom": "Conception", "prefixe": "A",
            "taches": [
                ("Concevoir les écrans et le parcours utilisateur", 5),
                ("Définir l'architecture mobile et les API", 8),
            ],
            "livrables": ["Maquettes mobiles", "Architecture mobile"],
        },
        {
            "nom": "Développement", "prefixe": "D",
            "taches": [
                ("Développer les écrans et les interactions", 8),
                ("Développer les services et la synchronisation", 13),
            ],
            "livrables": ["Application mobile", "Services connectés"],
        },
        {
            "nom": "Tests", "prefixe": "T",
            "taches": [
                ("Tester sur émulateurs et appareils de référence", 5),
                ("Réaliser les tests fonctionnels et la recette", 8),
            ],
            "livrables": ["Rapport de tests multi-appareils", "Recette validée"],
        },
        {
            "nom": "Publication", "prefixe": "P",
            "taches": [
                ("Préparer les stores et les métadonnées", 3),
                ("Publier l'application et vérifier la disponibilité", 5),
            ],
            "livrables": ["Dossier de publication", "Application publiée"],
        },
    ],
    "donnees": [
        {
            "nom": "Cadrage", "prefixe": "C",
            "taches": [
                ("Cadrer l'objectif data/IA et le périmètre : {objectif}", 3),
                ("Formaliser les exigences et les contraintes{contraintes}", 5),
            ],
            "livrables": ["Périmètre cadré", "Liste des exigences"],
        },
        {
            "nom": "Collecte", "prefixe": "L",
            "taches": [
                ("Recenser les sources de données disponibles", 3),
                ("Collecter et qualifier les jeux de données", 8),
            ],
            "livrables": ["Inventaire des sources", "Jeux de données collectés"],
        },
        {
            "nom": "Préparation", "prefixe": "P",
            "taches": [
                ("Nettoyer et normaliser les données", 5),
                ("Construire les jeux d'entraînement et de test", 8),
            ],
            "livrables": ["Données normalisées", "Jeux d'entraînement/test"],
        },
        {
            "nom": "Modélisation", "prefixe": "M",
            "taches": [
                ("Concevoir et entraîner un premier modèle", 8),
                ("Ajuster les paramètres et documenter le modèle", 13),
            ],
            "livrables": ["Modèle entraîné", "Documentation du modèle"],
        },
        {
            "nom": "Validation", "prefixe": "V",
            "taches": [
                ("Évaluer les performances sur les jeux de test", 5),
                ("Vérifier la robustesse et les limites du modèle", 8),
            ],
            "livrables": ["Rapport d'évaluation", "Limites documentées"],
        },
        {
            "nom": "Restitution", "prefixe": "R",
            "taches": [
                ("Préparer les livrables et la documentation utilisateur", 3),
                ("Présenter les résultats et recueillir la validation", 5),
            ],
            "livrables": ["Documentation utilisateur", "Résultats restitués"],
        },
    ],
    "generique": [
        {
            "nom": "Cadrage", "prefixe": "C",
            "taches": [
                ("Cadrer l'objectif et le périmètre : {objectif}", 3),
                ("Formaliser les exigences et les contraintes{contraintes}", 5),
            ],
            "livrables": ["Périmètre cadré", "Liste des exigences"],
        },
        {
            "nom": "Conception", "prefixe": "A",
            "taches": [
                ("Analyser les besoins et concevoir la solution", 5),
                ("Définir les livrables et les critères d'acceptation", 8),
            ],
            "livrables": ["Solution conçue", "Critères d'acceptation"],
        },
        {
            "nom": "Réalisation", "prefixe": "D",
            "taches": [
                ("Réaliser la première partie des livrables", 8),
                ("Finaliser les livrables et les intégrer", 13),
            ],
            "livrables": ["Livrables intermédiaires", "Livrables intégrés"],
        },
        {
            "nom": "Vérification", "prefixe": "T",
            "taches": [
                ("Vérifier la conformité et la qualité", 5),
                ("Corriger les écarts et valider la recette", 8),
            ],
            "livrables": ["Rapport de vérification", "Recette validée"],
        },
        {
            "nom": "Clôture", "prefixe": "M",
            "taches": [
                ("Préparer le transfert et la documentation", 3),
                ("Livrer officiellement et clore le projet", 5),
            ],
            "livrables": ["Documentation finale", "Projet clos"],
        },
    ],
}

_MARKERS_MOBILE = (
    r"\bmobile\b", r"\bandroid\b", r"\bios\b", r"\biphone\b", r"\btablette\b",
)
_MARKERS_DONNEES = (
    r"\bdonnees?\b", r"\bdata\b", r"\bia\b", r"\bintelligence artificielle\b",
    r"\bmachine learning\b", r"\bdeep learning\b", r"\banalytique\b",
)
_MARKERS_WEB = (
    r"\bweb\b", r"\bapplication\b", r"\bappli\b", r"\bapp\b", r"\bsite\b",
    r"\bportail\b", r"\be-?commerce\b", r"\bfront\b", r"\bback\b",
)


def _normaliser(texte) -> str:
    """Minuscule ASCII approximative, sans accents, pour les mots-clés."""
    chaine = unicodedata.normalize("NFD", str(texte or "").lower())
    return "".join(c for c in chaine if unicodedata.category(c) != "Mn")


def _nettoyer_texte(texte, limite: int = 240) -> str:
    """Nettoie une donnée libre non fiable (une ligne, longueur bornée)."""
    chaine = str(texte or "").replace("\r", " ").replace("\n", " ")
    chaine = "".join(c for c in chaine if c in "\t" or ord(c) >= 32).strip()
    return chaine[:limite]


def _detecter_profil(objectif: str) -> str:
    """Choisit un profil déterministe à partir de mots-clés de l'objectif."""
    norm = _normaliser(objectif)
    if any(re.search(motif, norm) for motif in _MARKERS_MOBILE):
        return "mobile"
    if any(re.search(motif, norm) for motif in _MARKERS_DONNEES):
        return "donnees"
    if any(re.search(motif, norm) for motif in _MARKERS_WEB):
        return "web"
    return "generique"


def _description(modele: str, objectif: str, contraintes: str) -> str:
    """Remplit les éventuels champs de modèle avec des données tronquées."""
    suffixe = (" : " + contraintes) if contraintes else ""
    return modele.format(objectif=objectif, contraintes=suffixe)


def _construire_phases(profil: str, objectif: str, contraintes: str) -> list[dict]:
    """Construit les phases : 2 tâches réelles + 1 jalon, chaînage FS continu."""
    phases = []
    jalon_precedent = None

    for position, spec in enumerate(_PROFILS[profil]):
        prefixe = spec["prefixe"]
        codes = [f"{prefixe}{i}" for i in range(1, len(spec["taches"]) + 2)]
        taches = []

        for indice, (modele, duree) in enumerate(spec["taches"]):
            if indice == 0:
                predecesseurs = [jalon_precedent] if jalon_precedent else []
                texte = modele
            else:
                predecesseurs = [codes[indice - 1]]
                texte = modele
            taches.append({
                "code": codes[indice],
                "description": _description(texte, objectif, contraintes)
                if position == 0 and indice == 0 else texte,
                "duree": int(duree),
                "predecesseurs": list(predecesseurs),
                "jalon": False,
            })

        taches.append({
            "code": codes[-1],
            "description": f"Jalon — {spec['nom']} terminé et validé",
            "duree": 0,
            "predecesseurs": [codes[-2]],
            "jalon": True,
        })

        phases.append({
            "nom": spec["nom"],
            "livrables": list(spec["livrables"]),
            "taches": taches,
        })
        jalon_precedent = codes[-1]

    return phases


def _code_propre(code, contexte: str, erreurs: list[str]) -> str | None:
    """Valide un identifiant de tâche et retourne sa version normalisée."""
    if not isinstance(code, str) or not code.strip():
        erreurs.append(f"Code de tâche vide ou invalide ({contexte}).")
        return None
    return code.strip()


def _predecesseurs_propres(bruts, code: str, erreurs: list[str]) -> list[str]:
    """Normalise les prédécesseurs en liste de chaînes non vides."""
    if bruts is None:
        return []
    if isinstance(bruts, (str, bytes)):
        bruts = [bruts]
    if not isinstance(bruts, (list, tuple, set)):
        erreurs.append(
            f"Prédécesseurs invalides pour la tâche {code!r} : liste attendue."
        )
        return []
    propres = []
    for dep in bruts:
        if not isinstance(dep, str) or not dep.strip():
            erreurs.append(f"Prédécesseur vide ou invalide pour la tâche {code!r}.")
            continue
        propre = dep.strip()
        if propre not in propres:
            propres.append(propre)
    return propres


def _convertir_infos(infos, code: str, cle_predecesseurs: str,
                     erreurs: list[str]) -> dict:
    """Convertit une tâche au format attendu par le moteur PERT/CPM."""
    if not isinstance(infos, Mapping):
        erreurs.append(
            f"Informations invalides pour la tâche {code!r} : dictionnaire attendu."
        )
        return {"description": "", "duree": None, "depend_de": []}

    duree = infos.get("duree")
    if isinstance(duree, bool) or not isinstance(duree, int) or duree < 0:
        if (isinstance(duree, (int, float)) and not isinstance(duree, bool)
                and duree < 0):
            erreurs.append(
                f"Durée négative pour la tâche {code!r} : {duree!r} "
                "(entier ≥ 0 attendu)."
            )
        else:
            erreurs.append(
                f"Durée invalide pour la tâche {code!r} : {duree!r} "
                "(entier ≥ 0 attendu)."
            )

    alternatives = ("depend_de", "predecesseurs")
    if cle_predecesseurs == "depend_de":
        alternatives = ("depend_de", "predecesseurs")
    bruts = next(
        (infos[cle] for cle in alternatives if cle in infos), ()
    )
    predecesseurs = _predecesseurs_propres(bruts, code, erreurs)

    description = infos.get("description", "")
    return {
        "description": "" if description is None else str(description),
        "duree": duree,
        "depend_de": predecesseurs,
    }


def generer_wbs(objectif: str, contraintes: str | None = None) -> dict:
    """Propose une WBS déterministe, sans IA, sans réseau et sans écriture.

    Lève ``ValueError`` si l'objectif est vide. Le résultat contient des
    durées PROPOSÉES, indicatives et non garanties : elles doivent être
    modifiées et validées avant tout enregistrement.
    """
    if not isinstance(objectif, str) or not objectif.strip():
        raise ValueError(
            "L'objectif doit être une description non vide pour générer une WBS."
        )

    obj = _nettoyer_texte(objectif)
    con = _nettoyer_texte(contraintes) if contraintes is not None else ""
    profil = _detecter_profil(obj)
    phases = _construire_phases(profil, obj, con)

    proposition = {
        "objectif": obj,
        "contraintes": con,
        "hypotheses": HYPOTHESES,
        "limites": LIMITES,
        "phases": phases,
        "source": "regles",
        "avertissements": list(AVERTISSEMENTS),
    }

    # IA-GEN-05 : contrôle automatique avant présentation, sans effet de bord.
    try:
        _, erreurs = valider_proposition(proposition)
    except Exception as exc:  # défense en profondeur : ne jamais casser l'UI
        erreurs = [f"Vérification interne indisponible : {type(exc).__name__}."]
    if erreurs:
        proposition["avertissements"].append(
            "Attention : la vérification interne a signalé des anomalies "
            "dans la proposition ; corrigez-les avant validation."
        )

    return proposition


def valider_proposition(wbs: dict, taches_existantes: dict | None = None
                        ) -> tuple[dict, list[str]]:
    """Vérifie une proposition WBS et retourne ``(taches, erreurs)``.

    Toutes les tâches de toutes les phases sont converties au format
    ``{code: {'description', 'duree', 'depend_de'}}``, fusionnées avec
    ``taches_existantes``, puis contrôlées (codes, durées, prédécesseurs et
    cycles via :func:`logic.pert.compute`). La fonction ne lève pas et ne
    modifie ni ``wbs`` ni ``taches_existantes``. Aucune donnée n'est enregistrée.
    """
    erreurs: list[str] = []

    if not isinstance(wbs, Mapping):
        return {}, ["La proposition WBS doit être un dictionnaire (IA-GEN-05)."]

    taches: dict[str, dict] = {}

    # 1. Tâches existantes (copiées) : elles font partie du graphe à valider.
    if taches_existantes is not None:
        if not isinstance(taches_existantes, Mapping):
            erreurs.append("Les tâches existantes doivent être un dictionnaire.")
        else:
            existantes = copy.deepcopy(dict(taches_existantes))
            for brut_code, infos in existantes.items():
                code = _code_propre(brut_code, "tâche existante", erreurs)
                if code is None or code in taches:
                    if code is not None:
                        erreurs.append(f"Code de tâche en double : {code!r}.")
                    continue
                taches[code] = _convertir_infos(infos, code, "depend_de", erreurs)

    # 2. Tâches proposées par phases.
    phases = copy.deepcopy(wbs.get("phases") or [])
    if not isinstance(phases, (list, tuple)):
        erreurs.append("Le champ 'phases' doit être une liste de phases.")
        phases = []

    codes_proposes: set[str] = set()
    for phase in phases:
        if not isinstance(phase, Mapping):
            erreurs.append("Phase invalide : un dictionnaire était attendu.")
            continue
        nom_phase = str(phase.get("nom") or "phase sans nom")
        brut_taches = phase.get("taches") or []
        if not isinstance(brut_taches, (list, tuple)):
            erreurs.append(
                f"Les tâches de la phase {nom_phase!r} doivent être une liste."
            )
            continue

        for brut in brut_taches:
            if not isinstance(brut, Mapping):
                erreurs.append(
                    f"Tâche invalide dans la phase {nom_phase!r} : "
                    "un dictionnaire était attendu."
                )
                continue
            code = _code_propre(brut.get("code"), f"phase {nom_phase!r}", erreurs)
            if code is None:
                continue
            if code in taches:
                erreurs.append(
                    f"Collision de code avec une tâche existante : {code!r} "
                    "(code en double)."
                )
                continue
            if code in codes_proposes:
                erreurs.append(f"Code de tâche en double : {code!r}.")
                continue
            codes_proposes.add(code)
            taches[code] = _convertir_infos(
                brut, code, "predecesseurs", erreurs
            )

    if not taches:
        erreurs.append("Aucune tâche à valider : la proposition est vide.")

    # 3. Prédécesseurs existants (contrôle explicite, indépendant du moteur).
    for code, infos in taches.items():
        for dep in infos["depend_de"]:
            if dep not in taches:
                erreurs.append(
                    f"Prédécesseur inexistant pour la tâche {code!r} : "
                    f"{dep!r} (n'existe pas dans le graphe fusionné)."
                )

    # 4. Cycles et cohérence globale : seule la validation du moteur fait foi.
    try:
        compute(taches)
    except ValidationError as exc:
        for ligne in str(exc).splitlines():
            ligne = ligne.strip()
            if ligne:
                erreurs.append("Vérification moteur : " + ligne)
    except Exception as exc:  # données hostiles : ne jamais propager
        erreurs.append(
            "Vérification moteur impossible : " + type(exc).__name__ + "."
        )

    # Erreurs uniques, dans un ordre déterministe.
    return taches, list(dict.fromkeys(erreurs))


if __name__ == "__main__":  # pragma: no cover - démonstration locale
    exemple = generer_wbs("Créer un site web de réservation")
    graphe, anomalies = valider_proposition(exemple)
    resultat = compute(graphe)
    print("Phases :", ", ".join(p["nom"] for p in exemple["phases"]))
    print("Durée totale proposée (non garantie) :", resultat.duree_totale, "jours")
    print("Erreurs de validation :", anomalies)
