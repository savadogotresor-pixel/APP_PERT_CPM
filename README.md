# Application PERT / CPM

Une application web **Flask** pour calculer et visualiser un planning de projet avec la méthode **PERT/CPM** : dates au plus tôt et au plus tard, marges, chemin critique, graphe de réseau, diagramme de Gantt et export Excel.

## Démo en ligne

🚀 [https://app-pert-cpm.onrender.com/](https://app-pert-cpm.onrender.com/)

## Documentation

- [Cahier des charges — PERT/CPM PRO (v1.0)](docs/Cahier_des_charges_PERT_CPM_PRO.docx) : spécifications fonctionnelles et techniques de l'évolution prévue de l'application.
- [Convention de calcul](docs/convention_calcul.md) : règles PERT/CPM, définition des marges et tolérance de 1e-9.
- [Audit](docs/audit.md) : état des lieux et points de vigilance de l'application.
- [Plan d'implémentation](docs/plan_implementation.md) : lots d'évolution et objectifs de qualité.

## Fonctionnalités

- **Démo prête à l'emploi** avec un projet de 12 tâches (`/demo`)
- **Saisie personnalisée** de vos propres tâches (`/saisie`)
- **Calculs PERT/CPM** avec le moteur canonique `logic/pert.py` (`logic/calcul.py` reste une façade de compatibilité) : tri topologique, passe avant (ES/EF), passe arrière (LS/LF), marge totale et marge libre, détection de **tous** les chemins critiques (marge nulle tolérée à 1e-9 près)
- **Validation des données** : dépendances inexistantes, durées invalides, doublons et cycles de dépendance détectés sans blocage de l'application
- **Tableau des résultats** : colonnes « Marge totale » et « Marge libre », ligne « Chemins critiques (n) : ... »
- **Visualisations** : graphe PERT interactif (vis.js) et diagramme de Gantt
- **Conseils d'optimisation** : tâches critiques, marges faibles, tâches flexibles et comparaison à une date de fin cible
- **Export Excel** (`.xlsx`, 3 onglets) : chiffres **recalculés côté serveur** depuis la saisie, avec protection contre l'injection de formules Excel ; les images du graphe et du Gantt sont des captures envoyées par le navigateur
- **Supervision** : `GET /health` renvoie `{"status": "ok"}` ; taille maximale des requêtes fixée à 8 Mo

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

> Le mode debug est désactivé par défaut : définissez `FLASK_DEBUG=1` pour l'activer en développement. Pour une mise en production, utilisez par exemple `gunicorn app:app` (déjà présent dans `requirements.txt`). `GET /health` permet de vérifier que le serveur répond.

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

Les durées sont des **entiers supérieurs ou égaux à 0** : les décimales et les valeurs négatives sont refusées. Une durée de `0` est acceptée : elle représente une tâche instantanée (jalon).

## Tests

Les tests `unittest` couvrent le moteur PERT/CPM, le parseur de saisie et l'export :

```bash
python -m unittest discover -s tests -v
```

Sous Windows, avec l'interpréteur Anaconda :

```bash
& 'C:\Users\ASUS\anaconda3\python.exe' -m unittest discover -s tests -v
```

## Structure du projet

```
.
├── app.py                 # Routes Flask et préparation des données
├── logic/
│   ├── calcul.py          # Façade de compatibilité autour du moteur canonique
│   ├── pert.py            # Moteur canonique : tri topologique, marges, tous les chemins critiques
│   └── data.py            # Jeu de tâches de démonstration et parser de saisie
├── tests/                 # Tests unitaires (unittest)
├── templates/             # Pages HTML (accueil, saisie, résultats, erreur)
├── static/                # Feuilles de style
└── requirements.txt       # Dépendances Python
```

## Stack technique

Python, Flask, Jinja2, vis.js (graphe), frappe-gantt (diagramme de Gantt), openpyxl (export Excel), html2canvas (captures utilisées par le PDF et l'Excel). Les images du graphe et du Gantt proviennent de **captures effectuées dans le navigateur** : elles ne sont pas générées par matplotlib/networkx.

## Auteur

Trésor0777 — [savadogotresor-pixel](https://github.com/savadogotresor-pixel)
