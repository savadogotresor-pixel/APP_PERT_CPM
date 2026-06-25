taches = {
    "A": {
        "description": "Analyse des besoins et cahier des charges",
        "duree": 3,
        "depend_de": []
    },
    "B": {
        "description": "Conception de la base de données",
        "duree": 4,
        "depend_de": ["A"]
    },
    "C": {
        "description": "Maquettes UI/UX (wireframes)",
        "duree": 4,
        "depend_de": ["A"]
    },
    "D": {
        "description": "Mise en place de l'environnement de développement",
        "duree": 2,
        "depend_de": ["A"]
    },
    "E": {
        "description": "Développement du système d'authentification",
        "duree": 6,
        "depend_de": ["B", "D"]
    },
    "F": {
        "description": "Développement du front-end",
        "duree": 8,
        "depend_de": ["C", "D"]
    },
    "G": {
        "description": "Développement du back-end (API)",
        "duree": 10,
        "depend_de": ["B", "D"]
    },
    "H": {
        "description": "Intégration front-end / back-end",
        "duree": 5,
        "depend_de": ["E", "F", "G"]
    },
    "I": {
        "description": "Tests et corrections",
        "duree": 4,
        "depend_de": ["H"]
    },
    "J": {
        "description": "Déploiement et mise en ligne",
        "duree": 2,
        "depend_de": ["I"]
    },
    "K": {
        "description": "Rédaction du rapport final",
        "duree": 5,
        "depend_de": ["H"]
    },
    "L": {
        "description": "Préparation de la soutenance",
        "duree": 3,
        "depend_de": ["K"]
    }
}

if __name__ == '__main__':
    for nom, infos in taches.items():
        print(nom, infos)

def parser_taches(texte):
    """
    Retourne un tuple (taches_custom, erreurs) :
    - taches_custom : dictionnaire des tâches valides
    - erreurs : liste de messages d'erreur (vide si tout est OK)
    """
    taches_custom = {}
    erreurs = []

    for i, ligne in enumerate(texte.strip().split('\n'), start=1):
        if not ligne.strip():
            continue

        parts = ligne.split(';')

        if len(parts) < 3:
            erreurs.append(f"Ligne {i} : format invalide, il faut au moins nom;description;durée → \"{ligne.strip()}\"")
            continue

        nom = parts[0].strip()
        description = parts[1].strip()
        duree_str = parts[2].strip()

        if not nom:
            erreurs.append(f"Ligne {i} : le nom de la tâche est manquant")
            continue

        if nom in taches_custom:
            erreurs.append(f"Ligne {i} : la tâche \"{nom}\" est définie plusieurs fois")
            continue

        if not duree_str.isdigit():
            erreurs.append(f"Ligne {i} (tâche \"{nom}\") : durée manquante ou invalide (\"{duree_str}\")")
            continue

        duree = int(duree_str)

        if len(parts) > 3 and parts[3].strip():
            depend_de = [d.strip() for d in parts[3].split(',') if d.strip()]
        else:
            depend_de = []

        taches_custom[nom] = {
            "description": description,
            "duree": duree,
            "depend_de": depend_de
        }

    return taches_custom, erreurs