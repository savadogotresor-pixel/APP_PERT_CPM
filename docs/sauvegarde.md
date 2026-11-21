# Sauvegarde et restauration — PERT/CPM PRO

**Date :** 21 novembre 2026  
**Références :** cahier des charges §11 (ligne 426 : « Mettre en place des sauvegardes régulières et tester leur restauration ») et §15 (risque « Perte de données »), `docs/deploiement.md`, `docs/documentation_technique.md`.  
**Scripts associés :** `scripts/backup.py` et `scripts/restore.py` (bibliothèque standard Python uniquement, base SQLite).

## 1. Stratégie

| Élément | Développement (SQLite) | Production (PostgreSQL) |
|---|---|---|
| Fréquence | Avant chaque modification de schéma ou import important ; au moins une fois par semaine | **Quotidienne** (recommandé : 02:00 UTC, hors pics d'usage), plus une sauvegarde immédiate avant chaque déploiement ou migration |
| Rétention | 30 dernières copies (script : `--keep 30`) | 30 sauvegardes quotidiennes + 8 hebdomadaires + 12 mensuelles |
| Destination | Dossier `backups/` local (non versionné) | Stockage **hors de Render** (S3, Backblaze ou équivalent), avec versioning et chiffrement au repos |
| RPO (perte maximale acceptable) | 24 h | 24 h ; réduire à 1 h si une réplication ou des dumps plus fréquents sont mis en place |
| RTO (délai de remise en service visé) | 1 h | 1 h (restauration + vérifications) |
| Responsable | Développeur local | Responsable technique / administrateur Render + PostgreSQL, désigné par le porteur |

Règles générales :

1. Une sauvegarde non **testée** n'est pas une sauvegarde : voir §5.
2. Ne jamais conserver l'unique copie sur le même service que la base de production.
3. Ne jamais journaliser d'identifiants PostgreSQL dans le registre ; stocker les secrets dans les variables d'environnement ou un gestionnaire de secrets.
4. Avant toute restauration, sauvegarder l'état courant (même dégradé) : il peut contenir des données utilisateurs légitimes.

## 2. Sauvegarde SQLite (développement)

Le script `scripts/backup.py` utilise `sqlite3.Connection.backup()`, ce qui produit une copie **cohérente** même si la base est ouverte (contrairement à une copie du fichier à `cp`/`Copy-Item`). Le chemin source est déduit de `DATABASE_URL` ou, à défaut, de `instance/pert_cpm.db` (repli `pert_cpm.db`). Le nom de fichier contient un horodatage : `pert_cpm_20261121_020000.sqlite3`.

```bash
# Depuis la racine du dépôt, avec les valeurs par défaut
python scripts/backup.py

# Chemin explicite, dossier de sortie et rétention personnalisés
python scripts/backup.py --db instance/pert_cpm.db --output backups --keep 30 --days 45

# Aperçu sans écrire ni supprimer quoi que ce soit
python scripts/backup.py --dry-run
```

Options principales :

| Option | Effet |
|---|---|
| `--db CHEMIN` | Force le fichier SQLite source (sinon `DATABASE_URL`, sinon `instance/pert_cpm.db`). |
| `--output DOSSIER` | Dossier des sauvegardes (`backups` par défaut), créé si nécessaire. |
| `--keep N` | Nombre maximum de copies conservées (`30` par défaut ; `0` désactive la limitation). |
| `--days N` | Supprime également les copies plus anciennes que N jours (désactivé par défaut). |
| `--no-prune` | Désactive toute suppression automatique. |
| `--dry-run` | Affiche les actions prévues sans écrire ni supprimer. |

Le script vérifie l'intégrité de la copie (`PRAGMA quick_check`) avant de la déclarer valide, puis purge uniquement les fichiers portant le même préfixe et le motif d'horodatage attendu.

## 3. Sauvegarde PostgreSQL (production)

Utiliser l'outil officiel `pg_dump` (fourni avec le client PostgreSQL), avec le format personnalisé `--format=custom`, plus compact et plus rapide à restaurer via `pg_restore`.

```bash
# URL de connexion : render fournit DATABASE_URL (ne pas l'écrire dans un script versionné)
set DATABASE_URL=postgresql://user:password@host:5432/dbname

# Dump complet de la base (format binaire/custom)
pg_dump --format=custom --no-owner --no-privileges \
        --file="backup_pert_cpm_%DATE%.dump" "$DATABASE_URL"

# Variante SQL lisible (utile pour un audit ponctuel)
pg_dump --format=plain --no-owner --no-privileges \
        --file="backup_pert_cpm_%DATE%.sql" "$DATABASE_URL"
```

Sous PowerShell, construire le nom avec `Get-Date -Format yyyyMMdd_HHmmss` :

```powershell
$horodatage = Get-Date -Format 'yyyyMMdd_HHmmss'
pg_dump --format=custom --no-owner --no-privileges --file="backup_pert_cpm_$horodatage.dump" $env:DATABASE_URL
```

Bonne pratique : fournir le mot de passe via `PGPASSWORD` dans l'environnement du cron/dump (ou un fichier `.pgpass` avec droits 600), jamais en argument de commande (visible dans l'historique et les journaux des processus).

Vérification après dump :

```bash
pg_restore --list "backup_pert_cpm_$horodatage.dump" | head -20
Get-FileHash "backup_pert_cpm_$horodatage.dump" -Algorithm SHA256   # PowerShell
# sha256sum backup_pert_cpm_$horodatage.dump                       # Linux
```

Transférer le fichier vers le stockage hors site, puis consigner son emplacement et son empreinte dans le registre (§7). Si Render fournit des sauvegardes managées PostgreSQL, les activer **en complément** et documenter leur fréquence : elles ne remplacent pas un dump vérifié et restaurable par l'équipe.

## 4. Restauration

### 4.1 SQLite (développement)

```bash
# 1) Simulation : vérifie le fichier et affiche les étapes, n'écrit rien
python scripts/restore.py --backup backups/pert_cpm_20261121_020000.sqlite3

# 2) Exécution réelle : --force autorise l'écrasement, --yes valide la confirmation non interactive
python scripts/restore.py --backup backups/pert_cpm_20261121_020000.sqlite3 --force --yes
```

Le script `scripts/restore.py` :

- refuse tout écrasement sans `--force` (mode simulation par défaut) ;
- demande une confirmation explicite (`OUI`) sauf `--yes` ;
- vérifie l'en-tête SQLite et `PRAGMA quick_check` de la sauvegarde ;
- crée une **sauvegarde de sécurité de l'état courant** avant remplacement (`..._avant_restauration_<horodatage>.sqlite3`) ;
- copie la sauvegarde dans un fichier temporaire du même dossier, le vérifie, puis remplace la base de façon atomique avec `os.replace()` ;
- supprime les fichiers `-wal`/`-shm` résiduels uniquement pendant une restauration réelle.

Arrêter l'application avant la restauration si elle est locale et active (`python app.py`). Sur le serveur déployé, SQLite n'est pas utilisé : appliquer la procédure PostgreSQL ci-dessous.

### 4.2 PostgreSQL (production)

1. **Fenêtre de maintenance** : prévenir les utilisateurs, mettre le Web Service en pause ou basculer sur une page de maintenance.
2. **Sauvegarder l'état courant** avec `pg_dump` (§3), même si la base paraît corrompue.
3. **Inspecter la sauvegarde** :
   ```bash
   pg_restore --list backup_pert_cpm_20261121_020000.dump | head -30
   ```
4. **Restaurer** (destructif : `--clean --if-exists` supprime les objets existants) :
   ```bash
   pg_restore --clean --if-exists --no-owner --no-privileges \
              --dbname="$DATABASE_URL" backup_pert_cpm_20261121_020000.dump
   ```
5. **Vérifier** : nombre de lignes des tables `user`, `project`, `task`, `dependency`, `project_access`, `scenario`, `audit_log` ; connexion d'un compte connu ; `/health` ; un calcul et un export.
6. **Redémarrer** le service Render, puis refaire la grille de vérification post-déploiement (`docs/deploiement.md`, §2.5).

> Ne jamais lancer `pg_restore` directement sur une base utilisée par l'application sans arrêt ou pause : les transactions en cours peuvent être écrasées.

## 5. Test de restauration obligatoire (cahier, ligne L426)

Le test de restauration est **obligatoire** :

- **avant chaque mise en production** qui touche au schéma ou aux données ;
- **au moins une fois par trimestre** pour la production ;
- à chaque changement de la procédure, du fournisseur d'hébergement ou de la version de PostgreSQL.

Protocole :

1. Choisir une sauvegarde et enregistrer son nom, sa date et son empreinte SHA256.
2. Restaurer dans un **environnement isolé** : base PostgreSQL temporaire (autre nom de base) pour la prod, ou fichier SQLite distinct pour le dev. Ne jamais tester sur la base de production.
3. Contrôler l'intégrité : `pg_restore --list` / `PRAGMA quick_check`, nombre de lignes attendu, absence d'erreurs dans les journaux.
4. Démarrer l'application sur cette copie (`DATABASE_URL` de test, `APP_SETTINGS=config.TestConfig` **non** adapté à une base persistante : utiliser `config.Config` avec une URI de test) puis exécuter :
   ```bash
   python -m unittest discover -s tests -v
   ```
5. Vérifier manuellement la démo (30 jours, `A → B → G → H → K → L`), l'ouverture d'un projet existant et la génération d'un PDF.
6. Mesurer la **durée réelle** de restauration et la comparer au RTO (1 h). Noter tout écart.
7. Remplir la fiche ci-dessous, la signer et l'archiver avec la preuve (captures, sortie des commandes, hash).

| Date | Environnement | Sauvegarde testée | Résultat | Durée | Anomalies / corrective | Testé par |
|---|---|---|---|---|---|---|
| ... | ... | `backup_pert_cpm_....dump` | ✅ / ❌ | ... min | ... | ... |

Un test échoué est un **défaut bloquant** pour la mise en production suivante : corriger la procédure, refaire le test, puis seulement déployer.

## 6. Responsabilités

| Rôle | Responsabilité |
|---|---|
| Développeur / responsable technique | Écrire et exécuter les scripts, configurer la tâche planifiée, vérifier les dumps, réaliser le test de restauration, tenir le registre. |
| Administrateur Render / PostgreSQL | Fournir les accès, surveiller l'espace disque et les sauvegardes managées, valider la configuration réseau et les secrets. |
| Responsable de projet / commanditaire | Valider la fréquence, la rétention et le RPO/RTO, être informé en cas d'incident, signer le PV de test de restauration. |
| Utilisateurs | Signaler immédiatement toute donnée manquante ou incohérente après un déploiement ou une restauration. |

En cas de perte de données suspectée : ne rien modifier, prévenir le responsable technique, conserver les journaux et tenter une restauration sur environnement isolé avant toute réécriture de la base de production.

## 7. Journalisation des sauvegardes

Toute sauvegarde (automatisée ou manuelle) est consignée dans le registre ci-dessous, conservé avec la documentation du projet. Les valeurs sensibles (mot de passe, URL complète avec identifiants) n'y figurent jamais.

| Date/heure (UTC) | Env. | Type | Fichier / emplacement | Taille | SHA256 | Initié par | Résultat | Test restauration |
|---|---|---|---|---|---|---|---|---|
| 2026-11-21 02:00 | prod | `pg_dump custom` | `s3://.../backup_pert_cpm_20261121_020000.dump` | … | … | … | OK | ☐ prévu / ✅ réalisé le … |
| 2026-11-21 09:15 | dev | `scripts/backup.py` | `backups/pert_cpm_20261121_091500.sqlite3` | … | … | … | OK | n/a |

Champs obligatoires : date/heure, environnement, type, chemin, taille, empreinte SHA256, initiateur, résultat (OK/échec + cause), date du dernier test de restauration. Une ligne « échec » déclenche une correction et une nouvelle sauvegarde immédiate.

Commandes utiles pour renseigner le registre :

```powershell
Get-Item "chemin\backup.dump" | Select-Object Name, Length, LastWriteTime
Get-FileHash "chemin\backup.dump" -Algorithm SHA256
```

```bash
ls -lh backup.dump && sha256sum backup.dump
```
