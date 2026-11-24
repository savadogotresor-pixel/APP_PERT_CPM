"""Tests du parcours « mot de passe oublié » (code HMAC à 6 chiffres).

Le service SMTP est remplacé par un bouchon : aucun e-mail réel n'est envoyé.
Chaque test dispose de sa propre application Flask et de sa propre base mémoire.
"""

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit

from flask import Flask
from werkzeug.security import check_password_hash

from config import TestConfig
from extensions import csrf, db, login_manager
from models import User, init_db
from routes_auth import (
    auth_bp,
    CODE_VERIFICATION_DELAI_RENVOI,
    CODE_VERIFICATION_TENTATIVES_MAX,
)
from services import mail as service_mail


RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANCIEN_MOT_DE_PASSE = 'AncienPassword123'
NOUVEAU_MOT_DE_PASSE = 'NouveauPassword456'


def _charger_utilisateur(user_id):
    """Chargeur Flask-Login partagé, sans dépendance à l'instance globale."""
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


class ResetPasswordTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(
            'test_reset_password',
            template_folder=os.path.join(RACINE, 'templates'),
            static_folder=os.path.join(RACINE, 'static'),
        )
        self.app.config.from_object(TestConfig)
        self.app.config.update(
            SECRET_KEY='secret-reset-test',
            EMAIL_VERIFICATION_ENABLED=False,
            WTF_CSRF_ENABLED=False,
            MAIL_SERVER='',
            MAIL_DEFAULT_SENDER='PERT/CPM <no-reply@example.test>',
        )

        @self.app.route('/')
        def accueil():
            return 'ACCUEIL-PERT-CPM'

        @self.app.route('/offline')
        def offline():
            return 'HORS-LIGNE'

        db.init_app(self.app)
        login_manager.init_app(self.app)
        login_manager.login_view = 'auth.login'
        if login_manager.user_callback is None:
            login_manager.user_loader(_charger_utilisateur)
        csrf.init_app(self.app)
        self.app.register_blueprint(auth_bp)
        init_db(self.app)

        self.client = self.app.test_client()
        self.envois = []
        self.smtp_configure = patch.object(
            service_mail, 'smtp_configure', return_value=True
        )
        self.smtp_configure.start()
        self.addCleanup(self.smtp_configure.stop)
        self.envoyer = patch.object(
            service_mail, 'envoyer_code', side_effect=self._faux_envoi
        )
        self.envoyer.start()
        self.addCleanup(self.envoyer.stop)
        self.addCleanup(self._nettoyer_base)

        suffixe = uuid.uuid4().hex[:12]
        self.username = f'reset_{suffixe}'
        self.email = f'reset_{suffixe}@example.test'
        self.user_id = self._creer_compte()

    def _nettoyer_base(self):
        with self.app.app_context():
            db.session.remove()
            try:
                db.drop_all()
                db.engine.dispose()
            except Exception:
                pass

    def _creer_compte(self):
        with self.app.app_context():
            user = User(username=self.username, email=self.email)
            user.set_password(ANCIEN_MOT_DE_PASSE)
            db.session.add(user)
            db.session.commit()
            return user.id

    def _faux_envoi(self, email, code, username):
        self.envois.append({
            'email': email, 'code': code, 'username': username
        })
        return True

    def _utilisateur(self):
        with self.app.app_context():
            return db.session.get(User, self.user_id)

    @staticmethod
    def _chemin(response):
        return urlsplit(response.headers.get('Location', '')).path

    @staticmethod
    def _html(response):
        return response.get_data(as_text=True)

    def _demander_code(self, email=None):
        return self.client.post(
            '/password/reset', data={'email': email or self.email}
        )

    def _soumettre_code(self, code, mot_de_passe=NOUVEAU_MOT_DE_PASSE,
                        confirmation=None):
        return self.client.post('/password/reset', data={
            'code': code,
            'password': mot_de_passe,
            'password_confirm': confirmation if confirmation is not None
            else mot_de_passe,
        })

    def test_lien_present_sur_la_page_connexion(self):
        reponse = self.client.get('/login')
        self.assertEqual(200, reponse.status_code)
        self.assertIn('/password/reset', self._html(reponse))
        self.assertIn('Mot de passe oublié', self._html(reponse))

    def test_demande_code_puis_reset_reussite(self):
        reponse = self._demander_code()
        self.assertEqual(302, reponse.status_code)
        self.assertEqual('/password/reset', self._chemin(reponse))
        self.assertEqual(1, len(self.envois))
        self.assertEqual(self.email, self.envois[0]['email'])
        self.assertRegex(self.envois[0]['code'], r'^\d{6}$')

        user = self._utilisateur()
        self.assertIsNotNone(user.code_verification_hash)
        self.assertEqual(0, user.tentatives_code)

        reponse = self._soumettre_code(self.envois[0]['code'])
        self.assertEqual(302, reponse.status_code)
        self.assertEqual('/login', self._chemin(reponse))

        user = self._utilisateur()
        self.assertTrue(check_password_hash(user.password_hash, NOUVEAU_MOT_DE_PASSE))
        self.assertFalse(check_password_hash(user.password_hash, ANCIEN_MOT_DE_PASSE))
        self.assertIsNone(user.code_verification_hash)
        self.assertIsNone(user.code_verification_expire)
        self.assertEqual(0, user.tentatives_code)

        # Les données temporaires de la session sont supprimées.
        reponse = self.client.get('/password/reset')
        self.assertIn('Mot de passe oublié', self._html(reponse))
        self.assertNotIn('Nouveau mot de passe', self._html(reponse))

        connexion = self.client.post('/login', data={
            'username': self.username,
            'password': NOUVEAU_MOT_DE_PASSE,
        })
        self.assertEqual(302, connexion.status_code)
        self.assertEqual('/', self._chemin(connexion))

    def test_adresse_inconnue_ne_revele_pas_le_compte(self):
        reponse = self._demander_code('inconnu@example.test')
        self.assertEqual(200, reponse.status_code)
        self.assertIn('Si cette adresse est enregistrée', self._html(reponse))
        self.assertEqual([], self.envois)

    def test_mauvais_code_incremente_les_tentatives(self):
        self._demander_code()
        code = self.envois[0]['code']
        mauvais = '000000' if code != '000000' else '111111'

        reponse = self._soumettre_code(mauvais)
        self.assertEqual(200, reponse.status_code)
        self.assertIn('Code incorrect', self._html(reponse))
        self.assertEqual(1, self._utilisateur().tentatives_code)

        reponse = self._soumettre_code(code)
        self.assertEqual(302, reponse.status_code)
        self.assertEqual('/login', self._chemin(reponse))

    def test_cinq_tentatives_max_puis_plus_aucun_code_valide(self):
        self._demander_code()
        for _ in range(CODE_VERIFICATION_TENTATIVES_MAX):
            reponse = self._soumettre_code('000000')
        self.assertIn('Trop de tentatives', self._html(reponse))
        self.assertIsNone(self._utilisateur().code_verification_hash)

        reponse = self._soumettre_code(self.envois[0]['code'])
        self.assertIn('Aucun code valide', self._html(reponse))
        self.assertIn('en attente', self._html(reponse))
        self.assertFalse(check_password_hash(
            self._utilisateur().password_hash, NOUVEAU_MOT_DE_PASSE
        ))

    def test_code_expire_est_refuse(self):
        self._demander_code()
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            user.code_verification_expire = (
                datetime.now(timezone.utc).replace(tzinfo=None)
                - timedelta(minutes=1)
            )
            db.session.commit()

        reponse = self._soumettre_code(self.envois[0]['code'])
        self.assertIn('expiré', self._html(reponse))
        self.assertFalse(check_password_hash(
            self._utilisateur().password_hash, NOUVEAU_MOT_DE_PASSE
        ))
        self.assertIsNone(self._utilisateur().code_verification_hash)

    def test_mot_de_passe_trop_court_ou_different_est_refuse(self):
        self._demander_code()
        code = self.envois[0]['code']

        reponse = self._soumettre_code(code, 'court')
        self.assertIn('8 caractères', self._html(reponse))
        reponse = self._soumettre_code(code, NOUVEAU_MOT_DE_PASSE, 'different123')
        self.assertIn('ne correspondent pas', self._html(reponse))
        self.assertFalse(check_password_hash(
            self._utilisateur().password_hash, NOUVEAU_MOT_DE_PASSE
        ))

    def test_renvoi_attend_une_minute(self):
        self._demander_code()
        premier_code = self.envois[0]['code']

        reponse = self.client.post('/password/reset/renvoyer')
        self.assertEqual(302, reponse.status_code)
        self.assertIn('Veuillez patienter', self._messages())
        self.assertEqual(1, len(self.envois))

        # On recule uniquement l'horodatage de session : patcher time.time()
        # invaliderait la signature du cookie de session (horodatage futur).
        import routes_auth
        with self.client.session_transaction() as donnees_session:
            donnees_session['reset_dernier_envoi'] = (
                routes_auth.time.time() - CODE_VERIFICATION_DELAI_RENVOI - 1
            )
        reponse = self.client.post('/password/reset/renvoyer')
        self.assertEqual(302, reponse.status_code)
        self.assertEqual(2, len(self.envois))
        self.assertNotEqual(premier_code, self.envois[1]['code'])

        reponse = self._soumettre_code(self.envois[1]['code'])
        self.assertEqual('/login', self._chemin(reponse), self._html(reponse))

    def _messages(self):
        """Lit les messages flash en attente sans détruire la session."""
        with self.client.session_transaction() as donnees_session:
            return ' '.join(
                message for _categorie, message
                in donnees_session.get('_flashes', [])
            )


if __name__ == '__main__':
    unittest.main()
