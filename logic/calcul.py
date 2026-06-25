from datetime import timedelta


def passe_avant(taches):
    """
    taches : dictionnaire comme dans data.py
    Retourne un dictionnaire du type :
        {"A": {"ES": 0, "EF": 3}, "B": {"ES": 3, "EF": 7}, ...}
    """
    resultats = {}

    while len(resultats) < len(taches):
        for nom, infos in taches.items():
            if nom in resultats:
                continue  # déjà calculée, on passe

            # TODO 1 : la tâche n'est calculable que si toutes ses
            # dépendances ont déjà un résultat dans `resultats`
            if not all(dep in resultats for dep in infos["depend_de"]):
                continue  # une dépendance manque encore, on la zappe pour l'instant

            # TODO 2 : ES = 0 si pas de dépendance, sinon max des EF des dépendances
            if not infos["depend_de"]:
                es = 0
            else:
                es = max(resultats[dep]["EF"] for dep in infos["depend_de"])

            # TODO 3 : EF = ES + durée
            ef = es + infos["duree"]

            # TODO 4 : on stocke le résultat
            resultats[nom] = {"ES": es, "EF": ef}

    return resultats


if __name__ == '__main__':
    from data import taches
    print(passe_avant(taches))

def passe_arriere(taches, resultats_avant):
    """
    taches : le dictionnaire de data.py
    resultats_avant : le dictionnaire renvoyé par passe_avant()
    Retourne : {"A": {"LS": 0, "LF": 3}, "B": {"LS": 3, "LF": 7}, ...}
    """
    duree_totale = max(r["EF"] for r in resultats_avant.values())

    # 1. construire les successeurs : qui dépend de moi ?
    successeurs = {nom: [] for nom in taches}
    for nom, infos in taches.items():
        for dep in infos["depend_de"]:
            successeurs[dep].append(nom)

    # 2. boucle "miroir" de la passe avant, en remontant
    resultats = {}
    while len(resultats) < len(taches):
        for nom, infos in taches.items():
            if nom in resultats:
                continue

            succs = successeurs[nom]
            if not all(s in resultats for s in succs):
                continue  # un successeur n'est pas encore calculé, on attend

            if not succs:
                lf = duree_totale  # personne après moi -> je finis à la date de fin du projet
            else:
                lf = min(resultats[s]["LS"] for s in succs)

            ls = lf - infos["duree"]
            resultats[nom] = {"LS": ls, "LF": lf}

    return resultats


if __name__ == '__main__':
    from data import taches
    av = passe_avant(taches)
    ar = passe_arriere(taches, av)
    print(ar)

def calculer_marges(taches, resultats_avant, resultats_arriere):
    """
    Retourne :
        {"A": {"ES":0, "EF":3, "LS":0, "LF":3, "marge":0, "critique":True}, ...}
    """
    resultats = {}
    for nom in taches:
        es = resultats_avant[nom]["ES"]
        ef = resultats_avant[nom]["EF"]
        ls = resultats_arriere[nom]["LS"]
        lf = resultats_arriere[nom]["LF"]
        marge = ls - es
        critique = (marge == 0)

        resultats[nom] = {
            "ES": es, "EF": ef,
            "LS": ls, "LF": lf,
            "marge": marge,
            "critique": critique
        }
    return resultats


def liste_chemin_critique(resultats_marges):
    """Retourne juste les noms des tâches critiques, ex: ['A','B','G','H','K','L']"""
    return [nom for nom, r in resultats_marges.items() if r["critique"]]


if __name__ == '__main__':
    from data import taches
    av = passe_avant(taches)
    ar = passe_arriere(taches, av)
    final = calculer_marges(taches, av, ar)
    for nom, r in final.items():
        print(nom, r)
    print("Chemin critique :", liste_chemin_critique(final))


def valider_taches(taches):
    """
    Vérifie la cohérence du dictionnaire de tâches.
    Retourne une liste de messages d'erreur (vide si tout est valide).
    """
    erreurs = []
    noms_connus = set(taches.keys())

    # 1. dépendances vers des tâches inexistantes
    for nom, infos in taches.items():
        for dep in infos["depend_de"]:
            if dep not in noms_connus:
                erreurs.append(f"La tâche \"{nom}\" dépend de \"{dep}\", qui n'existe pas")

    if erreurs:
        return erreurs  # inutile de chercher un cycle si des dépendances sont déjà invalides

    # 2. détection de cycle (parcours en profondeur)
    EN_COURS, FINI = 1, 2
    etat = {}

    def dfs(nom, chemin):
        etat[nom] = EN_COURS
        chemin.append(nom)
        for dep in taches[nom]["depend_de"]:
            if etat.get(dep) == EN_COURS:
                idx = chemin.index(dep)
                cycle = chemin[idx:] + [dep]
                erreurs.append("Dépendance circulaire détectée : " + " → ".join(cycle))
                return True
            if etat.get(dep) != FINI:
                if dfs(dep, chemin):
                    return True
        chemin.pop()
        etat[nom] = FINI
        return False

    for nom in taches:
        if etat.get(nom) is None:
            if dfs(nom, []):
                break

    return erreurs


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