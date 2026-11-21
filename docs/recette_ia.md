# Procès-verbal de recette — module IA (lot IA-4)

**Date :** 21 novembre 2026  
**Périmètre :** chapitre 19 du cahier des charges, matrice §19.11, lots IA-1 à IA-4.  
**Références :** `docs/recette.md` (recette générale), `docs/documentation_technique.md` §19.  
**Commande de rejeu :** `python -m unittest discover -s tests -v`.

## Matrice de recette IA (§19.11)

| # | Cas (§19.11) | Résultat attendu | Méthode | Statut |
|---|---|---|---|---|
| 1 | Question sur la durée du projet | La réponse reprend exactement la durée calculée par le moteur PERT/CPM, sans chiffre inventé. | Test automatisé : `tests/test_ia_routes.py`, `tests/test_ia_service.py` (réponses vérifiées, repli déterministe). | ✅ Vert |
| 2 | Demande de création de projet (WBS) | Une WBS est prévisualisée ; aucune donnée n'est enregistrée avant confirmation explicite. | Test automatisé : `tests/test_wbs_routes.py` (aperçu sans écriture, `confirmer=oui` requis, audit `IA_WBS_CREATION`). | ✅ Vert |
| 3 | Prédécesseur invalide proposé | La validation serveur rejette la proposition et explique l'erreur (code inconnu, cycle, auto-dépendance). | Test automatisé : `tests/test_actions_ia.py`, complété par `tests/test_wbs_routes.py` (contrôle via `logic.pert.compute`). | ✅ Vert |
| 4 | Demande de modification | Les changements sont affichés avant confirmation, puis exécutés et journalisés après confirmation explicite (`CONFIRMER`) ; le planning est recalculé. | Test automatisé : `tests/test_actions_ia.py` ; contrôle manuel du parcours HTTP `/projects/<id>/actions/assister` (prévisualisation → `SUPPRIMER`/`CONFIRMER` → `IA_ACTION_EXECUTEE`). | ✅ Vert |
| 5 | Simulation d'optimisation | Chaque scénario est recalculé par le moteur et comparé au planning initial ; rien n'est appliqué. | Test automatisé : `tests/test_optimisation_assistee.py`, `tests/test_optimisation_assistee_routes.py`, `tests/test_optimisation.py`. | ✅ Vert |
| 6 | Projet non autorisé | Aucune donnée du projet n'est communiquée : 403 avant tout calcul, aucune alerte, aucun audit, aucun scénario. | Test automatisé : `tests/test_auth_projects.py`, `tests/test_optimisation_assistee_routes.py` ; contrôle manuel des trois routes IA-4 avec un compte intrus. | ✅ Vert |
| 7 | Fournisseur IA indisponible | Message explicite et mode sans IA ; les fonctions classiques (calcul, risques par règles, rapport, optimisations) restent disponibles. | Test automatisé : `tests/test_ia_service.py`, `tests/test_risques.py`, `tests/test_rapport_intelligent.py` (`IA_ENABLED=0`). | ✅ Vert |
| 8 | Réponse incertaine | L'assistant signale ses limites ; une supposition n'est jamais présentée comme un fait (chiffres vérifiés, avertissements, `appreciation_ia=None` en cas de doute). | Test automatisé : `tests/test_ia_service.py`, `tests/test_rapport_intelligent.py` ; contrôle manuel du bandeau « Niveau indicatif ». | ✅ Vert |
| 9 | Risques et actions contrôlées (IA-4) | Alertes non dupliquées, origine « Règle calculée » distincte de « Appréciation IA », statuts commentables, suppressions protégées par `SUPPRIMER`, rapports sourcés « moteur PERT/CPM ». | Test automatisé : `tests/test_risques.py`, `tests/test_actions_ia.py`, `tests/test_rapport_intelligent.py` ; contrôle manuel des trois pages et des audits `IA_RISQUE_STATUT`, `IA_ACTION_EXECUTEE`, `IA_RAPPORT_INTELLIGENT`. | ✅ Vert |

## Vérifications transversales effectuées

- **Sécurité** : `@login_required` sur les trois routes IA-4 ; `Project.query.get_or_404` puis `user_can_access()` avec `abort(403)` avant tout calcul ou envoi de données ; jetons CSRF dans les trois gabarits.
- **Traçabilité** : les audits IA-4 ne contiennent que des identifiants et des types (`project_id`, `alerte_id`, `statut`, `type`, `cible`) : ni texte libre, ni commentaire, ni demande de l'utilisateur.
- **Cohérence des chiffres** : risques, actions et rapport consomment exclusivement les résultats de `logic.pert.compute` ; aucune date absolue n'est déduite des jours relatifs ES/EF/LS/LF.
- **Non-régression** : suite complète `python -m unittest discover -s tests -v` verte, y compris les tests HTTP existants.

## Limites

- **IA non connectée par défaut** : sans `IA_API_KEY` (ou avec `IA_ENABLED=0`), l'application fonctionne en **mode sans IA**. Les règles déterministes, les calculs PERT/CPM et les rapports restent disponibles ; seule l'appréciation textuelle est absente.
- **Niveaux de risque indicatifs** : les alertes traduisent des règles simples (marge faible/nulle, tâches bloquantes, convergence, date de début absente). Elles ne sont ni des probabilités ni des garanties et doivent être validées par l'équipe projet.
- **Aucune prédiction de date** : le moteur fournit des durées et des jours relatifs ; aucune date d'achèvement n'est inférée sans date de référence explicitement renseignée et validée.
- **Nom définitif de l'app à choisir** : le titre « PERT/CPM PRO » reste provisoire ; la dénomination finale (documents, interface, déploiement) doit être arbitrée avant la mise en production.
