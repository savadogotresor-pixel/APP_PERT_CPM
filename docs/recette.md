# Procès-verbal de recette — PERT/CPM PRO

**Date de recette :** 21 novembre 2026  
**Version applicative :** lots 0 à 6 livrés (admin en cours de finalisation, IA non implémentée)  
**Références :** cahier des charges v1.0 §12 (matrice de recette, lignes 427-464) et §16 (critères de réussite, lignes 556-565), `docs/convention_calcul.md`, `docs/documentation_technique.md`.  
**Environnement de test :** local (Python 3, SQLite `TestConfig` en mémoire) et instance Render <https://app-pert-cpm.onrender.com/> pour les contrôles manuels.  
**Commande de recette automatisée :** `python -m unittest discover -s tests -v` — **86 tests**.

| Rôle | Nom | Date | Signature |
|---|---|---|---|
| Commanditaire / responsable de projet | À compléter | | |
| Chef de projet | À compléter | | |
| Responsable des tests | À compléter | | |

> Légende des statuts : **Validé (automatisé)** = un test automatisé existe et couvre le résultat attendu ; **À valider manuellement** = aucun test automatisé ne couvre intégralement le cas, une vérification humaine est requise avant validation définitive. Les résultats ci-dessous constatent l'existence et la couverture des tests du dépôt ; la signature du PV reste nécessaire pour la validation officielle.

## 1. Matière du cahier — §12

| Domaine | Cas de test (cahier §12) | Méthode de vérification | Résultat attendu | Statut |
|---|---|---|---|---|
| Calcul | Chaîne simple `A → B → C` | **Automatisé** : `tests/test_pert.py::test_chaine_simple` (A=2, B=3, C=4). | Durée 9 jours, `ES/EF` = 0/2, 2/5, 5/9, chemin `[A, B, C]`. | **Validé (automatisé)** |
| Calcul | Deux branches parallèles de durées différentes | **Automatisé** : `tests/test_pert.py::test_branches_paralleles` (A=2 ; B=5 ; C=3 ; D=1 après B et C). | Durée 8 jours (branche longue A-B-D) ; C non critique, marges totale et libre = 2. | **Validé (automatisé)** |
| Calcul | Deux chemins critiques de même durée | **Automatisé** : `tests/test_pert.py::test_deux_chemins_critiques`, `test_multi_chemins_avec_departs_et_fins_distincts` et `tests/test_rapport.py::test_chemins_critiques_multiples`. | Durée 7 ; les deux chemins `[A, B, D]` et `[A, C, D]` sont identifiés et repris dans le PDF. | **Validé (automatisé)** |
| Validation | Prédécesseur inexistant | **Automatisé** : `tests/test_pert.py::test_predecesseur_inexistant` et `tests/test_app.py::test_duree_negative_et_predecesseur_inconnu`. | Calcul bloqué ; message explicite contenant le code inconnu et « n'existe pas », affiché sans erreur 500. | **Validé (automatisé)** |
| Validation | Cycle `A → B → A` | **Automatisé** : `tests/test_pert.py::test_cycle_leve_erreur_rapidement`, `test_cycle_avec_tache_en_aval`, `test_auto_dependance_est_un_cycle` ; `tests/test_app.py::test_cycle_sans_blocage` ; `tests/test_auth_projects.py::test_taches_et_cycle`. | `ValidationError` « Dépendance circulaire détectée : A → B → A », détection < 2 s, aucun blocage du serveur, création refusée. | **Validé (automatisé)** |
| Validation | Durée négative | **Automatisé** : `tests/test_pert.py::test_duree_negative`, `tests/test_parser.py::test_duree_negative`, `tests/test_import_export.py::test_erreur_duree_negative`, `tests/test_app.py::test_duree_negative_et_predecesseur_inconnu`. | Saisie rejetée avec mention « négative » ; durée nulle acceptée comme jalon (`test_duree_nulle_est_valide`). | **Validé (automatisé)** |
| Marge | Tâche non critique : marge totale positive et cohérente | **Automatisé** : `tests/test_pert.py::test_branches_paralleles` (C : MT = ML = 2), `test_demo_12_taches` (tableaux complets MT/ML des 12 tâches) et `test_epsilon_absorbe_les_residus_flottants`. | MT = LS − ES = LF − EF > 0 pour une tâche non critique ; criticité tolerant 1e-9. | **Validé (automatisé)** |
| Visualisation | Modification d'une durée → tableau, graphe et Gantt mis à jour | **Manuel** dans le navigateur : modifier la durée d'une tâche d'un projet, lancer le recalcul, comparer le tableau, le réseau PERT et le Gantt. Les tests `tests/test_auth_projects.py::test_taches_et_cycle` et `tests/test_app.py::test_saisie_valide` couvrent déjà le **recalcul et le tableau** (5 → 7 jours ; rendu HTML) mais pas le rendu graphique. | Les trois vues affichent la même durée totale, les mêmes dates et la même criticité ; les éléments critiques restent colorés. | **À valider manuellement** |
| Sécurité | Accès au projet d'un autre utilisateur | **Automatisé** : `tests/test_auth_projects.py::test_projet_crud_et_acces` (second compte → HTTP 403) et `tests/test_models.py::test_user_can_access`. | Accès refusé (403) en lecture comme en modification ; le propriétaire, l'admin et les `ProjectAccess` explicites restent autorisés. | **Validé (automatisé)** |
| Export | Génération d'un rapport PDF : fichier lisible avec données et graphiques exacts | **Automatisé** : `tests/test_rapport.py::test_generation_pdf_structure_et_textes`, `test_chemins_critiques_multiples`, `test_caracteres_speciaux_sans_erreur`, `test_resultat_manquant_leve_value_error` ; **contrôle visuel complémentaire recommandé** pour la lisibilité des graphiques. | `%PDF` valide, texte extractible (date, chemin `A -> B -> C`, deux chemins critiques), tableau et graphiques générés depuis les résultats du moteur ; aucun chiffre manquant. | **Validé (automatisé)** |
| Persistance | Redémarrage du serveur : données toujours disponibles | **Manuel** : créer projet + tâches sur l'instance déployée (PostgreSQL) ou locale (SQLite), redémarrer le service Render / `python app.py`, rouvrir le projet. Les tests utilisent une base en mémoire et ne couvrent pas la persistance réelle. | Projet, tâches, dépendances et durée recalculée identiques après redémarrage ; `/health` répond `{"status":"ok"}`. | **À valider manuellement** |

### Conditions minimales de recette (cahier §12.1)

| Condition | Méthode | Statut |
|---|---|---|
| Aucun défaut bloquant ne subsiste sur les fonctions essentielles | Constater à l'issue de la campagne manuelle et des 86 tests. | **À valider manuellement** |
| Les calculs sont validés sur plusieurs jeux de données connus et contrôlés manuellement | Jeux de référence : chaîne, branches, deux chemins critiques, démo 12 tâches (D = 30), Annexe A (`tests/test_pert.py`, `tests/test_app.py`). | **Validé (automatisé)** |
| Les tâches, dépendances et résultats sont persistés correctement | Tests de création de tâches/dépendances (`test_models.py`, `test_auth_projects.py`) ; persistance réelle à confirmer en environnement déployé. | **À valider manuellement** |
| Les accès non autorisés sont refusés | `tests/test_auth_projects.py::test_projet_crud_et_acces`, `tests/test_models.py::test_user_can_access`. | **Validé (automatisé)** |
| Les exports correspondent aux résultats affichés | `tests/test_app.py::test_export_excel_recalcule_cote_serveur` (valeurs farfelues ignorées), `tests/test_import_export.py`, `tests/test_rapport.py`. | **Validé (automatisé)** |
| Le déploiement est accessible et la base de production est persistante | Vérification Render `/health` + redémarrage (grille §2.5 de `docs/deploiement.md`). | **À valider manuellement** |

## 2. Critères de réussite — cahier §16

| Critère (§16) | Méthode de vérification | Statut |
|---|---|---|
| Un utilisateur peut créer un compte et gérer ses propres projets | `tests/test_auth_projects.py::test_register_login_logout`, `test_login_par_email_et_mauvais_mdp`, `test_unicite_inscription`, `test_projet_crud_et_acces` ; `tests/test_models.py::test_mot_de_passe_hache`. | **Validé (automatisé)** |
| Un projet peut contenir plusieurs tâches et dépendances cohérentes | `tests/test_models.py::test_creation_projet_taches_dependances`, `test_project_to_taches`, `test_cascade_suppression_projet` ; `tests/test_auth_projects.py::test_taches_et_cycle`. | **Validé (automatisé)** |
| Le moteur calcule correctement ES, EF, LS, LF, marges et chemin critique | `tests/test_pert.py` (18 tests), dont `test_chaine_simple`, `test_branches_paralleles`, `test_deux_chemins_critiques`, `test_demo_12_taches`. | **Validé (automatisé)** |
| Les graphes PERT et Gantt reflètent fidèlement les données du projet | Contrôle visuel dans le navigateur après recalcul ; les tests HTTP vérifient le rendu HTML mais pas les nœuds/barres des graphiques. | **À valider manuellement** |
| Les erreurs de saisie et les cycles sont détectés et expliqués | `tests/test_pert.py::test_predecesseur_inexistant`, `test_cycle_leve_erreur_rapidement` ; `tests/test_app.py::test_duree_negative_et_predecesseur_inconnu` ; `tests/test_parser.py` ; `tests/test_import_export.py`. | **Validé (automatisé)** |
| Les rapports exportés sont lisibles et cohérents avec les résultats affichés | `tests/test_rapport.py` (structure PDF, texte extractible, chemins multiples, échappement XML) et `tests/test_import_export.py` (CSV/XLSX). Contrôle visuel recommandé en complément. | **Validé (automatisé)** |
| Les données sont conservées après redémarrage ou redéploiement | Procédure manuelle sur l'instance déployée avec base PostgreSQL (voir §1, dernier cas). | **À valider manuellement** |
| L'application est utilisable sur les principaux navigateurs et formats d'écran | Test manuel Chrome, Firefox, Edge ; ordinateur, tablette, smartphone ; navigation clavier et contrastes. | **À valider manuellement** |
| La documentation permet à un autre développeur d'installer et de maintenir la solution | Relecture indépendante de `docs/documentation_technique.md`, `docs/deploiement.md`, `docs/sauvegarde.md` et du `README.md` par une personne n'ayant pas écrit le code. | **À valider manuellement** |

## 3. Synthèse

- **Cas du cahier §12 :** 9 validés (automatisé), 2 à valider manuellement (actualisation des graphiques, persistance après redémarrage).
- **Critères §16 :** 6 validés (automatisé), 3 à valider manuellement (fidélité visuelle des graphiques, persistance après redémarrage, navigateurs/formats), plus la relecture documentaire à faire valider.
- **Conditions §12.1 :** 3 validées (automatisé), 3 à valider manuellement.
- La campagne automatisée est reproductible avec une seule commande ; elle ne remplace ni le contrôle visuel des visualisations et des exports, ni la vérification de la persistance sur l'infrastructure de production.

## 4. Réserves

1. **IA non implémentée.** Les exigences du chapitre 19 du cahier (AI Project Manager : assistant conversationnel, génération WBS, optimisation assistée, analyse de risques, actions en langage naturel) ne sont pas livrées. Les lots IA-1 à IA-4 du `docs/plan_implementation.md` restent conditionnels ; aucune recette IA n'est donc incluse dans ce PV.
2. **Nom définitif à choisir.** Le porteur n'a pas encore arbitré entre les pistes du plan d'implémentation (PERT Manager, ChronoPro, Planif'PERT, CritiPath…). Le nom provisoire « Application PERT/CPM » et l'URL `app-pert-cpm.onrender.com` sont utilisés ; le choix retenu impactera l'interface, les exports et le PV de recette.
3. **Unité de temps : jours calendaires.** La v1 utilise des jours entiers calendaires (`EF = ES + d`, fin exclusive, week-ends et jours fériés inclus). Cette convention doit être **confirmée officiellement** ; le passage à des jours ouvrés ou à des heures ferait l'objet d'une évolution distincte et invaliderait les jeux de référence.
4. **Administration en cours de finalisation.** Le blueprint `/admin` est présent dans le dépôt mais son câblage est encore en cours ; les contrôles manuels `/admin/*` doivent être rejoués après livraison définitive.
5. **Points de vigilance complémentaires.** Deux routes historiques restent exemptées de CSRF ; la limitation des tentatives de connexion n'est pas implémentée ; les migrations de schéma ne sont pas versionnées avec un outil dédié. Ces points ne bloquent pas la recette des lots 0 à 6 mais doivent être traités avant d'élargir le périmètre (IA notamment).
