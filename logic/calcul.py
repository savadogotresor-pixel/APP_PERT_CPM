"""Façade de compatibilité pour l'application Flask.

Le moteur canonique vit désormais dans :mod:`logic.pert`. Les fonctions
historiques conservent leur signature et leur format de retour ; les nouvelles
fonctions et le résultat complet sont réexportés pour les nouveaux usages.
"""

from datetime import timedelta

try:
    from .pert import (
        EPSILON,
        PERTResult,
        ValidationError,
        compute,
        liste_chemins_critiques,
        topological_order,
        validate,
    )
except ImportError:  # exécution directe : python logic/calcul.py
    from pert import (
        EPSILON,
        PERTResult,
        ValidationError,
        compute,
        liste_chemins_critiques,
        topological_order,
        validate,
    )

__all__ = [
    "EPSILON",
    "PERTResult",
    "ValidationError",
    "compute",
    "liste_chemins_critiques",
    "topological_order",
    "validate",
    "passe_avant",
    "passe_arriere",
    "calculer_marges",
    "liste_chemin_critique",
    "valider_taches",
    "generer_conseils",
]


def passe_avant(taches):
    """
    taches : dictionnaire comme dans data.py
    Retourne un dictionnaire du type :
        {"A": {"ES": 0, "EF": 3}, "B": {"ES": 3, "EF": 7}, ...}
    """
    resultat = compute(taches)
    return {nom: {"ES": r["ES"], "EF": r["EF"]}
            for nom, r in resultat.resultats.items()}


def passe_arriere(taches, resultats_avant):
    """
    taches : le dictionnaire de data.py
    resultats_avant : le dictionnaire renvoyé par passe_avant()
    Retourne : {"A": {"LS": 0, "LF": 3}, "B": {"LS": 3, "LF": 7}, ...}

    ``resultats_avant`` est conservé pour compatibilité : le moteur canonique
    recalcule la passe avant de manière cohérente.
    """
    resultat = compute(taches)
    return {nom: {"LS": r["LS"], "LF": r["LF"]}
            for nom, r in resultat.resultats.items()}


def calculer_marges(taches, resultats_avant, resultats_arriere):
    """
    Retourne :
        {"A": {"ES":0, "EF":3, "LS":0, "LF":3, "marge":0, "critique":True}, ...}

    Les arguments intermédiaires sont conservés pour compatibilité ; les
    marges libre/totale détaillées sont disponibles via ``compute(taches)``.
    """
    resultat = compute(taches)
    return {
        nom: {
            "ES": r["ES"], "EF": r["EF"],
            "LS": r["LS"], "LF": r["LF"],
            "marge": r["marge"],
            "critique": r["critique"],
        }
        for nom, r in resultat.resultats.items()
    }


def liste_chemin_critique(resultats_marges):
    """Retourne juste les noms des tâches critiques, ex: ['A','B','G','H','K','L']"""
    return [nom for nom, r in resultats_marges.items() if r["critique"]]


def valider_taches(taches):
    """
    Vérifie la cohérence du dictionnaire de tâches.
    Retourne une liste de messages d'erreur (vide si tout est valide).
    """
    return validate(taches)


def generer_conseils(taches, resultats, chemin_critique, duree_totale,
                      date_debut=None, date_fin_cible=None):
    """
    Génère des conseils d'optimisation à partir des marges déjà calculées.
    Retourne une liste de dicts : {"type": ..., "texte": ...}
    """
    conseils = []
    nb_taches = len(taches)

    # 1. Tâches critiques : elles pilotent la durée totale du projet
    if chemin_critique:
        conseils.append({
            "type": "critique",
            "texte": (
                f"🔴 {len(chemin_critique)} tâche(s) sur {nb_taches} sont critiques et forment le "
                f"chemin critique ({' → '.join(chemin_critique)}), qui détermine la durée totale du "
                f"projet ({duree_totale} jour(s)). Concentrez vos meilleures ressources sur ces "
                f"tâches : le moindre retard sur l'une d'elles retarde directement la fin du projet."
            )
        })

    # 2. Tâches "presque critiques" : marge faible (≤ 10 % de la durée totale, au moins 1 jour)
    seuil_attention = max(1, round(duree_totale * 0.1))
    proches = sorted(
        [(nom, r["marge"]) for nom, r in resultats.items()
         if not r["critique"] and r["marge"] <= seuil_attention],
        key=lambda x: x[1]
    )
    if proches:
        detail = ", ".join(f"{nom} (marge {m} j)" for nom, m in proches)
        conseils.append({
            "type": "attention",
            "texte": (
                f"🟠 Surveillez aussi : {detail}. Leur marge est faible : un petit retard suffirait à "
                f"les rendre critiques à leur tour."
            )
        })

    # 3. Tâches flexibles : marge confortable (≥ 20 % de la durée totale, au moins 2 jours)
    seuil_flexible = max(2, round(duree_totale * 0.2))
    flexibles = sorted(
        [(nom, r["marge"]) for nom, r in resultats.items()
         if not r["critique"] and r["marge"] >= seuil_flexible],
        key=lambda x: -x[1]
    )[:3]
    if flexibles:
        detail = ", ".join(f"{nom} (marge {m} j)" for nom, m in flexibles)
        conseils.append({
            "type": "flexible",
            "texte": (
                f"🟢 {detail} disposent d'une marge confortable : vous pouvez décaler leur démarrage, "
                f"ou transférer temporairement des ressources vers les tâches critiques, sans risque "
                f"pour le planning global."
            )
        })

    # 4. Comparaison à une date de fin cible, si elle a été indiquée
    if date_fin_cible and date_debut:
        date_fin_reelle = date_debut + timedelta(days=duree_totale)
        ecart = (date_fin_reelle - date_fin_cible).days
        if ecart > 0:
            critiques_str = ", ".join(chemin_critique) if chemin_critique else "aucune"
            conseils.append({
                "type": "delai_retard",
                "texte": (
                    f"⏱️ Avec ce planning, le projet se termine le "
                    f"{date_fin_reelle.strftime('%d/%m/%Y')}, soit {ecart} jour(s) après la date cible "
                    f"du {date_fin_cible.strftime('%d/%m/%Y')}. Pour rattraper ce retard, il faut "
                    f"réduire la durée des tâches CRITIQUES uniquement ({critiques_str}) : raccourcir "
                    f"une tâche non critique n'aura aucun effet tant qu'elle ne devient pas elle-même "
                    f"critique."
                )
            })
        else:
            conseils.append({
                "type": "delai_ok",
                "texte": (
                    f"✅ Le planning respecte la date cible du "
                    f"{date_fin_cible.strftime('%d/%m/%Y')} : le projet se termine le "
                    f"{date_fin_reelle.strftime('%d/%m/%Y')}, avec {-ecart} jour(s) d'avance."
                )
            })

    # 5. Indicateur global de tension du planning
    taux_critique = (len(chemin_critique) / nb_taches) if nb_taches else 0
    if taux_critique >= 0.6:
        conseils.append({
            "type": "global",
            "texte": (
                f"📐 {round(taux_critique * 100)} % des tâches sont critiques : le planning est tendu, "
                f"avec très peu de flexibilité. Envisagez d'ajouter des ressources ou de revoir "
                f"certaines dépendances pour paralléliser davantage de tâches."
            )
        })

    return conseils


if __name__ == '__main__':
    try:
        from .data import taches
    except ImportError:  # exécution directe : python logic/calcul.py
        from data import taches

    resultat = compute(taches)
    print("Durée totale :", resultat.duree_totale)
    for nom, r in resultat.resultats.items():
        print(nom, r)
    print("Chemin critique :", liste_chemin_critique(resultat.resultats))
    print("Chemins critiques :", resultat.chemins_critiques)
