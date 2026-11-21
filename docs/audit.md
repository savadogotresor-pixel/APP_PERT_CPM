# Audit technique — Application PERT/CPM (version actuelle)

**Date :** 21 novembre 2026  
**Périmètre :** dépôt local `APP_PERT_CPM` (miroir de `github.com/savadogotresor-pixel/APP_PERT_CPM`), code correspondant à l'application déployée sur <https://app-pert-cpm.onrender.com/>.  
**Référence :** `docs/Cahier_des_charges_PERT_CPM_PRO.docx` (v1.0, 21/11/2026), ci-après « le cahier ».  
**Méthode :** relecture statique de `app.py`, `logic/calcul.py`, `logic/data.py`, `templates/`, `static/` et `requirements.txt`, puis confrontation aux exigences du cahier. Aucun test dynamique, aucun test d'intrusion et aucune inspection de l'environnement Render n'ont été effectués.

## 1. Synthèse

L'application est un **prototype fonctionnel et démontrable**, mais **mono-utilisateur, sans persistance, sans tests et sans sécurité**. Elle couvre correctement le noyau CPM déterministe et offre déjà une interface en français, deux visualisations et deux exports. En revanche, les chapitres authentification, projets, optimisation et IA du cahier sont entièrement absents.

Sur les 62 exigences identifiées par identifiant dans le cahier, environ **12 sont satisfaites ou partiellement satisfaites, soit une couverture estimée à 15-20 %**. Le détail figure au §4.

> **Priorité absolue :** deux défauts peuvent bloquer ou fausser un résultat en production — la boucle infinie sur cycle (`logic/calcul.py:12` et `:57`) et la confiance accordée aux données envoyées par le navigateur lors de l'export Excel (`app.py:151-182`).

## 2. Forces

| Force | Preuve dans le code |
|---|---|
| Moteur CPM de base correct | `passe_avant` / `passe_arriere` (`logic/calcul.py:4-74`) appliquent les formules du cahier (§6.2, lignes 289-291). Le jeu de démonstration donne bien **D = 30 jours** et le chemin **A → B → G → H → K → L**. |
| Validation des dépendances déjà écrite | `valider_taches` (`logic/calcul.py:121-163`) détecte les prédécesseurs inexistants et les cycles avec un message explicite ; elle est appelée sur la saisie personnalisée (`app.py:108-111`). |
| Démarrage immédiat | Routes `/`, `/demo`, `/saisie` (`app.py:60-89`) ; jeu de 12 tâches prêt à l'emploi (`logic/data.py:1-62`) ; démo publique en ligne. |
| Interface soignée et francophone | Navigation, tableaux, blocs d'aide repliables et conseils colorés (`templates/*.html`, `static/resultats.css`). |
| Visualisations présentes | Graphe PERT interactif vis-network et Gantt Frappe (`templates/resultats.html:132-168`), avec mise en rouge des tâches critiques. |
| Exports déjà livrables | PDF côté client et XLSX à trois onglets (`app.py:151-192`, `templates/resultats.html:170-250`). |
| Base de code compacte et lisible | Quatre routes, deux modules métier, quatre gabarits : la refonte peut être incrémentale, sans réécriture (recommandation Annexe B du cahier). |

## 3. Faiblesses constatées

### 3.1 Calcul et validation

- **Boucles potentielles infinies.** `passe_avant` (`logic/calcul.py:12`) et `passe_arriere` (`:57`) tournent tant que toutes les tâches n'ont pas de résultat. Si un cycle parvient jusqu'au calcul — par exemple via `/demo`, qui n'appelle pas `valider_taches` (`app.py:65-84`), ou via une évolution future du parser — le serveur ne termine jamais. Le cahier exige au contraire un blocage explicite (ligne 291).
- **Marge libre absente.** `calculer_marges` (`logic/calcul.py:83-103`) ne renvoie que `marge = LS − ES`. CALC-03 n'est pas couvert.
- **Chemins critiques non identifiés.** `liste_chemin_critique` (`:106-108`) renvoie une liste plate de noms critique, sans ordre de chemin ni regroupement quand plusieurs chemins existent (CALC-05).
- **Criticité sans tolérance numérique.** `critique = (marge == 0)` (`:95`) cascadera mal avec des durées décimales ou des arrondis futurs (§6.2, ligne 291).
- **Durée du projet.** `D = max(EF de toutes les tâches)` (`app.py:71`, `:117`) au lieu du maximum des EF des tâches terminales (ligne 289). Les deux coïncident sur le jeu de démonstration, mais la convention n'est ni documentée ni testée.
- **Validation incomplète.** Aucun contrôle de durée négative dans `parser_taches` (`logic/data.py:68-116`) ; la durée nulle est acceptée sans traitement explicite (TASK-04). `valider_taches` ne vérifie pas l'unicité des identifiants — seul le parser le fait, pour la saisie texte (TASK-03).

### 3.2 Parser de saisie (`logic/data.py:68-116`)

- `duree_str.isdigit()` (`:99`) accepte le caractère `²` mais `int('²')` lève une `ValueError` : **réponse 500** sur une saisie pourtant considérée valide par le test.
- Séparateur de prédécesseurs **virgule uniquement** (`:106`) ; l'Annexe A du cahier (ligne 609) recommande `;` (ex. `B;C`) et exige que le séparateur soit documenté.
- Seul le nombre de champs minimum est vérifié (`:83`) : les champs surnuméraires sont silencieusement ignorés.
- Aucun aperçu ni rapport d'erreurs par ligne avant confirmation (TASK-06).

### 3.3 Sécurité

| Constat | Localisation | Impact |
|---|---|---|
| `debug=True` en permanence | `app.py:195-196` | Traceback et console interactive exposés en production. |
| Aucune `SECRET_KEY`, aucune session, aucune authentification | `app.py:12` | Aucune protection des pages, aucun contrôle de propriété (AUTH-01 à AUTH-05, §11). |
| Aucune protection CSRF | formulaire `templates/saisie.html:20`, POST JSON `resultats.html:228` | Non conforme au §11 (ligne 421). |
| **XSS DOM dans le popup Gantt** | `templates/resultats.html:157-166` : `task.name` est interpolé en HTML brut ; or `name` contient la description saisie par l'utilisateur (`app.py:41`) | Exécution de script via une description du type `<img onerror=...>`. |
| **Export Excel non fiable** | `app.py:153-157` : `taches`, `resultats`, `duree_totale` et `chemin_critique` sont pris tels quels dans le JSON du navigateur | Falsification possible des résultats exportés ; l'export ne reflète pas nécessairement le calcul serveur (EXP-01, §12). |
| **Injection de formules Excel** | `app.py:167-169` : `nom` et `description` écrits directement en cellule | Une valeur commençant par `=`, `+`, `-` ou `@` est interprétée comme une formule. |
| Fichiers PNG temporaires jamais supprimés | `app.py:51-57` (`delete=False`, aucun `unlink`) | Accumulation de fichiers sur l'hébergement. |
| Aucune limite de taille de requête | `app.py:12` (pas de `MAX_CONTENT_LENGTH`) | Requêtes arbitrairement grandes, vecteur de déni de service. |
| CDN non épinglés pour vis-network | `templates/resultats.html:132` (`unpkg.com/vis-network/...` sans version) | Rupture silencieuse en cas de changement amont ; les autres CDN sont versionnés. |

### 3.4 Interface et expérience

- `#graphe` est fixé à **900 × 500 px** (`static/resultats.css:3-7`) : débordement sur mobile/tablette, contraire à l'exigence responsive (§7, §8.1).
- Aucun contrôle de **zoom Gantt** (VIS-05) : seul le défilement horizontal du conteneur est disponible (`resultats.css:9-12`, `resultats.html:150-167`).
- **Blocs d'aide masqués par erreur** : les trois `<details class="aide">` sont conditionnés par `{% if not conseils %}` (`templates/resultats.html:49`, `:96`, `:111`). Or `/resultats_custom` transmet toujours des conseils (`app.py:131-134`, `logic/calcul.py:166-261`) : les explications ne s'affichent que sur la démo.
- Export PDF fragile : capture raster via `html2canvas`, découpage en pages approximatif et dépendance à trois CDN (`resultats.html:172-211`). Texte non sélectionnable, résultats parfois tronqués sur les grands projets (EXP-02/03).
- VIS-03 (détail d'une tâche sur sélection) n'est pas implémenté pour le graphe PERT.

### 3.5 Dépendances, déploiement et qualité

- `requirements.txt` contient de nombreuses bibliothèques **inutilisées** : Flask-Login (`:8`), Flask-SQLAlchemy / SQLAlchemy (`:9`, `:25`), matplotlib (`:16`), networkx (`:17`), numpy (`:18`), contourpy, cycler, fonttools, kiwisolver, pyparsing, python-dateutil, six… Elles sont installées sans être importées.
- `gunicorn` n'est **pas versionné** (`:28`) ; il n'existe **ni `Procfile` ni `render.yaml`** : la configuration de production n'est pas reproductible depuis le dépôt.
- **Aucun test** (ni unitaire, ni intégration), aucune CI, aucun module de journalisation, aucun gestionnaire d'erreur HTTP. Le cahier exige des tests et des jeux de référence (§12, Annexe B).
- Structure plate (`app.py` monolithique) : l'organisation proposée en `app/`, `models/`, `routes/`, `services/`, `tests/` (§9.1) n'est pas en place.

## 4. Tableau d'écart avec le cahier des charges

Légende : ✅ conforme · ⚠️ partiel · ❌ absent.

| Domaine | Identifiants du cahier | Couverture | Écart principal | Priorité cahier |
|---|---|---|---|---|
| Authentification | AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05 | ❌ 0/5 | Aucun compte, aucune session, aucun hachage, aucune page protégée | Haute à Critique |
| Projets | PROJ-01, PROJ-02, PROJ-03, PROJ-04, PROJ-05 | ❌ 0/5 | Pas d'entité projet, pas de tableau de bord, aucun stockage | Haute (PROJ-04 : Moyenne) |
| Tâches | TASK-01, TASK-03, TASK-04, TASK-05, TASK-07 | ⚠️ ~3/7 | Saisie texte seulement ; pas de CRUD, pas d'import (TASK-02, TASK-06 ❌) ; durée nulle non traitée ; cycle détecté mais non bloqué sur `/demo` | Critique / Haute |
| Calcul | CALC-01, CALC-02, CALC-04, CALC-06 | ✅ 4/7 | Passe avant/arrière et tableau corrects | Critique / Haute |
| Calcul | CALC-03, CALC-05, CALC-07 | ⚠️/❌ | Marge libre absente ; un seul chemin plat ; validation contournable et boucles infinies | Critique / Haute |
| Visualisation | VIS-01, VIS-02, VIS-04, VIS-06 | ✅ 4/6 | Graphe et Gantt générés, éléments critiques colorés, recalculés à chaque POST | Haute |
| Visualisation | VIS-03, VIS-05 | ❌/⚠️ | Pas de fiche tâche au clic ; pas de zoom Gantt ; `#graphe` non responsive | Moyenne |
| Optimisation | OPT-01, OPT-02, OPT-03, OPT-04, OPT-05 | ❌ 0/5 | Les « conseils » sont des règles d'affichage, pas des simulations ; aucun scénario, aucune comparaison | Moyenne à Haute |
| Exports | EXP-01, EXP-02, EXP-03 | ⚠️ ~1,5/3 | XLSX et PDF existent mais sont générés depuis le navigateur ; images de graphiques seulement ; EXP-04 (date/paramètres) ❌ | Moyenne / Haute |
| IA — assistant | IA-ASS-01, IA-ASS-02, IA-ASS-03, IA-ASS-04, IA-ASS-05, IA-ASS-06 | ❌ 0/6 | Aucun service IA, aucune interface conversationnelle | Haute (IA-ASS-06 : Critique) |
| IA — génération | IA-GEN-01, IA-GEN-02, IA-GEN-03, IA-GEN-04, IA-GEN-05, IA-GEN-06 | ❌ 0/6 | Aucune génération WBS, aucune prévisualisation | Haute / Critique |
| IA — optimisation | IA-OPT-01, IA-OPT-02, IA-OPT-03, IA-OPT-04, IA-OPT-05, IA-OPT-06 | ❌ 0/6 | Aucun scénario assisté, aucun recalcul de simulation | Critique / Haute |
| IA — risques | IA-RISK-01, IA-RISK-02, IA-RISK-03, IA-RISK-04, IA-RISK-05 | ❌ 0/5 | Aucune analyse de risque (les conseils actuels ne sont pas des risques IA) | Moyenne à Critique |
| Non fonctionnel | Responsive, tests, sécurité, journalisation, sauvegarde (§7, §11, §12) | ❌ | Voir §3.3 à §3.5 | — |

## 5. Risques techniques et priorités

| Priorité | Risque | Conséquence | Mesure proposée | Lots du plan |
|---|---|---|---|---|
| **P0** | Cycle atteignant le moteur de calcul | Boucle infinie, serveur bloqué | Remplacer les `while` par un parcours topologique avec détection ; appeler la validation sur **toutes** les entrées | LOT 0 |
| **P0** | XSS via description dans le popup Gantt | Exécution de code dans le navigateur d'un utilisateur | Échappement DOM (`textContent`) au lieu d'interpolation HTML | LOT 1 |
| **P0** | Export Excel piloté par le client + formules Excel | Résultats falsifiés, formules exécutées à l'ouverture | Recalculer côté serveur ; préfixer les cellules texte ; supprimer les PNG temporaires | LOT 1, LOT 6 |
| **P0** | `debug=True`, pas de `SECRET_KEY`, pas de limites | Exposition de traces, sessions non protégées, DoS | Config par environnement, `debug=False`, clé secrète, `MAX_CONTENT_LENGTH`, gestionnaires d'erreurs | LOT 1 |
| **P1** | `isdigit()` sur `²` | HTTP 500 sur une saisie anodine | Validation numérique robuste (`int()` sous `try`) | LOT 0 / LOT 1 |
| **P1** | Marge libre et chemins multiples absents | Résultats incomplets ou trompeurs | Étendre le moteur certifié et ses jeux de référence | LOT 0 |
| **P1** | Absence de persistance et de contrôle d'accès | Perte des données, accès croisés entre utilisateurs | Modèles, migrations, ORM, vérification serveur de la propriété | LOT 2, LOT 3 |
| **P1** | Aucune protection CSRF | Modification non sollicitée | Jetons CSRF sur tous les formulaires et POST | LOT 1 |
| **P2** | Interface non responsive, aide masquée, pas de zoom | Utilisabilité réduite sur mobile | CSS fluide, correction de la condition Jinja, contrôles Gantt | LOT 3, LOT 4 |
| **P2** | CDN non épinglés / génération PDF client | Casse silencieuse, PDF peu fiable | Versions figées, exports serveur | LOT 1, LOT 6 |
| **P2** | Dépendances inutilisées, pas de configuration Render | Image de production lourde et non reproductible | Nettoyage de `requirements.txt`, `Procfile`/`render.yaml`, versions figées | LOT 1 |
| **P3** | Périmètre IA et optimisation non cadré | Dérive des délais, coûts API imprévus | Lots IA séparés, conditionnés aux jalons de sécurité et de recette | LOT 5, IA-1 à IA-4 |

## 6. Recommandation d'enchaînement

1. **LOT 0 — moteur certifié + tests** (en cours par un autre agent ; voir `docs/plan_implementation.md`). Aucun autre lot ne doit reposer sur un moteur non testé.
2. **LOT 1 — sécurisation minimale** avant toute nouvelle fonctionnalité exposée.
3. **LOT 2 puis LOT 3 — persistance, comptes, CRUD projets/tâches**.
4. **LOT 4 à LOT 6 — visualisations, optimisation, exports serveur**.
5. **LOT 7 — recette et déploiement**, puis seulement les **lots IA-1 à IA-4**.

Cet ordre reprend les recommandations de l'Annexe B du cahier (lignes 611-616) : auditer puis stabiliser, isoler le moteur, écrire les tests avant les fonctions avancées, et traiter les évolutions en lots séparés.
