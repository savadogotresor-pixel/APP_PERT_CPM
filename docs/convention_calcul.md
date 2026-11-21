# Convention de calcul PERT/CPM

Ce document fixe les règles de calcul implémentées par `logic/pert.py` (moteur
canonique) et exposées par `logic/calcul.py` (façade utilisée par l'application
Flask). Il sert de référence unique : toute évolution du calcul doit mettre à
jour ce document et les tests associés.

## 1. Représentation du graphe

Le projet est modélisé en **AON** (*Activity-On-Node*, activité-sur-nœud) :

- chaque **tâche** est un nœud, identifié par une chaîne non vide et unique ;
- chaque **dépendance** `X → Y` signifie « la tâche Y ne peut commencer que
  lorsque X est terminée », soit `Y.depend_de` contient `X` ;
- plusieurs projets peuvent démarrer simultanément (plusieurs tâches sans
  prédécesseur) et plusieurs tâches peuvent terminer le projet (plusieurs
  tâches sans successeur) ;
- le graphe doit être **acacyclique** : un cycle est une erreur bloquante.

Structure attendue :

```python
taches = {
    "A": {"description": "...", "duree": 3, "depend_de": []},
    "B": {"description": "...", "duree": 4, "depend_de": ["A"]},
}
```

Seules les clés `duree` et `depend_de` sont nécessaires au calcul ;
`description` est utilisée par l'affichage.

## 2. Unité de temps et convention de fin

- Les durées et toutes les dates (ES, EF, LS, LF, marges) sont exprimées en
  **jours calendaires** (jours entiers ou décimaux ; les dimanches et jours
  fériés ne sont pas exclus).
- `ES` (*Early Start*) et `LS` (*Late Start*) sont des instants de **début**.
- `EF` (*Early Finish*) et `LF` (*Late Finish*) sont des instants de **fin
  exclusive** : une tâche de durée `d` débutant à `t` occupe l'intervalle
  `[t, t + d[` et se termine à `t + d`.
- Le diagramme de Gantt actuel convertit ces instants en dates avec
  `date.today() + timedelta(days=ES)` pour le début et
  `date.today() + timedelta(days=EF)` pour la fin. La fin affichée est donc la
  date du premier jour suivant la tâche (convention exclusive), et non le
  dernier jour travaillé.
- `D` est la **durée totale du projet** : l'instant de fin de la dernière
  tâche, mesuré depuis l'origine `t = 0` du projet.

## 3. Formules

Notation : `d` est la durée de la tâche courante, `Préd` ses prédécesseurs et
`Succ` ses successeurs.

### Passe avant (tri topologique)

```
ES = 0                                   si Préd est vide
ES = max(EF(p) pour p dans Préd)          sinon
EF = ES + d
D  = max(EF(t) pour t terminale)          (t sans successeur)
```

### Passe arrière (ordre topologique inversé)

```
LF = D                                   si Succ est vide (tâche terminale)
LF = min(LS(s) pour s dans Succ)          sinon
LS = LF - d
```

### Marges et criticité

```
MT (marge totale)  = LS - ES
ML (marge libre)   = min(ES(s) pour s dans Succ) - EF    si Succ non vide
ML (marge libre)   = D - EF                              si tâche terminale
critique           = MT ≈ 0
```

La **marge totale** est le retard maximal d'une tâche sans retarder la fin du
projet. La **marge libre** est le retard maximal sans retarder le début
d'aucune tâche successeure : c'est une borne plus stricte (`ML ≤ MT`). La
clé `marge` est un **alias de `marge_totale`** conservé pour compatibilité
ascendante avec `app.py`, l'export Excel et `generer_conseils`.

### Epsilon

`EPSILON = 1e-9`. Une tâche est critique si `abs(MT) <= EPSILON`. Les résidus
numériques de ce même ordre sont ramenés exactement à `0` dans les marges, afin
que des durées décimales (par exemple `0.1 + 0.2`) ne produisent pas de fausses
marges ni de faux `critique = False`.

## 4. Cas particuliers

- **Durée nulle** : acceptée. Une tâche de durée `0` est une tâche
  instantanée / un jalon : `EF = ES`; elle peut être critique et figure
  normalement dans les chemins critiques. Seules les durées négatives,
  booléennes, non numériques, non finies (`NaN`, infini) sont refusées.
- **Dépendance inexistante** : erreur bloquante explicite, par exemple
  `La tâche "A" dépend de "Z", qui n'existe pas`.
- **Cycle** : erreur bloquante explicite, par exemple
  `Dépendance circulaire détectée : A → B → A`. La détection utilise le tri
  topologique de Kahn itératif (aucune récursion, aucune boucle non bornée) ;
  le cycle est reconstruit et affiché dans le sens des dépendances, en
  commençant par l'identifiant le plus petit pour un message déterministe.
- **Identifiants** : une chaîne vide ou un doublon (y compris après suppression
  des espaces en bordure) est refusé, même si le parseur les filtre déjà.
- **Toutes les erreurs** sont collectées par `validate()` et jointes dans le
  message de `ValidationError` levée par `compute()`.
- **Plusieurs chemins critiques** : `PERTResult.chemins_critiques` énumère
  *tous* les chemins maximaux du sous-graphe des tâches critiques, depuis
  chaque source critique jusqu'à chaque puits critique. Chaque chemin suit
  l'ordre topologique. `liste_chemin_critique()` (façade historique) renvoie
  quant à elle la **liste plate** des tâches critiques, utilisée par
  `resultats.html` et les conseils.

## 5. Jeu de référence (démo 12 tâches)

Données (`logic/data.py`) : A(3) ; B(4)←A ; C(4)←A ; D(2)←A ; E(6)←B,D ;
F(8)←C,D ; G(10)←B,D ; H(5)←E,F,G ; I(4)←H ; J(2)←I ; K(5)←H ; L(3)←K.

**Durée totale attendue : 30 jours.**
**Chemin critique attendu : A → B → G → H → K → L** (unique).

| Tâche | ES | EF | LS | LF | MT | ML | Critique |
|:-----:|---:|---:|---:|---:|---:|---:|:--------:|
| A |  0 |  3 |  0 |  3 | 0 | 0 | oui |
| B |  3 |  7 |  3 |  7 | 0 | 0 | oui |
| C |  3 |  7 |  5 |  9 | 2 | 0 | non |
| D |  3 |  5 |  5 |  7 | 2 | 2 | non |
| E |  7 | 13 | 11 | 17 | 4 | 4 | non |
| F |  7 | 15 |  9 | 17 | 2 | 2 | non |
| G |  7 | 17 |  7 | 17 | 0 | 0 | oui |
| H | 17 | 22 | 17 | 22 | 0 | 0 | oui |
| I | 22 | 26 | 24 | 28 | 2 | 0 | non |
| J | 26 | 28 | 28 | 30 | 2 | 2 | non |
| K | 22 | 27 | 22 | 27 | 0 | 0 | oui |
| L | 27 | 30 | 27 | 30 | 0 | 0 | oui |

### Autres jeux de tests

- **Annexe A** : A(3), B(4)←A, C(2)←A, D(6)←B,C ⇒ `D = 13`, critiques
  A, B, D ; C a une marge totale de 2.
- **Branches parallèles** : A(2), B(5)←A, C(3)←A, D(1)←B,C ⇒ `D = 8`,
  marge totale de C = 2.
- **Deux chemins critiques** : A(2), B(3)←A, C(3)←A, D(2)←B,C ⇒ `D = 7`,
  chemins `[[A, B, D], [A, C, D]]`.
- **Multi-départs / multi-fins** : A(2), X(1), B(3)←A, C(2)←X ⇒ `D = 5`,
  marge totale de C = 2, seul chemin critique `A → B`.

## 6. Utilisation

```python
from logic.pert import compute, ValidationError

try:
    resultat = compute(taches)
except ValidationError as exc:
    print(exc)  # tous les messages d'erreur, un par ligne
else:
    print(resultat.duree_totale)
    print(resultat.chemins_critiques)
    print(resultat.resultats["A"]["marge_libre"])
```

`python logic/pert.py` affiche un tableau lisible pour la démo. Les tests se
lancent avec `python -m unittest discover -s tests -v` (unittest, stdlib,
aucune dépendance supplémentaire).
