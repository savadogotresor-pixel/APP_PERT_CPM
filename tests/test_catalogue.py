"""Tests ciblés du catalogue de projets et de l'import direct.

Chaque test dispose d'une application Flask et d'une base mémoire propres :
aucune dépendance envers l'instance globale de ``app.py`` ni envers l'ordre
d'exécution de la suite.
"""

import copy
import html as html_module
import json
import re
import unittest
from urllib.parse import parse_qs, urlsplit

from flask import Blueprint, Flask, g

from config import TestConfig
from extensions import csrf, db, login_manager
from logic import catalogue as catalogue_logic
from logic.pert import compute
from models import AuditLog, Dependency, Project, Task, User, project_to_taches
from routes_catalogue import catalogue_bp
from routes_projects import projects_bp


class CatalogueTestCase(unittest.TestCase):
    """Graphes valides, accès réservé aux connectés, CSRF et optimisation."""

    def setUp(self):
        self.app = Flask(
            __name__,
            template_folder='../templates',
            static_folder='../static',
        )
        self.app.config.from_object(TestConfig)
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.app.config['SECRET_KEY'] = 'test-catalogue'

        db.init_app(self.app)
        login_manager.init_app(self.app)
        self._login_view_initial = login_manager.login_view
        login_manager.login_view = 'auth.login'
        csrf.init_app(self.app)

        @self.app.route('/')
        def accueil():
            return 'accueil'

        @login_manager.user_loader
        def charger_utilisateur_catalogue(identifiant):
            return db.session.get(User, int(identifiant))

        # Endpoints minimaux attendus par les templates et Flask-Login.
        auth_dummy = Blueprint('auth', __name__)

        @auth_dummy.route('/login')
        def login():
            return 'login'

        @auth_dummy.route('/logout')
        def logout():
            return 'logout'

        @auth_dummy.route('/profile')
        def profile():
            return 'profil'

        self.app.register_blueprint(auth_dummy)
        self.app.register_blueprint(projects_bp)
        self.app.register_blueprint(catalogue_bp)

        self.contexte = self.app.app_context()
        self.contexte.push()
        db.create_all()

        utilisateur = User(username='catalogue_user', email='catalogue@example.com')
        utilisateur.set_password('Password123')
        db.session.add(utilisateur)
        db.session.commit()
        self.user_id = utilisateur.id

    def tearDown(self):
        login_manager.login_view = self._login_view_initial
        db.session.remove()
        db.drop_all()
        self.contexte.pop()

    def _client_connecte(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True
        return client

    @staticmethod
    def _html(response):
        return response.get_data(as_text=True)

    def _verifier_redirection_login(self, response, destination):
        emplacement = response.headers.get('Location', '')
        morceaux = urlsplit(emplacement)
        self.assertEqual('/login', morceaux.path, msg=emplacement)
        self.assertEqual([destination], parse_qs(morceaux.query).get('next'))

    @staticmethod
    def _jeton_csrf(html):
        correspondance = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
        assert correspondance is not None, 'Jeton CSRF absent du formulaire'
        return correspondance.group(1)

    @staticmethod
    def _derniere_feuille_style(html):
        entete = html.split('</head>')[0]
        liens = re.findall(r'<link[^>]+rel="stylesheet"[^>]*>', entete)
        return liens[-1] if liens else ''

    def _ajouter_tache(self, client, projet_id, **donnees):
        """POST tokenisé sur la route d'ajout, avec la fiche fraîchement lue."""
        html = self._html(client.get(f'/catalogue/{projet_id}'))
        formulaire = {'csrf_token': self._jeton_csrf(html)}
        formulaire.update(donnees)
        return client.post(
            f'/catalogue/{projet_id}/taches', data=formulaire
        )

    @staticmethod
    def _donnees_graphiques(html):
        """Extrait le JSON injecté dans la fiche pour les visualisations."""
        correspondance = re.search(
            r'const DONNEES_GRAPHiques = (\{.*?\});', html, re.DOTALL
        )
        assert correspondance is not None, 'JSON des graphiques absent de la fiche'
        return json.loads(correspondance.group(1))

    # ------------------------------------------------------------------
    # Données du catalogue
    # ------------------------------------------------------------------
    def test_catalogue_contient_des_graphes_valides_et_varies(self):
        projets = catalogue_logic.CATALOGUE
        self.assertGreaterEqual(len(projets), 10)
        self.assertLessEqual(len(projets), 12)

        identifiants = [projet['id'] for projet in projets]
        self.assertEqual(len(identifiants), len(set(identifiants)))
        self.assertGreaterEqual(len({p['categorie'] for p in projets}), 10)

        for projet in projets:
            for cle in ('id', 'titre', 'categorie', 'description', 'icone',
                        'difficulte', 'taches'):
                self.assertIn(cle, projet)
            self.assertTrue(projet['taches'])
            resultat = compute(projet['taches'])
            self.assertGreater(resultat.duree_totale, 0)
            self.assertTrue(resultat.chemins_critiques)
            self.assertEqual(len(resultat.resultats), len(projet['taches']))

        # Au moins un jalon (durée nulle) figure dans le catalogue.
        self.assertTrue(any(
            infos['duree'] == 0
            for projet in projets for infos in projet['taches'].values()
        ))

    def test_get_projet_et_erreurs(self):
        projet = catalogue_logic.get_projet('construction-maison')
        self.assertIsNotNone(projet)
        self.assertEqual(projet['titre'], "Construction d'une maison individuelle")
        self.assertIsNone(catalogue_logic.get_projet('inexistant'))
        with self.assertRaises(ValueError):
            catalogue_logic.optimiser('inexistant', self.user_id)

    # ------------------------------------------------------------------
    # Accès réservé aux utilisateurs connectés
    # ------------------------------------------------------------------
    def test_index_anonyme_redirige_vers_login(self):
        response = self.app.test_client().get('/catalogue/')
        self.assertEqual(302, response.status_code)
        self._verifier_redirection_login(response, '/catalogue/')

    def test_detail_anonyme_redirige_vers_login(self):
        response = self.app.test_client().get('/catalogue/construction-maison')
        self.assertEqual(302, response.status_code)
        self._verifier_redirection_login(
            response, '/catalogue/construction-maison'
        )

    def test_index_connecte_200_affiche_cartes_et_navigation(self):
        response = self._client_connecte().get('/catalogue/')
        self.assertEqual(200, response.status_code)
        html = self._html(response)

        self.assertIn('Catalogue de projets', html)
        self.assertNotIn('📊 Démo (12 tâches)', html)
        self.assertIn('🧰 Catalogue', html)
        self.assertNotIn('Saisie des tâches', html)
        self.assertNotIn('Saisie', html)
        self.assertEqual(len(catalogue_logic.CATALOGUE), html.count('Voir le projet'))
        contenu = html_module.unescape(html)
        for projet in catalogue_logic.CATALOGUE:
            self.assertIn(projet['titre'], contenu)
            self.assertIn(str(compute(projet['taches']).duree_totale), contenu)
        self.assertIn('design.css', self._derniere_feuille_style(html))

        # La navigation ne montre que les rubriques des utilisateurs connectés.
        self.assertIn('Mes projets', html)
        self.assertIn('Déconnexion', html)

    def test_detail_connecte_200_tableau_et_formulaire(self):
        client = self._client_connecte()
        response = client.get('/catalogue/construction-maison')
        self.assertEqual(200, response.status_code)
        html = self._html(response)
        self.assertIn('🧰 Catalogue', html)

        projet = catalogue_logic.get_projet('construction-maison')
        resultat = compute(projet['taches'])
        contenu = html_module.unescape(html)
        self.assertIn(projet['titre'], contenu)
        self.assertIn(f'{resultat.duree_totale} j', contenu)
        self.assertIn('Optimiser ce projet', html)
        self.assertIn('name="csrf_token"', html)
        self.assertIn('🔥 Chemin critique', html)
        self.assertIn(' → '.join(resultat.chemins_critiques[0]), html)
        for code in resultat.ordre:
            self.assertIn(f'<strong>{code}</strong>', html)
        self.assertIn('design.css', self._derniere_feuille_style(html))
        self.assertNotIn('Saisie des tâches', html)

    def test_detail_inconnu_404(self):
        response = self._client_connecte().get('/catalogue/inconnu')
        self.assertEqual(404, response.status_code)

    # ------------------------------------------------------------------
    # Flux d'optimisation
    # ------------------------------------------------------------------
    def test_optimisation_connecte_cree_projet_et_redirige(self):
        modele = catalogue_logic.get_projet('application-mobile')
        client = self._client_connecte()
        html = self._html(client.get('/catalogue/application-mobile'))
        jeton = self._jeton_csrf(html)

        response = client.post(
            '/catalogue/application-mobile/optimiser',
            data={'csrf_token': jeton},
        )
        self.assertEqual(302, response.status_code)
        self.assertNotIn('/login', response.headers.get('Location', ''))

        with self.app.app_context():
            projet = Project.query.filter_by(name=modele['titre']).first()
            self.assertIsNotNone(projet)
            self.assertEqual(self.user_id, projet.user_id)
            self.assertEqual(len(modele['taches']), len(projet.tasks))
            self.assertEqual(
                sum(len(infos['depend_de']) for infos in modele['taches'].values()),
                Dependency.query.filter_by().count(),
            )
            self.assertEqual(
                {code: infos['duree'] for code, infos in modele['taches'].items()},
                {tache.code: tache.duree for tache in projet.tasks},
            )
            self.assertEqual(
                compute(modele['taches']).duree_totale,
                compute(project_to_taches(projet)).duree_totale,
            )
            self.assertEqual(
                1,
                AuditLog.query.filter_by(
                    user_id=self.user_id, action='CATALOGUE_OPTIMISE'
                ).count(),
            )
            projet_id = projet.id

        self.assertIn(
            f'/projects/{projet_id}/optimisation', response.headers.get('Location', '')
        )
        page = client.get(response.headers['Location'])
        self.assertEqual(200, page.status_code)

    def test_optimisation_anonyme_redirige_vers_login(self):
        connecte = self._client_connecte()
        jeton = self._jeton_csrf(self._html(connecte.get('/catalogue/mariage')))
        with connecte.session_transaction() as session:
            jeton_brut = session['csrf_token']

        # Client réellement anonyme, mais avec le jeton CSRF de session : la
        # redirection Flask-Login doit intervenir avant toute copie en base.
        # Le contexte applicatif partagé par les tests conserve l'utilisateur
        # dans ``g`` : on purge ce cache avant la requête anonyme.
        g.pop('_login_user', None)
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['csrf_token'] = jeton_brut

        with self.app.app_context():
            avant = Project.query.count()

        response = client.post(
            '/catalogue/mariage/optimiser', data={'csrf_token': jeton}
        )
        self.assertEqual(302, response.status_code)
        self._verifier_redirection_login(response, '/catalogue/mariage/optimiser')
        with self.app.app_context():
            self.assertEqual(avant, Project.query.count())

    def test_post_sans_jeton_csrf_est_refuse(self):
        client = self._client_connecte()
        response = client.post('/catalogue/mariage/optimiser', data={})
        self.assertEqual(400, response.status_code)

    # ------------------------------------------------------------------
    # Ajout de tâches à un exemplaire personnel
    # ------------------------------------------------------------------
    def test_ajout_tache_connecte_cree_copie_recalcule_et_preserve_modele(self):
        projet_id = 'construction-maison'
        modele = catalogue_logic.get_projet(projet_id)
        catalogue_avant = copy.deepcopy(catalogue_logic.CATALOGUE)
        graphe_attendu = dict(modele['taches'])
        graphe_attendu['N'] = {
            'description': 'Recette fonctionnelle',
            'duree': 5,
            'depend_de': ['M'],
        }
        resultat_attendu = compute(graphe_attendu)
        self.assertGreater(
            resultat_attendu.duree_totale,
            compute(modele['taches']).duree_totale,
        )

        client = self._client_connecte()
        response = self._ajouter_tache(
            client, projet_id, code='N', description='Recette fonctionnelle',
            duree='5', predecesseurs='M',
        )
        self.assertEqual(302, response.status_code)
        self.assertIn(
            f'/catalogue/{projet_id}#ajout-tache',
            response.headers.get('Location', ''),
        )

        with self.app.app_context():
            copie = catalogue_logic.trouver_copie(projet_id, self.user_id)
            self.assertIsNotNone(copie)
            self.assertEqual(self.user_id, copie.user_id)
            self.assertEqual(len(modele['taches']) + 1, len(copie.tasks))
            tache = next(t for t in copie.tasks if t.code == 'N')
            self.assertEqual('Recette fonctionnelle', tache.description)
            self.assertEqual(5, tache.duree)
            self.assertEqual(['M'], [d.predecessor.code for d in tache.dependances])
            self.assertEqual(
                resultat_attendu.duree_totale,
                compute(project_to_taches(copie)).duree_totale,
            )
            self.assertEqual(
                1,
                AuditLog.query.filter_by(
                    user_id=self.user_id, action='CATALOGUE_TACHE_AJOUTEE'
                ).count(),
            )
            copie_id = copie.id

        page = client.get(f'/catalogue/{projet_id}')
        contenu = html_module.unescape(self._html(page))
        self.assertIn(f'{resultat_attendu.duree_totale} j', contenu)
        self.assertIn(f'/projects/{copie_id}', contenu)
        self.assertEqual(catalogue_avant, catalogue_logic.CATALOGUE)

    def test_second_ajout_reutilise_la_meme_copie(self):
        projet_id = 'mariage'
        modele = catalogue_logic.get_projet(projet_id)
        catalogue_avant = copy.deepcopy(catalogue_logic.CATALOGUE)
        client = self._client_connecte()

        premiere = self._ajouter_tache(
            client, projet_id, code='M', description='Réservation du traiteur',
            duree='2', predecesseurs='L',
        )
        seconde = self._ajouter_tache(
            client, projet_id, code='N', description='Envoi des faire-part',
            duree='3', predecesseurs='M',
        )
        self.assertEqual(302, premiere.status_code)
        self.assertEqual(302, seconde.status_code)

        graphe_attendu = dict(modele['taches'])
        graphe_attendu['M'] = {
            'description': 'Réservation du traiteur', 'duree': 2,
            'depend_de': ['L'],
        }
        graphe_attendu['N'] = {
            'description': 'Envoi des faire-part', 'duree': 3,
            'depend_de': ['M'],
        }
        with self.app.app_context():
            self.assertEqual(1, Project.query.count())
            copie = catalogue_logic.trouver_copie(projet_id, self.user_id)
            self.assertIsNotNone(copie)
            self.assertEqual(len(modele['taches']) + 2, len(copie.tasks))
            self.assertEqual(
                {'M', 'N'},
                {t.code for t in copie.tasks} - set(modele['taches']),
            )
            self.assertEqual(
                2,
                AuditLog.query.filter_by(
                    user_id=self.user_id, action='CATALOGUE_TACHE_AJOUTEE'
                ).count(),
            )
            self.assertEqual(
                compute(graphe_attendu).duree_totale,
                compute(project_to_taches(copie)).duree_totale,
            )
        self.assertEqual(catalogue_avant, catalogue_logic.CATALOGUE)

    def test_optimisation_reprend_exemplaire_deja_enrichi(self):
        """Le bouton d'optimisation ne recrée pas un exemplaire déjà enrichi."""
        projet_id = 'mariage'
        client = self._client_connecte()
        self._ajouter_tache(
            client, projet_id, code='M', description='Traiteur',
            duree='2', predecesseurs='L',
        )
        with self.app.app_context():
            copie = catalogue_logic.trouver_copie(projet_id, self.user_id)
            copie_id = copie.id

        html = self._html(client.get(f'/catalogue/{projet_id}'))
        response = client.post(
            f'/catalogue/{projet_id}/optimiser',
            data={'csrf_token': self._jeton_csrf(html)},
        )
        self.assertEqual(302, response.status_code)
        self.assertIn(
            f'/projects/{copie_id}/optimisation',
            response.headers.get('Location', ''),
        )
        with self.app.app_context():
            self.assertEqual(1, Project.query.count())
            copie = db.session.get(Project, copie_id)
            self.assertEqual(
                {'M'}, {t.code for t in copie.tasks} - set(
                    catalogue_logic.get_projet(projet_id)['taches']
                ),
            )

    def test_fiche_affiche_tache_ajoutee_et_badge_personnel(self):
        projet_id = 'festival'
        modele = catalogue_logic.get_projet(projet_id)
        client = self._client_connecte()
        response = self._ajouter_tache(
            client, projet_id, code='N', description='Bilan de sécurité renforcé',
            duree='4', predecesseurs='M',
        )
        self.assertEqual(302, response.status_code)

        page = client.get(f'/catalogue/{projet_id}')
        self.assertEqual(200, page.status_code)
        html = self._html(page)
        contenu = html_module.unescape(html)
        self.assertIn('Exemplaire personnel', contenu)
        self.assertIn('Tâches de mon exemplaire', contenu)
        self.assertIn('<strong>N</strong>', html)
        self.assertIn('Bilan de sécurité renforcé', contenu)
        self.assertIn('➕ Ajouter une tâche à mon exemplaire', contenu)
        with self.app.app_context():
            copie = catalogue_logic.trouver_copie(projet_id, self.user_id)
            self.assertIsNotNone(copie)
            self.assertIn(f'/projects/{copie.id}', contenu)
            self.assertEqual(
                len(modele['taches']) + 1, len(project_to_taches(copie))
            )
        self.assertIn('design.css', self._derniere_feuille_style(html))

    def test_ajouts_invalides_refuses_sans_ecriture(self):
        projet_id = 'refonte-site-web'
        catalogue_avant = copy.deepcopy(catalogue_logic.CATALOGUE)
        client = self._client_connecte()
        cas = [
            ('code dupliqué',
             {'code': 'A', 'description': 'Doublon', 'duree': '2', 'predecesseurs': ''},
             'existe déjà'),
            ('prédécesseur inconnu',
             {'code': 'N', 'description': 'Test', 'duree': '2', 'predecesseurs': 'ZZZ'},
             "n'existe pas"),
            ('durée non numérique',
             {'code': 'N', 'description': 'Test', 'duree': 'abc', 'predecesseurs': ''},
             'La durée doit être un entier'),
            ('durée négative',
             {'code': 'N', 'description': 'Test', 'duree': '-1', 'predecesseurs': ''},
             'La durée doit être un entier'),
            ('auto-dépendance',
             {'code': 'N', 'description': 'Test', 'duree': '2', 'predecesseurs': 'N'},
             "d'elle-même"),
            ('code avec virgule',
             {'code': 'N,X', 'description': 'Test', 'duree': '2', 'predecesseurs': ''},
             'ne peut pas contenir de virgule'),
        ]
        for titre, donnees, message in cas:
            with self.subTest(cas=titre):
                response = self._ajouter_tache(client, projet_id, **donnees)
                self.assertEqual(200, response.status_code)
                html = self._html(response)
                self.assertIn('La tâche n\'a pas été ajoutée', html)
                self.assertIn(message, html_module.unescape(html))
                with self.app.app_context():
                    self.assertEqual(0, Project.query.count())
                    self.assertEqual(0, Task.query.count())
                    self.assertEqual(0, Dependency.query.count())
                    self.assertEqual(0, AuditLog.query.count())
                self.assertEqual(catalogue_avant, catalogue_logic.CATALOGUE)

        # Les valeurs saisies sont conservées pour permettre la correction.
        html = self._html(self._ajouter_tache(
            client, projet_id, code='Nope', description='Conserver ce texte',
            duree='abc', predecesseurs='K',
        ))
        self.assertIn('value="Nope"', html)
        self.assertIn('Conserver ce texte', html)
        self.assertIn('value="K"', html)

    def test_ajout_tache_anonyme_redirige_vers_login_sans_ecrire(self):
        projet_id = 'these-recherche'
        connecte = self._client_connecte()
        jeton = self._jeton_csrf(self._html(connecte.get(f'/catalogue/{projet_id}')))
        with connecte.session_transaction() as session:
            jeton_brut = session['csrf_token']

        # Même jeton CSRF, mais requête réellement anonyme : Flask-Login doit
        # refuser avant toute création de copie (le cache ``g`` est purgé).
        g.pop('_login_user', None)
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['csrf_token'] = jeton_brut
        with self.app.app_context():
            avant = Project.query.count()

        response = client.post(
            f'/catalogue/{projet_id}/taches',
            data={'csrf_token': jeton, 'code': 'N', 'description': 'Test',
                  'duree': '1', 'predecesseurs': ''},
        )
        self.assertEqual(302, response.status_code)
        self._verifier_redirection_login(
            response, f'/catalogue/{projet_id}/taches'
        )
        with self.app.app_context():
            self.assertEqual(avant, Project.query.count())

    def test_ajout_tache_sans_jeton_csrf_est_refuse(self):
        client = self._client_connecte()
        with self.app.app_context():
            avant = Project.query.count()
        response = client.post(
            '/catalogue/mariage/taches',
            data={'code': 'M', 'description': 'Test', 'duree': '1',
                  'predecesseurs': ''},
        )
        self.assertEqual(400, response.status_code)
        with self.app.app_context():
            self.assertEqual(avant, Project.query.count())
            self.assertEqual(0, AuditLog.query.count())

    def test_fiche_cinq_representations_et_json_pert_coherent(self):
        projet_id = 'campagne-marketing'
        modele = catalogue_logic.get_projet(projet_id)
        resultat = compute(modele['taches'])
        html = self._html(self._client_connecte().get(f'/catalogue/{projet_id}'))

        self.assertIn('📈 Representations avancées', html)
        for cle in ('durees', 'marges', 'criticite', 'nuage', 'calendrier'):
            self.assertIn(f'data-graphique="{cle}"', html)
        self.assertIn('Graphiques.genererTous()', html)
        self.assertIn('id="graphiques-resultats"', html)
        self.assertIn('id="graphiques-invite"', html)
        self.assertIn('graphiques.css', html)
        self.assertIn('graphiques.js', html)
        self.assertLess(html.index('graphiques.css'), html.index('design.css'))
        self.assertIn('design.css', self._derniere_feuille_style(html))
        self.assertIn('window.DONNEES_GRAPHiques', html)

        donnees = self._donnees_graphiques(html)
        self.assertEqual(set(modele['taches']), set(donnees['taches']))
        self.assertEqual(len(modele['taches']), len(donnees['taches']))
        self.assertEqual(resultat.resultats, donnees['resultats'])
        self.assertEqual(resultat.chemins_critiques, donnees['chemins'])
        self.assertEqual(
            resultat.duree_totale, compute(donnees['taches']).duree_totale
        )

    def test_graphiques_de_la_fiche_refletent_les_taches_ajoutees(self):
        projet_id = 'formation-en-ligne'
        modele = catalogue_logic.get_projet(projet_id)
        client = self._client_connecte()
        response = self._ajouter_tache(
            client, projet_id, code='O', description='Quiz de certification',
            duree='7', predecesseurs='N',
        )
        self.assertEqual(302, response.status_code)

        html = self._html(client.get(f'/catalogue/{projet_id}'))
        donnees = self._donnees_graphiques(html)
        self.assertEqual(len(modele['taches']) + 1, len(donnees['taches']))
        self.assertIn('O', donnees['taches'])
        self.assertEqual(7, donnees['taches']['O']['duree'])
        self.assertEqual(['N'], donnees['taches']['O']['depend_de'])
        self.assertIn('O', donnees['resultats'])

        graphe_attendu = dict(modele['taches'])
        graphe_attendu['O'] = {
            'description': 'Quiz de certification', 'duree': 7,
            'depend_de': ['N'],
        }
        resultat_attendu = compute(graphe_attendu)
        self.assertEqual(
            resultat_attendu.duree_totale, compute(donnees['taches']).duree_totale
        )
        self.assertEqual(resultat_attendu.resultats, donnees['resultats'])
        self.assertIn(f'{resultat_attendu.duree_totale} j', html_module.unescape(html))


if __name__ == '__main__':
    unittest.main()
