# Guide utilisateur — Application PERT/CPM (version actuelle)

**Application en ligne :** <https://app-pert-cpm.onrender.com/>  
**Objet :** ce guide décrit **l'application telle qu'elle existe aujourd'hui**. Les comptes utilisateurs et les projets enregistrés sont disponibles ; le guide se concentre sur l'accès, la saisie, le calcul et les exports.

## 1. Périmètre actuel — à savoir avant de commencer

- L'application propose des **comptes utilisateurs** : l'accueil (`/`) et le catalogue (**🧰 Catalogue**, `/catalogue/`) sont réservés aux personnes connectées ; la démo (`/demo`) et la saisie libre (`/saisie`) restent accessibles par URL directe.
- Les projets enregistrés dans « Mes projets » sont **persistants**. La saisie libre sur `/saisie`, elle, n'est pas sauvegardée : si vous actualisez la page, copiez votre texte de tâches pour le garder.
- Les durées sont exprimées en **jours entiers supérieurs ou égaux à 0**. Une durée de `0` est acceptée : elle correspond à une tâche instantanée (jalon) qui ne consomme aucun jour. Les dates du Gantt sont calculées en ajoutant ces jours à la date de début choisie (jours calendaires).
- Une seule relation de dépendance est gérée : « la tâche prédécesseur doit être terminée avant que la tâche suivante commence ».
- Le calcul fonctionne sans connexion Internet ; en revanche, les graphiques interactifs et l'export PDF/Excel chargent des bibliothèques depuis des CDN et peuvent manquer hors ligne.

## 2. Prise en main

### 2.1 Accueil

La page d'accueil (`/`) est **protégée** : sans session, vous êtes redirigé vers `/login?next=%2F`. Après connexion, elle affiche un message de bienvenue, la présentation des fonctionnalités et deux boutons :

- **📊 Découvrir la démo (12 tâches)** : ouvre un projet d'exemple déjà calculé ;
- **✏️ Saisir vos tâches** : ouvre le formulaire de saisie.

Une fois connecté, la barre de navigation donne accès à **🧰 Catalogue**, à l'accueil, à « Mes projets », à l'assistant IA et au profil. Avant connexion, ni **Catalogue**, ni **Démo**, ni **Saisie** n'y figurent. La démo reste accessible depuis le bouton de l'accueil ou l'URL `/demo` ; la saisie depuis le bouton de l'accueil ou l'URL `/saisie`. Ouvrir `/catalogue/` sans session redirige vers `/login?next=%2Fcatalogue%2F`. « Connexion » et « Inscription » ne sont pas des rubriques de navigation : ces actions restent disponibles depuis la page de connexion (lien « Créer un compte »).

### 2.2 Connexion, chiffrement du mot de passe et mode hors ligne

Sur `/login`, saisissez votre nom d'utilisateur ou votre adresse e-mail et votre mot de passe. **En ligne**, le navigateur récupère la clé publique sur `/auth/public-key` et chiffre le mot de passe (RSA-OAEP-SHA256) avant l'envoi dans le champ caché `password_chiffre` ; le serveur détient seul la clé privée. Le mot de passe est ensuite stocké sous forme de hachage.

Si JavaScript, Web Crypto ou la clé publique est indisponible (page ouverte hors ligne, par exemple), le formulaire envoie le mot de passe en clair : le serveur accepte ce format pour compatibilité. Le calcul PERT/CPM reste utilisable hors ligne, mais les graphiques et les exports peuvent ne pas s'afficher sans accès aux CDN.

### 2.3 Démo (12 tâches)

Cliquez sur **📊 Démo (12 tâches)** (`/demo`). Le projet d'exemple contient 12 activités (A à L), de l'analyse des besoins à la préparation de la soutenance.

Résultat attendu :

- **Durée totale du projet : 30 jours** ;
- **Chemin critique : A → B → G → H → K → L** ;
- **Chemins critiques (1) : A → B → G → H → K → L** (le nombre entre parenthèses indique combien de chemins sont critiques) ;
- Les lignes correspondantes du tableau et les nœuds du graphe sont colorés en rouge.

Cette démo sert de référence : elle permet de vérifier que l'application affiche bien les résultats connus.

## 3. Saisir son propre projet

### 3.1 Accès au formulaire

Cliquez sur **✏️ Saisie des tâches** (`/saisie`). Le formulaire comporte trois champs :

| Champ | Format | Obligatoire |
|---|---|---|
| Date de début du projet | `JJ/MM/AAAA` dans le champ visible (ex. `21/11/2026`) | Oui — une alerte bloque l'envoi si le format est incorrect |
| Date de fin souhaitée | `JJ/MM/AAAA` | Non — sert uniquement à comparer le planning à un objectif |
| Tâches | Une tâche par ligne, format ci-dessous | Oui |

### 3.2 Format des tâches

```
nom;description;durée;prédécesseurs
```

- **nom** : identifiant court et unique (ex. `A`, `T01`, `API`). Un même nom ne peut pas apparaître deux fois.
- **description** : texte libre ; évitez le code HTML.
- **durée** : nombre entier de jours supérieur ou égal à 0 (ex. `3`). Les valeurs comme `2,5`, `-1` ou `2j` sont refusées. Le `0` est autorisé : il représente une tâche instantanée (jalon).
- **prédécesseurs** : noms des tâches à terminer avant celle-ci, séparés par des **virgules** (ex. `B,C`). Laissez le champ vide pour une tâche sans prédécesseur. Le point-virgule final est alors facultatif mais recommandé.

Exemple complet à copier :

```
A;Analyse des besoins;3;
B;Conception BDD;4;A
C;Maquettes UI;4;A
D;Développement API;10;B,C
E;Tests;5;D
```

Règles à respecter :

1. Chaque prédécesseur doit être une tâche **définie dans la même saisie**.
2. Aucun cycle : `A` ne peut pas dépendre de `B` si `B` dépend déjà de `A`. Un cycle est détecté par le moteur de calcul et signalé par un message lisible (ex. `A → B → A`) : l'application reste utilisable et vous pouvez corriger votre saisie.
3. Les durées doivent être des entiers supérieurs ou égaux à 0 ; la valeur `0` est acceptée et correspond à une tâche instantanée (jalon), sans décalage de dates.

Cliquez ensuite sur **Calculer**. En cas d'erreur, la page liste les messages correspondants et un lien **Retour au formulaire** permet de corriger sans ressaisir depuis zéro — pensez néanmoins à conserver une copie de votre texte.

## 4. Lire les résultats

La page de résultats affiche, dans l'ordre : la date de début (si indiquée), la durée totale, le chemin critique, la ligne « Chemins critiques (n) », le tableau détaillé, les conseils, le graphe PERT, le Gantt, puis les boutons d'export.

### 4.1 Tableau des calculs

| Colonne | Signification |
|---|---|
| **Tâche** | Identifiant saisi |
| **Description** | Libellé saisi |
| **Durée** | Durée en jours |
| **ES** | *Early Start* — début au plus tôt ; vaut 0 si la tâche n'a pas de prédécesseur, sinon le maximum des EF de ses prédécesseurs |
| **EF** | *Early Finish* — fin au plus tôt ; `EF = ES + durée` |
| **LS** | *Late Start* — début au plus tard ; `LS = LF − durée` |
| **LF** | *Late Finish* — fin au plus tard ; minimum des LS des tâches suivantes, ou durée totale du projet si la tâche n'a pas de successeur |
| **Marge totale** | `LS − ES` (également `LF − EF`) : retard possible sans décaler la fin du projet |
| **Marge libre** | Retard maximum que la tâche peut prendre **sans décaler le début d'aucune tâche suivante** : `min(ES des successeurs) − EF`, ou durée totale − EF si la tâche n'a pas de successeur. Elle est toujours **inférieure ou égale à la marge totale**. |
| **Critique ?** | `OUI` si la marge totale vaut 0 (à 1e-9 près), sinon `Non` |

Les **lignes surlignées en rouge** sont celles des tâches critiques. Extrait de la démo (la marge libre y vaut 0 pour ces tâches) :

| Tâche | ES | EF | LS | LF | Marge totale | Marge libre |
|---|---:|---:|---:|---:|---:|---:|
| A | 0 | 3 | 0 | 3 | 0 | 0 |
| B | 3 | 7 | 3 | 7 | 0 | 0 |
| G | 7 | 17 | 7 | 17 | 0 | 0 |
| H | 17 | 22 | 17 | 22 | 0 | 0 |
| K | 22 | 27 | 22 | 27 | 0 | 0 |
| L | 27 | 30 | 27 | 30 | 0 | 0 |

Une tâche peut avoir une marge totale positive et une marge libre nulle : elle peut retarder la fin du projet, mais pas le démarrage de la tâche suivante.

> **Nuance :** l'en-tête « Chemin critique » affiche la **liste des tâches critiques dans l'ordre du tableau**. Sur la démo, cet ordre correspond bien à A → B → G → H → K → L. Avec plusieurs chemins critiques ou un ordre de saisie différent, cette liste ne doit pas être lue comme une succession chronologique. La ligne **« Chemins critiques (n) : ... »** donne, elle, **tous** les chemins critiques réels : chaque chemin est affiché dans l'ordre d'exécution et les chemins sont séparés par `|`.

### 4.2 Conseils d'optimisation

La section historique **💡 Conseils d'optimisation** apparaît **uniquement après une saisie personnalisée**. Une section distincte, **💡 Conseils de l'IA maison**, est désormais affichée dès que le calcul est disponible, y compris sur la démo (voir §8.2). Les conseils historiques sont classés par couleur :

- 🔴 **Critique** : les tâches qui pilotent la durée totale ; tout retard dessus retarde la fin du projet ;
- 🟠 **Attention** : tâches à marge faible, qui pourraient devenir critiques ;
- 🟢 **Flexible** : tâches à marge confortable, dont on peut décaler le démarrage ;
- ⏱️ **Délai** : comparaison à la date de fin souhaitée (en retard ou en avance) ;
- 📐 **Global** : part de tâches critiques et tension du planning.

Ces conseils sont des **indications fondées sur des règles simples**, pas des simulations : ils ne modifient jamais votre planning.

### 4.3 Graphe PERT

Chaque **nœud** est une tâche avec sa durée ; chaque **flèche** est une dépendance (la tâche de départ doit être terminée avant l'arrivée). La disposition se fait de gauche à droite.

- Nœud **rouge** : tâche critique ; nœud **bleu** : tâche non critique.
- Vous pouvez déplacer les nœuds et utiliser la molette de la souris pour zoomer/déplacer la vue (contrôles de la bibliothèque vis-network).

> Sur les écrans étroits, le graphe garde actuellement une largeur fixe de 900 px : faites défiler horizontalement pour le parcourir.

### 4.4 Diagramme de Gantt

Chaque barre représente une tâche, placée entre sa date de début (date de début du projet + ES) et sa date de fin (date de début du projet + EF). Les barres des tâches critiques sont en rouge.

- **Cliquez sur une barre** : un popup affiche le nom de la tâche, sa date de début et sa date de fin au format `JJ/MM/AAAA`.
- Faites défiler horizontalement pour voir les périodes éloignées. Il n'existe pas encore de boutons de zoom dédiés.
- La date de début par défaut est celle du jour ; indiquez une date de début dans le formulaire pour obtenir des dates calibrées sur votre projet.

### 4.5 Blocs d'aide

Les panneaux repliables **« Comment sont calculées les dates et la marge ? »**, **« Comment lire ce graphe ? »** et **« Comment ce diagramme a-t-il été construit ? »** ne s'affichent actuellement que sur la **démo**. Sur une saisie personnalisée, reportez-vous aux §4.1, §4.3 et §4.4 du présent guide.

## 5. Exporter les résultats

Deux boutons se trouvent sous les graphiques.

### 5.1 PDF

Cliquez sur **📄 Télécharger en PDF**. Le navigateur capture la zone des résultats puis télécharge `resultats_pert_cpm.pdf`.

Limites actuelles : le rendu est une **image** (le texte n'est pas sélectionnable) et la découpage en pages est approximatif pour les grands projets. Vérifiez le PDF obtenu avant de le diffuser.

### 5.2 Excel

Cliquez sur **📊 Télécharger en Excel**. Le navigateur capture les deux graphiques, envoie les données au serveur et télécharge `resultats_pert_cpm.xlsx`. Le classeur contient trois onglets :

1. **Résultats** — le tableau des tâches, la durée totale et le chemin critique ; les lignes critiques sont surlignées en rouge ;
2. **Graphe PERT** — image du graphe ;
3. **Diagramme Gantt** — image du Gantt.

L'export dépend des CDN (vis-network, Frappe Gantt, html2canvas) : si un graphique ne s'affiche pas à l'écran, son image peut manquer dans le classeur.

#### Fiabilité des chiffres

Les chiffres du classeur ne sont **pas** ceux de la page : `/export_excel` renvoie la saisie des tâches au serveur, qui recalcule ES, EF, LS, LF, marges et chemins critiques avec le moteur canonique avant de remplir le classeur. Toute valeur affichée ou modifiée dans le navigateur est donc ignorée : l'export ne peut pas être falsifié depuis le navigateur. Par mesure de sécurité, les textes commençant par `=`, `+`, `-` ou `@` sont forcés en texte pour éviter l'injection de formules Excel, et la requête est limitée à 8 Mo. Seules les images des graphiques sont envoyées par le navigateur ; elles doivent être des PNG valides.

## 6. Dépannage

| Message ou symptôme | Cause probable | Action |
|---|---|---|
| `Aucune tâche saisie.` | Le champ Tâches est vide | Saisissez au moins une ligne. |
| `Ligne N : format invalide, il faut au moins nom;description;durée` | Moins de trois champs séparés par `;` | Ex. `A;Analyse;3;` (le `;` final est autorisé). |
| `durée manquante ou invalide ("…")` | Durée vide, non numérique, décimale (`2,5`) ou négative | Indiquez un entier supérieur ou égal à 0 : `3` (ou `0` pour une tâche instantanée). N'utilisez ni virgule décimale ni exposant. |
| `durée invalide ("²")` | Seuls les chiffres ASCII sont acceptés ; `²` est rejeté proprement | Remplacez `²` par `2`. |
| `la tâche "X" est définie plusieurs fois` | Identifiant dupliqué | Renommez l'une des deux tâches. |
| `La tâche "X" dépend de "Y", qui n'existe pas` | Prédécesseur mal orthographié ou non défini | Corrigez le nom ou ajoutez la tâche `Y`. |
| `Dépendance circulaire détectée : A → B → A` | Cycle de dépendances | Supprimez l'une des flèches du cycle (ex. retirez `A` des prédécesseurs de `B`). |
| Alerte « Veuillez saisir une date de début valide au format JJ/MM/AAAA. » | Date saisie sous forme `AAAA-MM-JJ` ou incomplète | Utilisez le format `JJ/MM/AAAA` dans le champ visible. |
| Graphiques absents ou page sans style | CDN bloqués ou hors ligne | Autorisez `cdn.jsdelivr.net`, `unpkg.com` et `cdnjs.cloudflare.com`, puis rechargez. |
| Export Excel/PDF qui ne télécharge pas | Requête ou capture interrompue | Vérifiez la connexion, réessayez et autorisez les téléchargements pour le site. |
| Mes tâches disparaissent après actualisation | Saisie libre non sauvegardée | Conservez une copie de votre texte ou enregistrez le projet dans « Mes projets ». |
| Redirection vers `/login?next=%2F` en ouvrant l'accueil | L'accueil exige une connexion | Connectez-vous, ou créez un compte depuis la page de connexion. |
| Redirection vers `/login?next=%2Fcatalogue%2F` en ouvrant le catalogue | Le catalogue est réservé aux utilisateurs connectés | Connectez-vous ; vous serez ramené au catalogue (ou à la fiche demandée). |
| Connexion impossible alors que les graphiques s'affichent | Clé publique ou Web Crypto indisponible (hors ligne) | Vérifiez la connexion et rechargez ; le formulaire en clair reste accepté. |

## 7. Lancer l'application en local (facultatif)

```bash
git clone https://github.com/savadogotresor-pixel/APP_PERT_CPM.git
cd APP_PERT_CPM
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Ouvrez ensuite <http://127.0.0.1:5000>. L'accueil renvoie vers `/login` : créez un compte via « Créer un compte » si nécessaire. Le mode debug est désactivé par défaut : définissez `FLASK_DEBUG=1` pour l'activer (développement uniquement). `GET /health` renvoie `{"status": "ok"}` lorsque le serveur fonctionne. Utilisez la version déployée pour un usage courant.

## 8. Fonctions récentes

### 8.1 Catalogue de projets

Connectez-vous d'abord : le lien **🧰 Catalogue** n'apparaît dans la barre de navigation qu'après connexion. Ouvrez-le (ou `/catalogue/`) pour consulter la liste des **12 projets types variés** (construction d'une maison, application mobile, mariage, campagne marketing, thèse, ferme maraîchère, déménagement d'entreprise, formation en ligne, court métrage, cabinet médical, festival, refonte de site web), avec leur durée calculée. Sans session, l'accès au catalogue est redirigé vers la page de connexion, avec retour vers la page demandée après authentification.

Cliquez sur **Voir le projet** pour ouvrir la fiche `/catalogue/<id>` : vous y voyez le résumé, le tableau des calculs et le chemin critique, recalculés par le moteur PERT/CPM. Sur la fiche, le bouton **🚀 Optimiser ce projet** crée une **copie du modèle dans votre propre compte** avec les durées d'origine, puis ouvre directement la page de simulation d'optimisation. Le modèle d'origine et le planning de référence ne sont pas modifiés : les réductions ne sont que simulées tant que vous ne les appliquez pas explicitement.

### 8.2 Conseils IA maison automatiques

Une section **💡 Conseils de l'IA maison** s'affiche automatiquement sous les résultats de la démo (`/demo`), de votre saisie (`/resultats_custom`) et de la page d'optimisation d'un projet. Elle est distincte des « Conseils d'optimisation » historiques décrits au §4.2.

Ces conseils reposent sur **8 règles déterministes** : chemin critique unique, chemins critiques multiples, tâche critique à plusieurs successeurs, forte densité de dépendances, jalons en fin de chaîne, marge libre nulle avec marge totale positive, démarrages indépendants multiples et durée totale nulle. Chaque carte indique une gravité (Prioritaire / Attention / Information), une explication, une **preuve calculée** (valeurs issues du moteur : durée, marges, décomptes, successeurs) et une action suggérée. Aucun service externe n'est contacté et aucun chiffre n'est inventé. Depuis la fiche d'un projet, le bouton **💡 Conseils (IA maison)** ouvre une page dédiée réservée au propriétaire.

### 8.3 Assistant IA conversationnel

Le lien **Assistant IA** de la navigation ouvre `/ia/discussion`. Choisissez un projet dans la liste, posez votre question en français : le moteur maison reconnaît **16 intentions** (durée totale, chemin critique, marges, tâche précise, dépendances, risques, optimisation, explication PERT, date cible, etc.) et répond avec les **chiffres recalculés** du projet choisi. Chaque réponse affiche ses sources pour permettre la vérification ; si le planning n'est pas calculable, l'assistant le signale au lieu d'avancer un nombre.

Cet espace ne fait **aucun appel à un service externe**, ne demande aucune clé API et n'applique **aucun quota**. Vos échanges restent dans l'historique de la session (20 messages maximum par projet, soit 10 questions et 10 réponses, chacun limité à 1 000 caractères). L'ancien assistant propre à chaque projet reste disponible sur sa fiche via le bouton **🤖 Assistant IA**.

### 8.4 « Se souvenir de moi »

La case **Se souvenir de moi** est présente sur la page de connexion **et** sur celle de création de compte. Si vous la cochez, Flask-Login dépose le cookie persistant `remember_token` : vous restez reconnu après la fermeture du navigateur. Sans cette case, la session reste classique. Le bouton **Déconnexion** supprime ce cookie.

### 8.5 Vérification de l'e-mail à l'inscription

En production, chaque nouveau compte doit être validé par un **code à 6 chiffres** envoyé sur l'adresse e-mail fournie. Après l'inscription, vous êtes conduit sur `/verify` : saisissez le code reçu dans les 15 minutes (5 essais maximum). La validation connecte automatiquement le compte et ouvre l'accueil. Le bouton **Renvoyer le code** génère un nouveau code (au plus une fois par minute).

Le code n'est jamais stocké en clair ni affiché une seconde fois. Si aucun serveur SMTP n'est configuré (développement local), l'application affiche le code une seule fois dans un avertissement et le consigne dans le log serveur : l'inscription reste possible. Les comptes créés avant l'activation de cette fonction restent valides.

### 8.6 Visualisations avancées

Sur la page de résultats, la section **📈 Representations avancées** permet de générer à la demande cinq graphiques SVG : histogramme des durées, histogramme des marges totales, diagramme circulaire critiques / non critiques, nuage de points durée ↔ marge et barres ES/EF/LS/LF. Cochez les graphiques souhaités puis cliquez sur **Générer les visualisations**. Tout est calculé dans le navigateur à partir des chiffres du moteur PERT : aucun service externe n'est appelé et le graphe PERT, le Gantt et les exports restent inchangés.
