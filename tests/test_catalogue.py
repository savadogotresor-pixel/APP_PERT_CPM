"""Tests ciblés du catalogue de projets et de l'import direct.

Chaque test dispose d'une application Flask et d'une base mémoire propres :
aucune dépendance envers l'instance globale de ``app.py`` ni envers l'ordre
d'exécution de la suite.
"""

import html as html_module
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


if __name__ == '__main__':
    unittest.main()
