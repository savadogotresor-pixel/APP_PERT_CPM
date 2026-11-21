"""Synthèse intelligente commentée d'un planning PERT/CPM (lot IA-4, §19.7).

Le service met en forme des chiffres **déjà calculés** par le moteur canonique
:mod:`logic.pert` : il ne recalcule ni les dates PERT, ni les marges, ni les
chemins critiques. Il produit un rapport daté, sourcé et explicite sur ses
hypothèses, en distinguant les faits calculés des recommandations.

L'enrichissement par le service IA est volontairement optionnel : sans clé
d'API, avec un chiffre non vérifiable ou sur erreur, la synthèse reste entière
et ``appreciation_ia`` vaut ``None`` (aucune exception ne remonte à l'appelant).
"""

from __future__ import annotations

import copy
import math
import unicodedata
from collections.abc import Mapping
from datetime import datetime

from services import ia as service_ia

try:  # Le service de risques peut être créé en parallèle : import tolérant.
    from services.risques import analyser_risques as _analyser_risques
except Exception:  # pragma: no cover - dépend du déploiement des lots
    _analyser_risques = None


_UNITE = "jours calendaires"
_FLECHE = " → "
_SOURCE_CHIFFRES = "moteur PERT/CPM (source de vérité)"

_HYPOTHESES = (
    f"Unité de durée : {_UNITE}.",
    "Relations entre tâches : FS (finish-to-start) uniquement.",
    "Aucune ressource ni coût n'est modélisé.",
    "Données de référence : planning enregistré (source de vérité).",
    "Rapport généré à partir des données calculées, sans prédiction certaine.",
)


def _est_mapping(valeur) -> bool:
    return isinstance(valeur, Mapping)


def _texte(valeur, defaut: str = "") -> str:
    """Normalise un libellé non fiable en une seule ligne lisible."""
    if valeur is None:
        return defaut
    chaine = " ".join(str(valeur).split())
    return chaine or defaut


def _nombre(valeur):
    """Valeur numérique finie, ou ``None`` si la donnée est absente."""
    if isinstance(valeur, bool) or valeur is None:
        return None
    if isinstance(valeur, (int, float)):
        nombre = float(valeur)
    else:
        try:
            nombre = float(str(valeur).strip().replace(",", "."))
        except (TypeError, ValueError):
            return None
    return nombre if math.isfinite(nombre) else None


def _formater_nombre(valeur):
    """Formate un nombre calculé sans virgule superflue (9.0 -> '9')."""
    nombre = _nombre(valeur)
    if nombre is None:
        return None
    if nombre.is_integer():
        return str(int(nombre))
    return ("%.10f" % nombre).rstrip("0").rstrip(".").replace(".", ",")


def _entier(valeur) -> int:
    nombre = _nombre(valeur)
    return int(nombre) if nombre is not None else 0


def _copier_entrees(taches, resultats, chemins_critiques):
    """Copie les données reçues : le rapport ne modifie jamais ses arguments."""
    try:
        taches_copie = copy.deepcopy(taches) if _est_mapping(taches) else {}
        resultats_copie = copy.deepcopy(resultats) if _est_mapping(resultats) else {}
    except Exception:  # objets non copiables : copie superficielle suffisante
        taches_copie = dict(taches) if _est_mapping(taches) else {}
        resultats_copie = dict(resultats) if _est_mapping(resultats) else {}

    chemins: list[list[str]] = []
    if isinstance(chemins_critiques, (list, tuple)):
        for chemin in chemins_critiques:
            if isinstance(chemin, str):
                codes = [chemin]
            elif isinstance(chemin, (list, tuple, set)):
                codes = [str(code) for code in chemin]
            else:
                continue
            codes = [code.strip() for code in codes if code.strip()]
            if codes:
                chemins.append(codes)
    return taches_copie, resultats_copie, chemins


def _chemins_textes(chemins: list[list[str]]) -> list[str]:
    return [_FLECHE.join(codes) for codes in chemins]


def _est_critique(resultat) -> bool:
    if not _est_mapping(resultat):
        return False
    if "critique" in resultat:
        critique = resultat.get("critique")
        if isinstance(critique, bool):
            return critique
        nombre = _nombre(critique)
        if nombre is not None:
            return nombre != 0
        return _texte(critique).lower() in {
            "oui", "true", "yes", "critique", "critical",
        }
    marge = _nombre(resultat.get("marge", resultat.get("marge_totale")))
    return marge is not None and marge == 0


def _codes_critiques(taches, resultats, chemins: list[list[str]]) -> list[str]:
    """Codes critiques déclarés par le moteur, avec repli sur les chemins."""
    critiques = [
        str(code) for code in taches if _est_critique(resultats.get(code))
    ]
    if critiques:
        return critiques
    vus: set[str] = set()
    for chemin in chemins:
        for code in chemin:
            if code not in vus:
                vus.add(code)
                critiques.append(code)
    return critiques


def _marge_texte(resultat) -> str | None:
    if not _est_mapping(resultat):
        return None
    for cle in ("marge", "marge_totale"):
        if resultat.get(cle) is not None:
            return _formater_nombre(resultat.get(cle))
    return None


def _sans_accents(texte) -> str:
    normalise = unicodedata.normalize("NFD", str(texte or ""))
    return "".join(c for c in normalise if unicodedata.category(c) != "Mn").lower()


def _origine_risque(risque) -> str:
    """IA-RISK-03 : origine normalisée, uniquement 'regle' ou 'regle+ia'."""
    if not _est_mapping(risque):
        return "regle"
    brut = (
        risque.get("origine")
        or risque.get("source")
        or risque.get("type")
        or "regle"
    )
    texte = _sans_accents(brut)
    if "ia" in texte.replace("+", " + ").split():
        return "regle+ia"
    return "regle"


def _champ_risque(risque, cles, defaut: str) -> str:
    if not _est_mapping(risque):
        return _texte(risque, defaut)
    for cle in cles:
        if risque.get(cle) is not None:
            return _texte(risque.get(cle), defaut)
    return defaut


def _code_risque(risque) -> str | None:
    if not _est_mapping(risque):
        return None
    for cle in ("code", "tache", "taches", "tâche", "tâches"):
        valeur = risque.get(cle)
        if valeur is None:
            continue
        if isinstance(valeur, (list, tuple, set)):
            codes = [_texte(v) for v in valeur if _texte(v)]
            if codes:
                return ", ".join(codes)
        elif _texte(valeur):
            return _texte(valeur)
    return None


def _recommandation(risque, titre: str) -> str:
    """Recommandation prudente déduite de l'alerte, sans engagement de date."""
    code = _code_risque(risque)
    if code:
        marge = None
        if _est_mapping(risque):
            marge = _formater_nombre(
                risque.get("marge", risque.get("marge_totale"))
            )
        if marge is not None:
            return (
                f"Surveiller la marge de {code} ({marge} {_UNITE}) : vérifier "
                "son avancement avant tout engagement, sans modifier le "
                "planning de référence sans validation."
            )
        return (
            f"Surveiller la marge et l'avancement de {code} : ne pas "
            "s'engager sur une date sans recalcul préalable par le moteur."
        )
    return (
        f"Suivre l'alerte « {titre} » : vérifier les marges concernées et "
        "arbitrer les priorités sans modifier le planning de référence sans "
        "validation."
    )


def _commenter_scenario(scenario) -> str:
    if not _est_mapping(scenario):
        return "Scénario incomplet : aucune donnée de simulation transmise."
    titre = _texte(
        scenario.get("titre") or scenario.get("nom"), "Scénario sans titre"
    )
    resultat = scenario.get("resultat")
    if not _est_mapping(resultat):
        return f"{titre} : scénario incomplet (résultat de simulation absent)."
    initial = resultat.get("initial")
    simule = resultat.get("simule")
    avant = _formater_nombre(initial.get("duree_totale")) if _est_mapping(initial) else None
    apres = _formater_nombre(simule.get("duree_totale")) if _est_mapping(simule) else None
    if avant is None or apres is None:
        return (
            f"{titre} : scénario incomplet "
            "(durée initiale ou simulée non transmise)."
        )
    gain = _formater_nombre(resultat.get("gains"))
    if gain is None:
        return f"{titre} : {avant} → {apres} jours (gain non transmis)."
    return f"{titre} : {avant} → {apres} jours (gain {gain})"


def _enrichissement_ia(taches, resultats, duree, chemins, avertissements):
    """Appréciation IA facultative et vérifiée ; ``None`` en cas de doute."""
    try:
        if not callable(getattr(service_ia, "est_disponible", None)):
            return None
        if not service_ia.est_disponible():
            return None
        if not all(
            callable(getattr(service_ia, nom, None))
            for nom in ("construire_contexte", "repondre", "verifier_nombres")
        ):
            return None

        contexte = service_ia.construire_contexte(
            taches, resultats, duree, chemins, None
        )
        question = (
            "En une ou deux phrases, commentez en français la synthèse du "
            "planning à partir des seuls faits calculés transmis. N'introduisez "
            "aucun chiffre nouveau et ne promettez aucune date."
        )
        reponse = service_ia.repondre(
            question, taches, resultats, duree, chemins, None, None
        )
        if _est_mapping(reponse):
            texte_brut = reponse.get("texte")
            source = reponse.get("source")
            appel_ia = reponse.get("appel_ia")
        else:
            texte_brut, source, appel_ia = reponse, None, None

        if source is not None and str(source).lower() != "ia":
            return None
        if appel_ia is not None and not bool(appel_ia):
            return None
        texte = _texte(texte_brut)
        if not texte:
            return None
        if not service_ia.verifier_nombres(texte, contexte):
            avertissements.append(
                "Enrichissement IA écarté : un chiffre du texte n'a pas pu "
                "être vérifié par rapport aux faits calculés."
            )
            return None
        return texte
    except Exception:
        avertissements.append(
            "Enrichissement IA indisponible : la synthèse reste fondée sur "
            "le moteur PERT/CPM."
        )
        return None


def generer_synthese(
    nom_projet: str,
    taches: dict,
    resultats: dict,
    duree_totale,
    chemins_critiques: list[list[str]],
    risques: list[dict] | None = None,
    scenarios: list[dict] | None = None,
    donnees_manquantes: list[str] | None = None,
) -> dict:
    """Construit la synthèse intelligente §19.7 à partir des résultats du moteur.

    Aucun dictionnaire reçu n'est modifié. Les chiffres affichés proviennent
    exclusivement de ``duree_totale``, ``resultats``, ``risques`` et
    ``scenarios`` (ou des durées de ``taches`` pour la somme d'un chemin, telle
    que demandée par le cahier des charges).
    """
    taches, resultats, chemins = _copier_entrees(
        taches, resultats, chemins_critiques
    )
    duree = _entier(duree_totale)
    nombre_taches = len(taches)
    textes_chemins = _chemins_textes(chemins)
    critiques = _codes_critiques(taches, resultats, chemins)

    avertissements = [
        "Les chiffres cités sont des faits calculés par le moteur PERT/CPM ; "
        "les recommandations sont des appréciations, pas des faits."
    ]

    # --- Synthèse générale -------------------------------------------------
    if textes_chemins:
        if len(textes_chemins) == 1:
            phrase_chemin = f"Le chemin critique est {textes_chemins[0]}."
        else:
            phrase_chemin = (
                "Les chemins critiques sont " + ", ".join(textes_chemins) + "."
            )
    else:
        phrase_chemin = "Aucun chemin critique n'a été fourni par le moteur."
    synthese = (
        f"Le projet {_texte(nom_projet, 'sans nom')} compte {nombre_taches} "
        f"tâches ; sa durée calculée est de {duree} {_UNITE}. "
        f"{phrase_chemin} Les chiffres sont des faits calculés ; les "
        "recommandations ci-dessous sont des appréciations."
    )

    # --- Facteurs de durée -------------------------------------------------
    facteurs: list[str] = []
    for codes in chemins:
        somme = 0.0
        manquantes: list[str] = []
        for code in codes:
            infos = taches.get(code)
            valeur = _nombre(infos.get("duree")) if _est_mapping(infos) else None
            if valeur is None:
                manquantes.append(code)
            else:
                somme += valeur
        txt_chemin = _FLECHE.join(codes)
        if manquantes:
            somme_texte = (
                "somme non calculable (durée absente pour "
                + ", ".join(manquantes)
                + ")"
            )
            avertissements.append(
                "Durée absente pour la tâche "
                + ", ".join(manquantes)
                + " : la somme du chemin critique n'a pas pu être vérifiée."
            )
        else:
            somme_texte = f"somme des durées : {_formater_nombre(somme)} {_UNITE}"
            if abs(somme - duree) > 1e-9:
                avertissements.append(
                    f"La somme des durées du chemin {txt_chemin} "
                    f"({_formater_nombre(somme)} {_UNITE}) diffère de la durée "
                    f"totale calculée ({duree} {_UNITE}) : cette dernière fait foi."
                )
        facteurs.append(
            f"Chemin critique : {txt_chemin} — {somme_texte} ; "
            f"tâches critiques : {', '.join(codes)}."
        )
    if not facteurs:
        if nombre_taches:
            facteurs.append(
                f"Aucun chemin critique n'a été transmis par le moteur ; la "
                f"durée totale calculée est de {duree} {_UNITE}."
            )
        else:
            facteurs.append(
                f"Aucun chemin critique : aucun changement n'a été calculé "
                f"et la durée totale est de {duree} {_UNITE}."
            )

    # --- Chemin critique et marges ----------------------------------------
    if critiques:
        liste = ", ".join(critiques)
        if len(chemins) == 1:
            debut = (
                f"Le chemin critique {textes_chemins[0]} comprend "
                f"{len(critiques)} tâches critiques : {liste}."
            )
        elif chemins:
            debut = (
                "Les chemins critiques ("
                + "; ".join(textes_chemins)
                + f") comprennent {len(critiques)} tâches critiques "
                f"distinctes : {liste}."
            )
        else:
            debut = (
                f"Le moteur signale {len(critiques)} tâches critiques : "
                f"{liste}."
            )
        chemin_commentaire = (
            debut
            + " Tout retard sur ces tâches retarde la fin calculée du projet : "
            "aucune marge totale ne peut l'absorber."
        )
    else:
        chemin_commentaire = (
            "Aucune tâche critique ni chemin critique n'a été transmis : le "
            "commentaire sur le chemin critique n'est pas disponible."
        )

    avec_resultats = [c for c in taches if _est_mapping(resultats.get(c))]
    sans_resultats = [str(c) for c in taches if c not in avec_resultats]
    for code in sans_resultats:
        avertissements.append(f"Résultats PERT absents pour la tâche {code}.")
    non_critiques = [
        str(code) for code in avec_resultats if not _est_critique(resultats.get(code))
    ]
    if non_critiques:
        lignes = []
        for code in non_critiques:
            marge = _marge_texte(resultats.get(code))
            lignes.append(
                f"{code} : marge de {marge} {_UNITE}"
                if marge is not None
                else f"{code} : marge non renseignée"
            )
        marges_commentaire = (
            "Marges disponibles sur les tâches non critiques : "
            + "; ".join(lignes)
            + ". Ces marges sont des données calculées, pas une réserve de "
            "temps garantie."
        )
    elif nombre_taches == 0:
        marges_commentaire = "Aucune tâche n'est définie : aucune marge à commenter."
    elif sans_resultats:
        marges_commentaire = (
            "Marges non disponibles : résultats PERT absents pour les tâches "
            "concernées."
        )
    else:
        marges_commentaire = (
            "Aucune marge disponible : toutes les tâches sont critiques."
        )
    if sans_resultats and non_critiques:
        marges_commentaire += (
            " Résultats PERT absents pour : " + ", ".join(sans_resultats) + "."
        )

    # --- Alertes de risques (avec repli tolérant sur le service dédié) -----
    if risques is None and _analyser_risques is not None:
        try:
            risques = _analyser_risques(taches, resultats, duree, chemins)
        except Exception:
            risques = []
            avertissements.append(
                "Analyse des risques indisponible : aucune alerte automatique "
                "n'a pu être ajoutée."
            )
    if risques is None:
        risques = []
    if not isinstance(risques, (list, tuple)):
        risques = [risques]

    lignes_risques: list[str] = []
    recommandations: list[str] = []
    for risque in risques:
        titre = _champ_risque(risque, ("titre", "libelle", "libellé", "message"), "alerte sans titre")
        justification = _champ_risque(
            risque,
            ("justification", "explication", "raison", "detail", "détail", "message"),
            "justification non transmise",
        )
        origine = _origine_risque(risque)
        lignes_risques.append(f"Alerte ({origine}) — {titre} : {justification}")
        recommandations.append(_recommandation(risque, titre))
    if not lignes_risques:
        lignes_risques.append(
            "Aucune alerte de risque transmise ou détectée : le rapport ne "
            "présente pas de risque identifié."
        )
        recommandations.append(
            "Aucune recommandation particulière déduite des alertes "
            "transmises ; toute modification du planning doit être validée "
            "séparément."
        )

    # --- Scénarios simulés -------------------------------------------------
    if scenarios is None:
        scenarios_liste: list = []
    elif _est_mapping(scenarios):
        scenarios_liste = [scenarios]
    elif isinstance(scenarios, (list, tuple)):
        scenarios_liste = list(scenarios)
    else:
        scenarios_liste = []
    scenarios_commentaires = [_commenter_scenario(s) for s in scenarios_liste]
    if not scenarios_commentaires:
        scenarios_commentaires.append(
            "Aucun scénario simulé transmis : seule la durée de référence est "
            "commentée."
        )

    # --- Données manquantes explicites ------------------------------------
    if donnees_manquantes:
        for manquant in donnees_manquantes:
            avertissements.append(
                f"Donnée manquante signalée : {_texte(manquant, 'élément non précisé')}"
            )

    appreciation_ia = _enrichissement_ia(
        taches, resultats, duree, chemins, avertissements
    )

    return {
        "titre": f"Synthèse intelligente — {_texte(nom_projet, 'sans nom')}",
        "date_generation": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "donnees_reference": {
            "duree_totale": duree,
            "nombre_taches": nombre_taches,
            "nombre_critiques": len(critiques),
            "chemins_critiques": textes_chemins,
            "unite": _UNITE,
            "relations": "FS",
        },
        "hypotheses": list(_HYPOTHESES),
        "synthese": synthese,
        "facteurs_duree": facteurs,
        "chemin_critique_commentaire": chemin_commentaire,
        "marges_commentaire": marges_commentaire,
        "risques": lignes_risques,
        "recommandations": recommandations,
        "scenarios_commentaires": scenarios_commentaires,
        "source_chiffres": _SOURCE_CHIFFRES,
        "avertissements": avertissements,
        "appreciation_ia": appreciation_ia,
    }
