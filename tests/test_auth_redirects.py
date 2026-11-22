"""Tests de non-régression des redirections auth et de la navigation.

Vérifie que l'inscription et la connexion reviennent à l'accueil par défaut,
que le paramètre ``next`` local est prioritaire (sans ouvrir de redirection),
que la case « Se souvenir de moi » persiste la session et que les liens
démonstration/suppression de la barre de navigation ne réapparaissent pas.
"""

import os
import re
import unittest
import uuid
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, unquote, urlsplit

from sqlalchemy import create_engine

from models import User


class AuthRedirectsTestCase(unittest.TestCase):
    """Scénarios HTTP contre l'instance Flask globale (base mémoire dédiée)."""

    MOT_DE_PASSE = 'Password123'

    @classmethod
    def setUpClass(cls):
        # APP_SETTINGS doit être choisi avant le premier import de app.py.
        os.environ['APP_SETTINGS'] = 'config.TestConfig'
        from app import app, db

        cls.app = app
        cls.db = app.extensions.get('sqlalchemy', db)
        app.config.from_object('config.TestConfig')
        app.config.update(TESTING=True)

        # Un autre module peut avoir importé app avec une base fichier : on
        # réapplique la configuration de test puis on remplace le moteur attaché.
        with app.app_context():
            cls.db.session.remove()
            moteur = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
            moteurs = cls.db._app_engines.setdefault(app, {})
            ancien = moteurs.get(None)
            if ancien is not None:
                ancien.dispose()
            moteurs[None] = moteur
            cls.db.create_all()

        cls.moteur = moteur
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            cls.db.drop_all()
            cls.db.session.remove()
        cls.moteur.dispose()
        os.environ.pop('APP_SETTINGS', None)

    def setUp(self):
        csrf_initial = self.app.config.get('WTF_CSRF_ENABLED', True)
        self.addCleanup(
            self.app.config.__setitem__, 'WTF_CSRF_ENABLED', csrf_initial
        )
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    @staticmethod
    def _chemin(response):
        """Chemin de l'en-tête Location (absolu ou relatif)."""
        return urlsplit(response.headers.get('Location', '')).path

    @staticmethod
    def _html(response):
        return response.get_data(as_text=True)

    @staticmethod
    def _valeur_cookie(response, nom):
        """Lit la valeur d'un cookie dans les en-têtes Set-Cookie."""
        cookies = SimpleCookie()
        for entete in response.headers.getlist('Set-Cookie'):
            cookies.load(entete)
        if nom not in cookies:
            return None
        return unquote(cookies[nom].value)

    def _verifier_redirection_login(self, reponse, destination):
        emplacement = reponse.headers.get('Location', '')
        morceaux = urlsplit(emplacement)
        self.assertEqual('/login', morceaux.path, msg=emplacement)
        self.assertEqual([destination], parse_qs(morceaux.query).get('next'))

    @staticmethod
    def _nav(html):
        """Extrait le contenu de ``ul.topbar-nav`` d'une page rendue."""
        correspondance = re.search(
            r'<ul\s+class="topbar-nav"[^>]*>(.*?)</ul>', html, re.S
        )
        assert correspondance is not None, 'Barre de navigation introuvable'
        return correspondance.group(1)

    @staticmethod
    def _identifiants():
        suffixe = uuid.uuid4().hex[:12]
        return f'auth_{suffixe}', f'{suffixe}@example.test'

    def _supprimer_utilisateur(self, user_id):
        with self.app.app_context():
            self.db.session.remove()
            utilisateur = self.db.session.get(User, user_id)
            if utilisateur is not None:
                self.db.session.delete(utilisateur)
                self.db.session.commit()

    def _supprimer_par_identifiants(self, username, email):
        with self.app.app_context():
            self.db.session.remove()
            pour_username = User.query.filter_by(username=username).all()
            pour_email = User.query.filter_by(email=email).all()
            for utilisateur in set(pour_username + pour_email):
                self.db.session.delete(utilisateur)
            self.db.session.commit()

    def _creer_utilisateur(self):
        """Utilisateur éphémère inséré en base et supprimé en fin de test."""
        username, email = self._identifiants()
        with self.app.app_context():
            utilisateur = User(username=username, email=email)
            utilisateur.set_password(self.MOT_DE_PASSE)
            self.db.session.add(utilisateur)
            self.db.session.commit()
            user_id = utilisateur.id
        self.addCleanup(self._supprimer_utilisateur, user_id)
        return username, email

    def _donnees_inscription(self, remember=False):
        username, email = self._identifiants()
        self.addCleanup(self._supprimer_par_identifiants, username, email)
        donnees = {
            'username': username,
            'email': email,
            'password': self.MOT_DE_PASSE,
            'password_confirm': self.MOT_DE_PASSE,
        }
        if remember:
            donnees['remember'] = '1'
        return donnees, username

    def _donnees_connexion(self, identifiant, next_url=None, remember=False):
        donnees = {
            'username': identifiant,
            'email': identifiant,
            'password': self.MOT_DE_PASSE,
        }
        if next_url is not None:
            donnees['next'] = next_url
        if remember:
            donnees['remember'] = '1'
        return donnees

    # ------------------------------------------------------------------
    # 1. Inscription sans next
    # ------------------------------------------------------------------
    def test_inscription_sans_next_redirige_accueil(self):
        donnees, _username = self._donnees_inscription()
        reponse = self.client.post('/register', data=donnees)

        self.assertEqual(302, reponse.status_code, msg=self._html(reponse)[:2000])
        self.assertEqual('/', self._chemin(reponse))

    # ------------------------------------------------------------------
    # 2. Connexion sans next
    # ------------------------------------------------------------------
    def test_connexion_sans_next_redirige_accueil(self):
        username, _email = self._creer_utilisateur()
        reponse = self.client.post(
            '/login', data=self._donnees_connexion(username)
        )

        self.assertEqual(302, reponse.status_code, msg=self._html(reponse)[:2000])
        self.assertEqual('/', self._chemin(reponse))

    # ------------------------------------------------------------------
    # 3. next local prioritaire, next externe refusé
    # ------------------------------------------------------------------
    def test_connexion_next_local_prioritaire_et_externe_refuse(self):
        username, _email = self._creer_utilisateur()

        local = self.client.post(
            '/login',
            data=self._donnees_connexion(username, next_url='/projects/'),
        )
        self.assertEqual(302, local.status_code, msg=self._html(local)[:2000])
        self.assertEqual('/projects/', self._chemin(local))

        self.client.get('/logout')
        externe = self.client.post(
            '/login',
            data=self._donnees_connexion(username, next_url='https://evil.test'),
        )
        self.assertEqual(302, externe.status_code, msg=self._html(externe)[:2000])
        self.assertEqual('/', self._chemin(externe))

    # ------------------------------------------------------------------
    # 4. Case « Se souvenir de moi » dans les deux formulaires
    # ------------------------------------------------------------------
    def test_case_se_souvenir_presente_sur_login_et_register(self):
        for chemin in ('/login', '/register'):
            with self.subTest(chemin=chemin):
                html = self._html(self.client.get(chemin))
                self.assertRegex(
                    html,
                    r'<input[^>]+name="remember"[^>]+value="1"',
                )

    # ------------------------------------------------------------------
    # 5. Cookie remember_token après inscription et après connexion
    # ------------------------------------------------------------------
    def test_cookie_remember_token_apres_inscription_et_connexion(self):
        donnees, _username = self._donnees_inscription(remember=True)
        inscription = self.client.post('/register', data=donnees)
        self.assertEqual(302, inscription.status_code)
        self.assertIsNotNone(
            self._valeur_cookie(inscription, 'remember_token'),
            msg='Cookie remember_token absent après inscription',
        )

        username, _email = self._creer_utilisateur()
        client_connexion = self.app.test_client()
        connexion = client_connexion.post(
            '/login', data=self._donnees_connexion(username, remember=True)
        )
        self.assertEqual(302, connexion.status_code)
        self.assertIsNotNone(
            self._valeur_cookie(connexion, 'remember_token'),
            msg='Cookie remember_token absent après connexion',
        )

    # ------------------------------------------------------------------
    # 6. Fermeture du navigateur : cookie persistant uniquement
    # ------------------------------------------------------------------
    def test_cookie_seul_restaure_la_session_apres_fermeture(self):
        username, _email = self._creer_utilisateur()
        client_ferme = self.app.test_client()
        connexion = client_ferme.post(
            '/login', data=self._donnees_connexion(username, remember=True)
        )
        jeton = self._valeur_cookie(connexion, 'remember_token')
        self.assertIsNotNone(jeton)

        # Nouveau client : aucun cookie de session, seulement le cookie persistant.
        client_reouvert = self.app.test_client()
        self.assertIsNone(client_reouvert.get_cookie('session'))
        client_reouvert.set_cookie('remember_token', jeton)

        accueil = client_reouvert.get('/')
        self.assertEqual(200, accueil.status_code, msg=self._html(accueil)[:2000])
        self.assertIn(username, self._html(accueil))

    # ------------------------------------------------------------------
    # 7. Navigation : Catalogue réservé aux utilisateurs connectés
    # ------------------------------------------------------------------
    def test_navigation_anonyme_sans_catalogue_sur_login_et_register(self):
        for chemin in ('/login', '/register'):
            with self.subTest(chemin=chemin):
                reponse = self.client.get(chemin)
                self.assertEqual(
                    200, reponse.status_code, msg=self._html(reponse)[:2000]
                )
                nav = self._nav(self._html(reponse))
                self.assertNotIn('Catalogue', nav)
                self.assertNotIn('Démo', nav)
                self.assertNotIn('Saisie', nav)

    def test_catalogue_anonyme_redirige_vers_login(self):
        reponse = self.client.get('/catalogue/')
        self.assertEqual(302, reponse.status_code)
        self._verifier_redirection_login(reponse, '/catalogue/')

    def test_demo_anonyme_sans_lien_catalogue(self):
        reponse = self.client.get('/demo')
        if reponse.status_code == 302:
            # Si la démo est protégée, vérifier seulement la redirection :
            # il n'y a pas de navigation à tester sur une telle réponse.
            self._verifier_redirection_login(reponse, '/demo')
            return
        # Tant que /demo reste public, aucun lien Catalogue n'est affiché.
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        self.assertNotIn('Catalogue', self._nav(self._html(reponse)))

    def test_navigation_connecte_affiche_catalogue(self):
        username, _email = self._creer_utilisateur()
        connexion = self.client.post(
            '/login', data=self._donnees_connexion(username)
        )
        self.assertEqual(302, connexion.status_code)

        for chemin in ('/login', '/register', '/catalogue/', '/demo'):
            with self.subTest(chemin=chemin):
                reponse = self.client.get(chemin)
                self.assertEqual(
                    200, reponse.status_code, msg=self._html(reponse)[:2000]
                )
                nav = self._nav(self._html(reponse))
                self.assertIn('Catalogue', nav)


if __name__ == '__main__':
    unittest.main()
