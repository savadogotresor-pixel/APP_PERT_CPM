# Plan d'implémentation — PERT/CPM PRO

**Date :** 21 novembre 2026  
**Références :** `docs/Cahier_des_charges_PERT_CPM_PRO.docx` (v1.0), `docs/audit.md`.  
**Objet :** transformer le prototype actuel en application multi-utilisateurs testée, sécurisée et déployée, puis ajouter progressivement le module « AI Project Manager ».

> ⚠️ **LOT 0 — moteur certifié et tests — est en cours d'implémentation par un autre agent.** Ce document le décrit pour mémoire et pour les dépendances, mais **ne prescrit aucune modification de son code** : `logic/calcul.py`, les futurs tests du moteur et les jeux de référence ne doivent pas être modifiés dans le cadre de la rédaction de ces documents. Tous les autres lots supposent le LOT 0 livré.

## 1. Principes directeurs

- Ne pas réécrire l'application : stabiliser, isoler, étendre (Annexe B du cahier, lignes 611-616).
- Le **moteur PERT/CPM reste la seule source de vérité mathématique**, y compris face à l'IA (lignes 619, 758, 775).
- Sécurité et persistance avant fonctions avancées ; chaque lot est livrable et vérifiable séparément.
- Les scénarios d'optimisation et les propositions IA sont des **simulations**, jamais des décisions automatiques (lignes 224, 675, 699).

Estimations relatives : **XS** < 1 j · **S** ~2-3 j · **M** ~4-6 j · **L** ~1-2 sem. · **XL** ~3-4 sem. (une personne, hors recette).

## 2. Découpage en lots

| Lot | Objectif | Exigences couvertes | Livrables | Estimation | Dépendances |
|---|---|---|---|---|---|
| **0 — Moteur certifié et tests** *(en cours, autre agent — ne pas modifier)* | Rendre le calcul déterministe, complet et testé | CALC-01 à CALC-07 ; TASK-03, TASK-04, TASK-05, TASK-07 (partie calcul) ; §6.2 ; jeux de référence §12 | Parcours topologique sans boucle infinie, marge libre, tous les chemins critiques, epsilon de criticité, validation centralisée des durées et cycles, suite de tests unitaires, jeux de référence (chaîne simple, branches, deux chemins critiques, cycle, durée négative) | **M** | Aucune |
| **1 — Socle sécurisé et production** | Supprimer les failles bloquantes et rendre le déploiement reproductible | CALC-07 ; EXP-01 (fiabilité) ; §11 ; §7 (disponibilité, maintenabilité) | `SECRET_KEY` par variable d'environnement, `debug=False`, `MAX_CONTENT_LENGTH`, jetons CSRF, gestionnaires d'erreurs, échappement DOM du popup Gantt, recalcul serveur avant export Excel, neutralisation des formules Excel, suppression des PNG temporaires, CDN versionnés, `Procfile`/`render.yaml`, `requirements.txt` épuré et gunicorn versionné, journalisation minimale | **M** | LOT 0 |
| **2 — Persistance et comptes** | Introduire utilisateurs et stockage durable | AUTH-01 à AUTH-05 ; PROJ-01 (socle) ; §10 ; §11 (hachage, contrôle de propriété) | Modèles Utilisateur / Projet / Tâche / Dépendance / Scénario / Journal, migrations, Flask-Login, mots de passe hachés, contrôle serveur de propriété, limitation des tentatives de connexion, politique de session | **XL** | LOT 1 |
| **3 — Espace projets et tâches** | Offrir le cycle complet de gestion, en interface responsive | PROJ-01 à PROJ-05 ; TASK-01 à TASK-07 ; §8 (tableau de bord, écrans) ; §7 (responsive, accessibilité) ; VIS-06 | Dashboard, liste et CRUD des projets (archivage confirmé), formulaire et tableau éditable des tâches, unicité des codes, import CSV/XLSX avec aperçu et rapport d'erreurs (séparateur `;` documenté), recalcul après modification, correction des blocs d'aide (`{% if not conseils %}`), mise en page fluide | **L** | LOT 2 |
| **4 — Visualisations fiables** | Graphiques synchronisés, lisibles et interactifs | VIS-01 à VIS-06 ; EXP-03 (images pour rapports) | Graphe PERT responsive avec mise en évidence des arcs critiques et fiche tâche au clic, Gantt avec zoom/défilement, rafraîchissement après recalcul, génération serveur des images statiques pour les exports | **M** | LOT 0, LOT 3 |
| **5 — Optimisation déterministe** | Simuler des compressions et comparer des scénarios | OPT-01 à OPT-05 ; §5.6 | Simulation de réduction de durée, comparaison initial/simulé, liste des tâches critiques compressibles, affichage systématique des hypothèses, limites et effets secondaires ; coût de compression en version avancée (OPT-04) | **M** | LOT 0, LOT 3 |
| **6 — Rapports et exports serveur** | Produire des fichiers conformes aux résultats affichés | EXP-01 à EXP-04 ; §12 (conformité des exports) | CSV et XLSX générés côté serveur, rapport PDF serveur avec tableaux et graphiques, date de génération et paramètres utilisés, cellules texte protégées contre les formules | **M** | LOT 4, LOT 5 |
| **7 — Recette, documentation et déploiement** | Valider et livrer la version PRO | L1 à L7 ; §12, §13, §15, §16 | Dossier de tests (cas normaux, limites, erreurs), PV de recette, documentation technique (architecture, installation, configuration), guide utilisateur PRO, procédure de sauvegarde et de restauration testée, supervision, mise en production | **M** | Tous les lots ci-dessus |
| **IA-1 — Assistant explicatif** | Répondre en langage naturel à partir des données réellement enregistrées | IA-ASS-01 à IA-ASS-06 ; §19.1, §19.2, §19.8, §19.9, §19.10 | Interface conversationnelle dans le tableau de bord et le détail de projet, service IA serveur séparé du moteur, contexte minimal et contrôlé, réponses fondées sur le moteur (zéro chiffre inventé), signalement des données manquantes, clé API en variable d'environnement, mode sans IA, limitation d'usage et suivi des coûts | **L** | LOT 3, LOT 7 (sécurité) |
| **IA-2 — Génération de projets** | Proposer une WBS à partir d'une description libre | IA-GEN-01 à IA-GEN-06 ; §19.3 | Analyse de l'objectif, proposition de phases/livrables/tâches/durées/dépendances/jalons, écran de prévisualisation, modification avant enregistrement, validation serveur des codes et cycles, enregistrement **uniquement après confirmation explicite** | **L** | IA-1, LOT 3 |
| **IA-3 — Optimisation assistée** | Suggérer des scénarios et les faire recalculer par le moteur | IA-OPT-01 à IA-OPT-06 ; §19.4 | Détection des tâches critiques, scénarios de compression / fast-tracking / crashing avec hypothèses, recalcul déterministe de chaque scénario, comparaison des durées, chemins et marges, compromis et limites affichés, planning de référence jamais modifié sans confirmation | **M** | IA-1, LOT 5 |
| **IA-4 — Gestion assistée, risques et rapports** | Actions en langage naturel, analyse de risques et synthèses commentées | IA-RISK-01 à IA-RISK-05 ; §19.5, §19.6, §19.7, §19.11 | Détection des marges faibles et des tâches bloquantes, distinction règles calculées / appréciations IA, alertes justifiées et commentables, actions structurées prévisualisées puis validées côté serveur et journalisées, confirmation renforcée pour les suppressions, rapports intelligents datés et sourcés, recette IA | **XL** | IA-2, IA-3, LOT 7 |

**Chemin critique du plan :** LOT 0 → LOT 1 → LOT 2 → LOT 3 → LOT 4/5 → LOT 6 → LOT 7 → IA-1 → IA-2/IA-3 → IA-4. Les lots IA sont **conditionnels** et ne doivent pas retarder la mise en production de la version PRO.

## 3. Planning indicatif

Les durées ci-dessous sont des semaines calendaires pour une personne ; elles reprennent les phases 1 à 7 du cahier (lignes 497-530) et supposent le LOT 0 livré en fin de semaine 2.

| Semaines | Travaux | Jalon |
|---|---|---|
| S1 | Audit, cadrage, arbitrage des hypothèses (§5) | **J0 — Audit validé** (`docs/audit.md`) |
| S2 | LOT 0 (en cours par l'autre agent) + démarrage LOT 1 | **J1 — Moteur certifié** (jeux de référence verts) |
| S3 | Fin LOT 1 : sécurité, exports fiables, configuration Render | **J2 — Socle sécurisé** |
| S4-S6 | LOT 2 : base, migrations, authentification, propriété des projets | **J3 — Persistance vérifiée** (données conservées après redémarrage) |
| S7-S8 | LOT 3 : dashboard, CRUD projets/tâches, import, responsive | **J4 — Gestion fonctionnelle** |
| S9 | LOT 4 : graphe PERT, Gantt, zoom, images serveur | **J5 — Visualisations synchronisées** |
| S10 | LOT 5 : scénarios d'optimisation | **J6 — Simulations validées** |
| S11 | LOT 6 : exports CSV/XLSX/PDF serveur | **J7 — Exports conformes** |
| S12 | LOT 7 : recette, documentation, sauvegardes, déploiement | **J8 — PV de recette / mise en production** |
| S13-S15 | IA-1 : assistant explicatif | **J-IA1 — Réponses vérifiées, mode sans IA opérationnel** |
| S16-S17 | IA-2 : génération WBS | **J-IA2 — Prévisualisation avant enregistrement** |
| S18 | IA-3 : optimisation assistée | **J-IA3 — Scénarios recalculés et comparés** |
| S19-S20 | IA-4 : actions contrôlées, risques, rapports, recette IA | **J-IA4 — Actions journalisées, coûts maîtrisés** |

Ce calendrier est **indicatif** : il doit être ajusté à la disponibilité de l'équipe et aux contraintes académiques (cahier, ligne 498). Les lots 4, 5 et IA-1 peuvent être partiellement parallélisés dès que le LOT 3 est stable.

## 4. Jalons de validation

| Jalon | Critères d'acceptation |
|---|---|
| J0 | Audit validé ; périmètre et hypothèses arbitrés ; nom définitif retenu. |
| J1 | Chaîne simple, deux branches, deux chemins critiques, cycle, durée négative : tous les tests verts ; marge libre et chemins multiples restitués. **Aucune modification du LOT 0 par ce plan.** |
| J2 | `debug=False` en production ; jetons CSRF actifs ; popup Gantt sans HTML injecté ; export Excel régénéré côté serveur ; aucun fichier temporaire résiduel ; démarrage via `Procfile`/`render.yaml`. |
| J3 | Inscription/connexion/déconnexion fonctionnelles ; mots de passe hachés ; un utilisateur ne peut pas lire ni modifier le projet d'un autre ; redémarrage serveur sans perte de données. |
| J4 | CRUD projet/tâche, import avec rapport d'erreurs, recalcul automatique, interface utilisable sur mobile et au clavier. |
| J5 | Tableau, graphe et Gantt reflètent le même recalcul ; éléments critiques visibles ; zoom et fiche tâche disponibles. |
| J6 | Chaque scénario est comparé au planning initial ; hypothèses, limites et effets secondaires affichés ; aucun scénario n'écrase le planning de référence. |
| J7 | CSV/XLSX/PDF générés côté serveur, lisibles, datés, et strictement identiques aux résultats affichés. |
| J8 | Tous les cas de test du §12 du cahier passés ; documentation L1-L6 livrée ; restauration de sauvegarde testée ; démo de référence D = 30 / A → B → G → H → K → L toujours verte. |
| J-IA1 | Chiffres des réponses identiques au moteur ; données insuffisantes signalées ; aucune clé API côté navigateur ; module désactivable sans casser PERT/CPM. |
| J-IA2 | Aucune donnée enregistrée avant confirmation ; durées proposées étiquetées « estimations » ; codes, prédécesseurs et cycles validés côté serveur. |
| J-IA3 | Chaque proposition recalculée par le moteur ; comparaison initial/simulé fournie ; limites du fast-tracking et du crashing explicitées. |
| J-IA4 | Toute action passe par prévisualisation → validation des droits → confirmation → exécution → journalisation ; scénarios d'attaque courants couverts ; coût API suivi. |

## 5. Hypothèses à trancher avant le LOT 2

| # | Point | État actuel | Décision attendue / recommandation |
|---|---|---|---|
| H1 | **Nom définitif de l'application** | « Application PERT/CPM » (provisoire) ; URL de production `app-pert-cpm.onrender.com` | À valider par le porteur. Pistes : *PERT Manager*, *ChronoPro*, *Planif'PERT*, *CritiPath*. Le nom retenu aura un impact sur le titre de l'interface, le README, les exports et l'éventuel domaine. |
| H2 | **Unité de temps** | Jours entiers ; `EF = ES + durée`, Gantt = date de début + `timedelta(days=ES)` (`app.py:37-38`) | Confirmer des **jours calendaires entiers** pour la v1, et documenter la convention (§6.2). Décider séparément de l'usage de jours ouvrés, des heures et d'une date de référence. |
| H3 | **Type de relation entre tâches** | Relation Finish-to-Start implicite, unique, sans délai (`logic/data.py:106`, `app.py:26-27`) | Conserver **FS sans lag** en v1 ; le champ `type_relation` du modèle (ligne 407) peut être réservé pour SS/FF/lag en évolution. Le séparateur de prédécesseurs sera `;` dans les imports (Annexe A, ligne 609) et `,` dans la saisie texte actuelle : **uniformiser et documenter**. |
| H4 | **Base de données** | Aucune | **SQLite en développement, PostgreSQL en production** (ligne 360). Vérifier le caractère persistant du disque sur l'offre Render retenue ; sinon prévoir un service PostgreSQL géré. Migrations versionnées obligatoires. |
| H5 | **Fournisseur IA** | Aucun | API OpenAI ou compatible (ligne 748) ; arbitrer sur coût, confidentialité, disponibilité et conditions d'utilisation. Clé API **uniquement en variable d'environnement serveur** (ligne 763), avec mode sans IA, limite d'usage et suivi des coûts. |
| H6 | **Sauvegarde** | Aucune | Définir fréquence (quotidienne recommandée), rétention, destination hors de l'hébergement, responsable de la restauration et **test de restauration trimestriel** (lignes 426, 539). |
| H7 | Hébergement et budget | Render, offre inconnue | Confirmer l'offre (veille, disque persistant, backups Postgres) et le budget mensuel, y compris le budget API IA. |

## 6. Suivi et responsabilités

- Chaque lot fait l'objet d'une branche et d'une revue avant intégration ; les tests du LOT 0 sont exécutés à chaque intégration.
- Le LOT 0 reste **la propriété de l'autre agent** jusqu'à son jalon J1 ; les autres lots consomment son API sans la modifier.
- Les écarts au cahier sont consignés dans `docs/audit.md` et mis à jour à chaque jalon.
