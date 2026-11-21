# Déploiement — PERT/CPM PRO

**Date :** 21 novembre 2026  
**Cible :** Render (Web Service + PostgreSQL géré) — <https://app-pert-cpm.onrender.com/>  
**Références :** `docs/documentation_technique.md`, `docs/sauvegarde.md`, `docs/recette.md`.

> Avant toute mise en production, exécuter la suite de tests (86 tests) et effectuer une sauvegarde. La base de données de production ne doit **jamais** être restaurée sans sauvegarde préalable de son état courant.

## 1. Pré-requis

- Un dépôt Git accessible par Render (miroir de `github.com/savadogotresor-pixel/APP_PERT_CPM`).
- Un compte Render avec un **Web Service** et une **base PostgreSQL** (offre avec persistance ; le disque des offres gratuites n'est pas garanti).
- Python 3 (les versions des dépendances sont figées dans `requirements.txt`).
- Les valeurs suivantes à portée : `SECRET_KEY`, `DATABASE_URL`, accès SSH/psql à la base, accès au tableau de bord Render.

## 2. Procédure Render pas-à-pas

### 2.1 Créer le service web

1. Dans Render : **New + → Web Service** → connecter le dépôt.
2. **Name** : `app-pert-cpm` (ou le nom définitif retenu, voir `docs/recette.md`, §Réserves).
3. **Runtime** : `Python 3` ; **Branch** : branche de production (`main`) ; **Region** : celle choisie pour la base PostgreSQL (latence et sauvegardes).
4. **Build Command** :

   ```bash
   pip install -r requirements.txt
   ```

5. **Start Command** :

   ```bash
   gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
   ```

   `$PORT` est fourni par Render. Ne jamais utiliser `python app.py` en production : le serveur de développement Flask ne doit pas être exposé. Avec SQLite, limiter à un seul worker ; avec PostgreSQL, deux workers conviennent au démarrage.

6. **Health Check Path** : `/health` (réponse attendue `{"status": "ok"}`, sans authentification).

### 2.2 Créer et raccorder PostgreSQL

1. **New + → PostgreSQL** ; noter l'**Internal Database URL** (recommandée : trafic interne, pas de coût de sortie).
2. Dans le Web Service, ouvrir **Environment** et ajouter `DATABASE_URL` avec cette URL.
3. `config.py` normalise automatiquement le préfixe `postgres://` de Render en `postgresql://`. Le pilote `psycopg2-binary` (version figée dans `requirements.txt`) est installé par le Build Command : le dialecte SQLAlchemy par défaut `postgresql+psycopg2` fonctionne donc sans autre réglage.
4. Au démarrage, `models.init_db(app)` exécute `db.create_all()` : les tables absentes sont créées. Vérifier les journaux Render : un échec d'initialisation est journalisé sans empêcher le démarrage du processus, il faut donc le rechercher explicitement.

> Si le déploiement échoue avec `ModuleNotFoundError: No module named 'psycopg2'`, vérifier que `requirements.txt` contient bien `psycopg2-binary` et que le Build Command est `pip install -r requirements.txt`.

### 2.3 Variables d'environnement

| Variable | Valeur attendue | Obligatoire en prod | Remarque |
|---|---|---|---|
| `SECRET_KEY` | Chaîne aléatoire longue (≥ 32 octets) | **Oui** | `config.ProductionConfig` lève une `RuntimeError` au démarrage si elle manque. |
| `DATABASE_URL` | URL PostgreSQL interne | **Oui** | Avec `ProductionConfig`, l'absence de la variable bloque aussi le démarrage. |
| `APP_SETTINGS` | `config.ProductionConfig` | Recommandé | Active secrets stricts, `DEBUG=False` et cookies sécurisés. Sans elle, `config.Config` utilise un secret de développement : **à proscrire en production**. |
| `FLASK_DEBUG` | absent ou `0` | Non | `1` active les traces de debug et le niveau de log `DEBUG` : interdit en production. |
| `PORT` | fourni par Render | Automatique | Utilisé par la commande gunicorn. |

Génération d'un secret :

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Ne jamais commiter la valeur dans le dépôt, un fichier `.env` versionné ou un ticket. Les secrets des services IA éventuels suivront la même règle (aucun n'est utilisé aujourd'hui).

### 2.4 Premier compte administrateur

Le blueprint `/admin` protège ses routes avec `admin_required` ; l'inscription via l'interface crée un compte standard. Pour obtenir le premier administrateur :

1. Se connecter à l'application déployée et créer un compte avec l'URL publique `/register`.
2. Promouvoir ce compte en base (le nom de la table doit être cité, `user` étant un mot réservé PostgreSQL) :

   ```sql
   UPDATE "user"
      SET is_admin = TRUE
    WHERE lower(username) = 'admin';
   ```

   Depuis le shell : `psql "$DATABASE_URL"` puis coller la requête, ou `psql "$DATABASE_URL" -c "UPDATE \"user\" SET is_admin = TRUE WHERE lower(username) = 'admin';"`.

3. Se déconnecter puis se reconnecter : le cookie de session est recomposé avec le nouveau rôle.
4. Ouvrir `/admin/` : le tableau de bord doit afficher les compteurs et les dernières actions. Le rôle peut ensuite être accordé/retiré depuis `/admin/utilisateurs` (l'auto-rétrogradation est refusée et journalisée).

> En développement SQLite, la promotion équivaut à :
> `python -c "import sqlite3; c = sqlite3.connect('instance/pert_cpm.db'); c.execute(\"UPDATE user SET is_admin = 1 WHERE username = 'admin'\"); c.commit()"`.

### 2.5 Vérification post-déploiement

| # | Contrôle | Méthode | Résultat attendu |
|---|---|---|---|
| 1 | Service en ligne | `curl https://app-pert-cpm.onrender.com/health` | HTTP 200, `{"status":"ok"}` |
| 2 | Configuration de production | Journaux Render au démarrage | Aucune `RuntimeError` sur `SECRET_KEY` / `DATABASE_URL`, aucune erreur `init_db` |
| 3 | En-têtes de sécurité | `curl -I https://app-pert-cpm.onrender.com/` | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, CSP ; HSTS sur HTTPS |
| 4 | Authentification | Inscription, connexion, déconnexion, `/profile` | Parcours nominal sans erreur 500 |
| 5 | Persistance | Créer un projet + 2 tâches, redémarrer le service, rouvrir le projet | Données conservées, durée recalculée à l'identique |
| 6 | Calcul | Projet de référence A(2) → B(3) | Durée 5 jours, chemin `A → B` |
| 7 | Droits | Deux comptes distincts ; tenter d'ouvrir le projet de l'autre | HTTP 403 |
| 8 | Exports | Télécharger CSV, XLSX et PDF depuis un projet | Fichiers lisibles, valeurs identiques au tableau |
| 9 | Administration | `/admin/`, `/admin/utilisateurs`, `/admin/journal` avec le compte admin | 200 ; 403 pour un compte standard |
| 10 | Journaux | Onglet **Logs** Render et `/admin/journal` | Aucune trace d'erreur non gérée pendant les tests |

Consigner le résultat de ces dix contrôles dans le PV de recette (`docs/recette.md`).

## 3. Rollback

1. **Rollback applicatif** : dans Render, ouvrir le service → **Deploys** → sélectionner le dernier déploiement sain → **Rollback**. Render redémarre avec l'image et le commit précédents ; les variables d'environnement restent celles configurées (les ajouter manuellement si le changement concernait aussi la configuration).
2. **Vérifier** : `/health`, connexion, un projet connu, un export ; consulter les journaux.
3. **Données** : un rollback de code ne restaure **pas** la base. Si une mise à jour de schéma a échoué, remplacer la base par la sauvegarde d'avant déploiement selon `docs/sauvegarde.md` — après avoir fait une copie de l'état courant (qui peut contenir des données utilisateurs légitimes).
4. **Communiquer** : informer les utilisateurs, Documenter l'incident et le correctif dans `docs/audit.md` / le registre des sauvegardes.

Ordre recommandé : code d'abord, données ensuite. Ne jamais restaurer une base plus ancienne sur un code plus récent sans vérifier la compatibilité du schéma.

## 4. Changement de `SECRET_KEY`

La clé sert à signer les cookies de session et les jetons CSRF.

1. Générer une nouvelle valeur : `python -c "import secrets; print(secrets.token_hex(32))"`.
2. Faire une sauvegarde de la base (mesure conservatoire ; les données ne sont pas chiffrées avec cette clé).
3. Dans Render → **Environment**, remplacer `SECRET_KEY`, enregistrer puis redéployer.
4. **Conséquence attendue** : toutes les sessions actives sont invalidées ; les utilisateurs doivent se reconnecter et les formulaires ouverts avant le changement doivent être rechargés. Aucune donnée n'est perdue.
5. Vérifier `/health`, une connexion fraîche, un POST avec jeton CSRF valide (création de projet) et l'absence d'erreurs `Bad Signature` dans les journaux.
6. Si plusieurs instances cohabitent pendant une transition, conserver brièvement l'ancienne valeur comme secret secondaire ou prévoir une fenêtre de maintenance courte : Flask ne gère pas nativement deux clés actives ici.

## 5. Passage SQLite → PostgreSQL avec `db.create_all()`

Contexte : le développement utilise `sqlite:///pert_cpm.db` (fichier `instance/pert_cpm.db`) ; la production doit utiliser PostgreSQL. Les URI `postgresql://...` ci-dessous nécessitent le pilote `psycopg2-binary` (installé via `pip install -r requirements.txt`) : sans lui, `create_engine()` lève `ModuleNotFoundError: No module named 'psycopg2'`.

### 5.1 Créer le schéma sur PostgreSQL

`init_db(app)` est appelé au chargement de `app.py` : il suffit donc de démarrer l'application avec la nouvelle URI. Pour le faire explicitement et vérifier le résultat :

```bash
set APP_SETTINGS=config.ProductionConfig
set SECRET_KEY=<valeur>
set DATABASE_URL=postgresql://user:password@host:5432/dbname
python -c "from app import app; from models import init_db; init_db(app); print('schema ok')"
```

`db.create_all()` est **idempotent** : il crée les tables absentes, ne supprime rien et ne modifie pas les colonnes existantes. Toute évolution de colonnes nécessite un script SQL versionné (aucun outil de migrations n'est installé à ce jour).

### 5.2 Transférer les données

Faire une sauvegarde SQLite (`scripts/backup.py`), puis insérer les lignes dans les tables déjà créées, en respectant l'ordre des clés étrangères :

```python
from sqlalchemy import create_engine
from models import db  # remplit db.metadata avec les 7 tables

sqlite_engine = create_engine("sqlite:///instance/pert_cpm.db")
pg_engine = create_engine("postgresql://user:password@host:5432/dbname")

for table in reversed(db.metadata.sorted_tables):
    with sqlite_engine.connect() as src:
        lignes = [dict(ligne) for ligne in src.execute(table.select())]
    if lignes:
        with pg_engine.begin() as dst:
            dst.execute(table.insert(), lignes)
```

Points d'attention :

- exécuter sur une base PostgreSQL **vide** (hors tables créées par `create_all()`) pour éviter les doublons de codes ;
- resynchroniser les séquences après insertion, par exemple :
  `SELECT setval(pg_get_serial_sequence('"user"', 'id'), COALESCE((SELECT MAX(id) FROM "user"), 1), (SELECT COUNT(*) FROM "user") > 0);` et idem pour `project`, `task`, `scenario`, `audit_log` ;
- citer le nom de table `"user"` (mot réservé SQL) dans toutes les requêtes psql ;
- vérifier la présence du mot de passe haché (`password_hash`) et de `is_admin` pour chaque compte, puis tester une connexion ;
- relancer les 86 tests avec `APP_SETTINGS=config.TestConfig` (ils n'utilisent pas la base de production).

### 5.3 Bascule

1. Déployer avec la nouvelle `DATABASE_URL` (et `APP_SETTINGS=config.ProductionConfig`).
2. Appliquer la grille de vérification du §2.5, en insistant sur la persistance après redémarrage.
3. Conserver la sauvegarde SQLite comme archive ; la stratégie de sauvegarde PostgreSQL prend le relais (voir `docs/sauvegarde.md`).

## 6. Limitations connues

- **Hébergement** : l'offre Render gratuite peut mettre le service en veille et ne garantit pas un disque persistant ; seule la base PostgreSQL offre la persistance. Le plan payant est recommandé pour la production.
- **Migrations** : `db.create_all()` ne gère ni les ajouts de colonnes ni les transformations de données ; aucun Alembic n'est configuré.
- **Sécurité** : limitation des tentatives de connexion non implémentée ; deux routes historiques (`/resultats_custom`, `/export_excel`) sont exemptées de CSRF ; CSP volontairement permissive pour les CDN de graphiques.
- **Administration** : blueprint en cours de finalisation ; son enregistrement dans `app.py` est conditionnel (vérifier les routes avec la commande du §5 de la documentation technique).
- **Fonctions absentes** : module IA (cahier §19), gestion des ressources/coûts, notifications, API publique.
- **Modèle de calcul** : relations Finish-to-Start sans délai uniquement ; unité de temps = jours calendaires entiers ; pas de PERT probabiliste à trois points.
- **Exports** : `/export_excel` intègre des captures PNG envoyées par le navigateur (validées et recalculées côté serveur pour les chiffres) ; le rapport PDF, lui, génère ses graphiques côté serveur avec matplotlib.
- **Dépendances** : `matplotlib`, `networkx`, `psycopg2-binary` (pilote PostgreSQL) et d'autres bibliothèques sont installées ; le rapport PDF utilise bien matplotlib, tandis que les graphiques interactifs reposent sur des CDN (vis.js, frappe-gantt).
