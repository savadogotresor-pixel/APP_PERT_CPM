import re


# Protections contre une saisie accidentellement (ou volontairement) énorme.
MAX_CARACTERES = 100_000
MAX_LIGNES = 2_000

# Seuls les chiffres ASCII sont acceptés : "²".isdigit() est vrai mais int("²") échoue.
_DUREE_ASCII = re.compile(r"^[0-9]+$")


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
    Analyse une saisie de tâches et retourne ``(taches_custom, erreurs)``.

    Format accepté, une tâche par ligne ::

        nom;description;durée[;pred1,pred2]
        nom;description;durée;pred1;pred2

    Les deux séparateurs de prédécesseurs sont acceptés : la virgule
    (``A;Analyse;3;B,C``) et le point-virgule, qui produit alors des champs
    supplémentaires (``A;Analyse;3;B;C``). Une ligne sans prédécesseur peut
    s'écrire ``A;Analyse;3`` ou ``A;Analyse;3;`` (champ 4 volontairement vide).
    Les lignes vides sont ignorées ; les noms sont nettoyés et doivent être
    uniques ; la description est facultative ; les prédécesseurs sont nettoyés.

    La durée doit être un entier décimal ASCII (regex ``^[0-9]+$``), donc ni
    négative, ni décimale, ni un chiffre non ASCII comme ``²``. Les durées
    nulles sont acceptées : convention documentée, une tâche de durée 0 est
    un jalon qui ne bloque pas le calcul. C'est une information, pas une
    erreur : aucun message d'erreur n'est ajouté dans ce cas afin de ne pas
    empêcher le calcul PERT/CPM.

    Toutes les erreurs sont accumulées avec leur numéro de ligne (les lignes
    vides comptent dans la numérotation). La taille de la saisie est limitée
    pour éviter un traitement démesuré.
    """
    taches_custom = {}
    erreurs = []

    if not isinstance(texte, str):
        return taches_custom, [
            "Ligne 1 : la saisie doit être une chaîne de caractères "
            f"(type reçu : {type(texte).__name__})"
        ]

    if len(texte) > MAX_CARACTERES:
        return taches_custom, [
            f"Ligne 1 : saisie trop longue ({len(texte)} caractères, "
            f"maximum {MAX_CARACTERES} caractères)"
        ]

    lignes = texte.splitlines()
    if len(lignes) > MAX_LIGNES:
        return taches_custom, [
            f"Ligne {MAX_LIGNES + 1} : trop de lignes ({len(lignes)}, "
            f"maximum {MAX_LIGNES})"
        ]

    for i, ligne in enumerate(lignes, start=1):
        if not ligne.strip():
            continue

        contenu = ligne.strip()
        parts = ligne.split(';')

        if len(parts) < 3:
            erreurs.append(f"Ligne {i} : format invalide, il faut au moins nom;description;durée → \"{contenu}\"")
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

        if not duree_str:
            erreurs.append(f"Ligne {i} (tâche \"{nom}\") : durée manquante")
            continue

        if duree_str.startswith('-') and _DUREE_ASCII.fullmatch(duree_str[1:]):
            erreurs.append(
                f"Ligne {i} (tâche \"{nom}\") : durée négative interdite "
                f"(\"{duree_str}\")"
            )
            continue

        if not _DUREE_ASCII.fullmatch(duree_str):
            erreurs.append(
                f"Ligne {i} (tâche \"{nom}\") : durée invalide (\"{duree_str}\") "
                ": un entier décimal (zéro ou plus) est attendu"
            )
            continue

        duree = int(duree_str)

        depend_de = []
        for index, champ in enumerate(parts[3:], start=4):
            valeur = champ.strip()
            if not valeur:
                # `A;Analyse;3;` : champ 4 vide et aucun autre champ = pas de prédécesseur.
                if index == 4 and len(parts) == 4:
                    continue
                erreurs.append(
                    f"Ligne {i} (tâche \"{nom}\") : champ {index} vide inattendu "
                    "après la durée"
                )
                continue
            for dep in valeur.split(','):
                dep = dep.strip()
                if dep:
                    depend_de.append(dep)

        taches_custom[nom] = {
            "description": description,
            "duree": duree,
            "depend_de": depend_de
        }

    return taches_custom, erreurs