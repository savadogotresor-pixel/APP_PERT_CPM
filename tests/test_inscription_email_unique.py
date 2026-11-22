"""Le compte créé avec une adresse e-mail est unique et reste connectable.

Ces tests utilisent une application Flask et une base SQLite isolées : ils
vérifient qu'une seconde inscription avec le même e-mail ne crée pas de
doublon et que la connexion fonctionne depuis un nouveau client.
"""

import os
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from flask import Flask

from config import TestConfig
from extensions import csrf, db, login_manager
from models import User, init_db
from routes_auth import auth_bp
from routes_catalogue import catalogue_bp
from routes_projects import projects_bp
from services import mail as service_mail

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOT_DE_PASSE = 'Password123'


def _charger_utilisateur(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


class InscriptionEmailUniqueTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(
            'test_inscription_email_unique',
            template_folder=os.path.join(RACINE, 'templates'),
            static_folder=os.path.join(RACINE, 'static'),
        )
        self.app.config.from_object(TestConfig)
        self.app.config.update(
            SECRET_KEY='secret-inscription-unique',
            WTF_CSRF_ENABLED=False,
            EMAIL_VERIFICATION_ENABLED=False,
            MAIL_SERVER='',
        )

        @self.app.route('/')
        def accueil():
            return 'ACCUEIL-PERT-CPM'

        @self.app.route('/offline', endpoint='offline')
        def offline():
            return 'HORS-LIGNE'

        self._login_view_initial = login_manager.login_view
        self._loader_initial = login_manager.user_callback
        db.init_app(self.app)
        login_manager.init_app(self.app)
        login_manager.login_view = 'auth.login'
        login_manager.user_loader(_charger_utilisateur)
        csrf.init_app(self.app)
        self.app.register_blueprint(auth_bp)
        self.app.register_blueprint(catalogue_bp, url_prefix='/catalogue')
        self.app.register_blueprint(projects_bp, url_prefix='/projects')
        init_db(self.app)

        self.contexte = self.app.app_context()
        self.contexte.push()
        self.email = f'compte_{uuid.uuid4().hex[:12]}@example.test'
        self.envois = []
        correctif_smtp = patch.object(
            service_mail, 'smtp_configure', return_value=True
        )
        correctif_envoi = patch.object(
            service_mail, 'envoyer_code', side_effect=self._faux_envoi
        )
        correctif_smtp.start()
        correctif_envoi.start()
        self.addCleanup(correctif_envoi.stop)
        self.addCleanup(correctif_smtp.stop)
        self.addCleanup(self._nettoyer)

    def _faux_envoi(self, email, code, username):
        self.envois.append({'email': email, 'code': code, 'username': username})
        return True

    def _nettoyer(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.contexte.pop()
        login_manager.login_view = self._login_view_initial
        login_manager._user_callback = self._loader_initial

    def _inscrire(self, client, **extras):
        donnees = {
            'email': self.email,
            'password': MOT_DE_PASSE,
            'password_confirm': MOT_DE_PASSE,
        }
        donnees.update(extras)
        return client.post('/register', data=donnees)

    def _utilisateur(self):
        return User.query.filter_by(email=self.email).one_or_none()

    def test_inscription_avec_email_seul_et_connexion_nouveau_client(self):
        client = self.app.test_client()
        reponse = self._inscrire(client)
        self.assertEqual(302, reponse.status_code)
        self.assertEqual('/', urlsplit(reponse.headers['Location']).path)

        utilisateur = self._utilisateur()
        self.assertIsNotNone(utilisateur)
        self.assertEqual(self.email.split('@')[0], utilisateur.username)
        self.assertTrue(utilisateur.check_password(MOT_DE_PASSE))

        nouveau_client = self.app.test_client()
        connexion = nouveau_client.post(
            '/login', data={'username': self.email, 'password': MOT_DE_PASSE}
        )
        self.assertEqual(302, connexion.status_code)
        self.assertEqual('/', urlsplit(connexion.headers['Location']).path)

    def test_email_deja_enregistre_redirige_connexion_sans_duplicata(self):
        client = self.app.test_client()
        self._inscrire(client, username='PremierCompte')
        self.assertEqual(1, User.query.filter_by(email=self.email).count())

        seconde = self._inscrire(client, username='SecondCompte')
        self.assertEqual(302, seconde.status_code)
        morceaux = urlsplit(seconde.headers['Location'])
        self.assertEqual('/login', morceaux.path)
        self.assertEqual([self.email], parse_qs(morceaux.query).get('email'))
        self.assertEqual(1, User.query.filter_by(email=self.email).count())

        page = self.app.test_client().get(seconde.headers['Location'])
        self.assertIn(f'value="{self.email}"', page.get_data(as_text=True))

        connexion = self.app.test_client().post(
            '/login', data={'username': self.email, 'password': MOT_DE_PASSE}
        )
        self.assertEqual(302, connexion.status_code)
        self.assertEqual('/', urlsplit(connexion.headers['Location']).path)

    def test_compte_non_verifie_reprend_verification_sans_duplicata(self):
        self.app.config['EMAIL_VERIFICATION_ENABLED'] = True
        client = self.app.test_client()

        premiere = self._inscrire(client)
        self.assertEqual(302, premiere.status_code)
        self.assertEqual('/verify', urlsplit(premiere.headers['Location']).path)
        self.assertEqual(1, len(self.envois))
        code = self.envois[-1]['code']

        seconde = self._inscrire(client, username='AutreNom')
        self.assertEqual(302, seconde.status_code)
        self.assertEqual('/verify', urlsplit(seconde.headers['Location']).path)
        self.assertEqual(1, User.query.filter_by(email=self.email).count())

        validation = client.post('/verify', data={'code': code})
        self.assertEqual(302, validation.status_code)
        self.assertEqual('/', urlsplit(validation.headers['Location']).path)
        self.assertTrue(self._utilisateur().email_verifie)

        connexion = self.app.test_client().post(
            '/login', data={'username': self.email, 'password': MOT_DE_PASSE}
        )
        self.assertEqual(302, connexion.status_code)
        self.assertEqual('/', urlsplit(connexion.headers['Location']).path)


if __name__ == '__main__':
    unittest.main()
