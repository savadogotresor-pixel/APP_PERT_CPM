# Application PERT / CPM

Une application web **Flask** pour calculer et visualiser un planning de projet avec la méthode **PERT/CPM** : dates au plus tôt et au plus tard, marges, chemin critique, graphe de réseau, diagramme de Gantt et export Excel.

## Démo en ligne

🚀 [https://app-pert-cpm.onrender.com/](https://app-pert-cpm.onrender.com/)

## Documentation

- [Cahier des charges — PERT/CPM PRO (v1.0)](docs/Cahier_des_charges_PERT_CPM_PRO.docx) : spécifications fonctionnelles et techniques de l'évolution prévue de l'application.

## Fonctionnalités

- **Démo prête à l'emploi** avec un projet de 12 tâches (`/demo`)
- **Saisie personnalisée** de vos propres tâches (`/saisie`)
- **Calculs PERT/CPM** : passe avant (ES/EF), passe arrière (LS/LF), marges et détection des tâches critiques
- **Validation des données** : dépendances inexistantes, durées invalides, doublons et cycles de dépendance
- **Visualisations** : graphe PERT interactif (vis.js) et diagramme de Gantt
- **Conseils d'optimisation** : tâches critiques, marges faibles, tâches flexibles et comparaison à une date de fin cible
- **Export Excel** (`.xlsx`) contenant le tableau des résultats, le graphe PERT et le diagramme Gantt

## Installation

```bash
git clone https://github.com/savadogotresor-pixel/APP_PERT_CPM.git
cd APP_PERT_CPM
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

## Lancement

```bash
python app.py
```

Ouvrez ensuite <http://127.0.0.1:5000> dans votre navigateur.

> `app.py` est lancé en mode `debug=True` pour le développement. Pour une mise en production, utilisez par exemple `gunicorn app:app` (déjà présent dans `requirements.txt`).

## Format de saisie des tâches

Une tâche par ligne, avec quatre champs séparés par des points-virgules :

```
nom;description;durée (jours);dépendances séparées par des virgules
```

Exemple :

```
A;Analyse des besoins;3;
B;Conception BDD;4;A
C;Maquettes UI;4;A
D;Développement API;10;B,C
```

Les dépendances sont facultatives : laissez le dernier champ vide pour une tâche sans prédécesseur.

## Structure du projet

```
.
├── app.py                 # Routes Flask et préparation des données
├── logic/
│   ├── calcul.py          # Passe avant/arrière, marges, chemin critique, conseils
│   └── data.py            # Jeu de tâches de démonstration et parser de saisie
├── templates/             # Pages HTML (accueil, saisie, résultats, erreur)
├── static/                # Feuilles de style
└── requirements.txt       # Dépendances Python
```

## Stack technique

Python, Flask, Jinja2,vis.js (graphe), frappe-gantt (diagramme de Gantt), openpyxl (export Excel), matplotlib / networkx (génération des images).

## Auteur

Trésor0777 — [savadogotresor-pixel](https://github.com/savadogotresor-pixel)
