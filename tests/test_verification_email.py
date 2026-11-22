"""Tests de la vérification des nouveaux comptes par code envoyé par e-mail.

Le service SMTP est remplacé par un bouchon : aucun e-mail réel n'est envoyé.
Chaque test dispose de sa propre application Flask et de sa propre base mémoire.
"""

import hashlib
import hmac
import os
import re
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit

from flask import Flask
from sqlalchemy import inspect
from werkzeug.security import generate_password_hash

from config import Config, ProductionConfig, TestConfig
from extensions import csrf, db, login_manager
from models import User, init_db
from routes_auth import auth_bp
from services import mail as service_mail


MOT_DE_PASSE = 'Password123'
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _maintenant_naif():
    """UTC naïf, même convention que les colonnes DateTime de l'application."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _charger_utilisateur(user_id):
    """Chargeur Flask-Login partagé, sans dépendance à l'instance globale."""
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


class VerificationEmailTestCase(unittest.TestCase):
    """Scénarios HTTP avec vérification activée et SMTP factice."""

    def setUp(self):
        self.app = Flask(
            'test_verification_email',
            template_folder=os.path.join(RACINE, 'templates'),
            static_folder=os.path.join(RACINE, 'static'),
        )
        self.app.config.from_object(TestConfig)
        self.app.config.update(
            SECRET_KEY='secret-verification-test',
            EMAIL_VERIFICATION_ENABLED=True,
            WTF_CSRF_ENABLED=False,
            MAIL_SERVER='',
            MAIL_DEFAULT_SENDER='PERT/CPM <no-reply@example.test>',
        )

        @self.app.route('/')
        def accueil():
            return 'ACCUEIL-PERT-CPM'

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
        self.smtp_mock = self.smtp_configure.start()
        self.addCleanup(self.smtp_configure.stop)
        self.envoyer = patch.object(
            service_mail, 'envoyer_code', side_effect=self._faux_envoi
        )
        self.envoyer.start()
        self.addCleanup(self.envoyer.stop)
        self.addCleanup(self._nettoyer_base)

        self.username, self.email = self._identifiants()

    def _nettoyer_base(self):
        with self.app.app_context():
            db.session.remove()
            try:
                db.drop_all()
                db.engine.dispose()
            except Exception:
                pass

    @staticmethod
    def _identifiants():
        suffixe = uuid.uuid4().hex[:12]
        return f'verif_{suffixe}', f'verif_{suffixe}@example.test'

    def _faux_envoi(self, email, code, username):
        self.envois.append({
            'email': email, 'code': code, 'username': username
        })
        return True

    def _donnees_inscription(self, remember=False):
        return {
            'username': self.username,
            'email': self.email,
            'password': MOT_DE_PASSE,
            'password_confirm': MOT_DE_PASSE,
            'remember': '1' if remember else '',
        }

    def _inscrire(self, client=None, remember=False):
        client = client or self.client
        return client.post('/register', data=self._donnees_inscription(remember))

    def _utilisateur(self):
        with self.app.app_context():
            return User.query.filter_by(email=self.email).first()

    @staticmethod
    def _chemin(response):
        return urlsplit(response.headers.get('Location', '')).path

    @staticmethod
    def _html(response):
        return response.get_data(as_text=True)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def test_configuration_activee_uniquement_si_demande(self):
        self.assertFalse(TestConfig.EMAIL_VERIFICATION_ENABLED)
        self.assertIsInstance(ProductionConfig.EMAIL_VERIFICATION_ENABLED, bool)
        self.assertIn('MAIL_SERVER', Config.__dict__)
        self.assertEqual(587, self.app.config['MAIL_PORT'])
        self.assertTrue(self.app.config['EMAIL_VERIFICATION_ENABLED'])

    # ------------------------------------------------------------------
    # Migration des comptes existants
    # ------------------------------------------------------------------
    def test_migration_idempotente_compte_existant_connectable(self):
        with self.app.app_context():
            # setUp a déjà créé le schéma actuel : on repart d'une table
            # « historique » sans les colonnes de vérification.
            db.session.remove()
            db.drop_all()
            with db.engine.begin() as connexion:
                connexion.execute(db.text(
                    'CREATE TABLE "user" ('
                    'id INTEGER NOT NULL PRIMARY KEY, '
                    'username VARCHAR(80) NOT NULL UNIQUE, '
                    'email VARCHAR(120) NOT NULL UNIQUE, '
                    'password_hash VARCHAR(255) NOT NULL, '
                    'created_at DATETIME, '
                    'is_admin BOOLEAN NOT NULL DEFAULT 0)'
                ))
                connexion.execute(
                    db.text(
                        'INSERT INTO "user" '
                        '(username, email, password_hash, is_admin) '
                        'VALUES (:username, :email, :hash, 0)'
                    ),
                    {
                        'username': 'ancien_compte',
                        'email': 'ancien@example.test',
                        'hash': generate_password_hash(MOT_DE_PASSE),
                    },
                )

            init_db(self.app)
            init_db(self.app)  # idempotence

            colonnes = {
                    colonne['name']
                    for colonne in inspect(db.engine).get_columns('user')
            }
            for nom in (
                'email_verifie', 'code_verification_hash',
                'code_verification_expire', 'tentatives_code',
            ):
                self.assertIn(nom, colonnes)
            ancien = User.query.filter_by(username='ancien_compte').first()
            self.assertIsNotNone(ancien)
            self.assertTrue(ancien.email_verifie)
            identifiant = ancien.id

        reponse = self.client.post('/login', data={
            'username': 'ancien_compte',
            'password': MOT_DE_PASSE,
        })
        self.assertEqual(302, reponse.status_code)
        self.assertEqual('/', self._chemin(reponse))
        with self.client.session_transaction() as session:
            self.assertEqual(str(identifiant), session.get('_user_id'))

    # ------------------------------------------------------------------
    # Inscription
    # ------------------------------------------------------------------
    def test_inscription_envoie_code_sans_connecter(self):
        reponse = self._inscrire(remember=True)
        self.assertEqual(302, reponse.status_code)
        self.assertEqual('/verify', self._chemin(reponse))
        self.assertEqual(1, len(self.envois))
        self.assertEqual(self.email, self.envois[0]['email'])

        code = self.envois[0]['code']
        self.assertRegex(code, r'^\d{6}$')
        utilisateur = self._utilisateur()
        self.assertIsNotNone(utilisateur)
        self.assertFalse(utilisateur.email_verifie)
        self.assertEqual(0, utilisateur.tentatives_code)
        self.assertNotEqual(code, utilisateur.code_verification_hash)
        self.assertNotEqual(
            hashlib.sha256(code.encode('utf-8')).hexdigest(),
            utilisateur.code_verification_hash,
        )
        attendu = hmac.new(
            self.app.config['SECRET_KEY'].encode('utf-8'),
            f"{utilisateur.id}:{code}".encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(attendu, utilisateur.code_verification_hash)

        ecart = utilisateur.code_verification_expire - _maintenant_naif()
        self.assertAlmostEqual(15 * 60, ecart.total_seconds(), delta=10)

        with self.client.session_transaction() as session:
            self.assertEqual(self.email, session['verify_email'])
            self.assertTrue(session['verify_remember'])
            self.assertNotIn('_user_id', session)
            self.assertNotIn(code, str(dict(session)))
        self.assertEqual(200, self.client.get('/').status_code)

    def test_code_en_clair_jamais_dans_la_page_sous_smtp(self):
        self._inscrire()
        code = self.envois[0]['code']
        page = self.client.get('/verify')
        self.assertNotIn(code, self._html(page))
        self.assertIn('Code de vérification', self._html(page))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def test_validation_reussie_connecte_et_redirige_accueil(self):
        self._inscrire(remember=True)
        code = self.envois[0]['code']
        reponse = self.client.post('/verify', data={'code': code})

        self.assertEqual(302, reponse.status_code)
        self.assertEqual('/', self._chemin(reponse))
        self.assertIsNotNone(
            reponse.headers.get('Set-Cookie'),
            msg='Le cookie remember doit être positionné après validation',
        )
        self.assertTrue(
            any('remember_token=' in valeur
                for valeur in reponse.headers.getlist('Set-Cookie'))
        )
        utilisateur = self._utilisateur()
        self.assertTrue(utilisateur.email_verifie)
        self.assertIsNone(utilisateur.code_verification_hash)
        self.assertIsNone(utilisateur.code_verification_expire)
        self.assertEqual(0, utilisateur.tentatives_code)
        with self.client.session_transaction() as session:
            self.assertEqual(str(utilisateur.id), session.get('_user_id'))
            self.assertNotIn('verify_email', session)
        self.assertEqual(200, self.client.get('/').status_code)

    def test_mauvais_code_incremente_les_tentatives(self):
        self._inscrire()
        code = self.envois[0]['code']
        mauvais = '000000' if code != '000000' else '111111'
        for tentative in range(1, 4):
            reponse = self.client.post('/verify', data={'code': mauvais})
            self.assertEqual(200, reponse.status_code)
            self.assertEqual(tentative, self._utilisateur().tentatives_code)
            self.assertIn('Code incorrect', self._html(reponse))
        with self.client.session_transaction() as session:
            self.assertNotIn('_user_id', session)

    def test_cinq_tentatives_invalident_le_code(self):
        self._inscrire()
        code = self.envois[0]['code']
        mauvais = '000000' if code != '000000' else '111111'
        for _ in range(5):
            self.client.post('/verify', data={'code': mauvais})
        utilisateur = self._utilisateur()
        self.assertEqual(5, utilisateur.tentatives_code)
        self.assertIsNone(utilisateur.code_verification_hash)

        reponse = self.client.post('/verify', data={'code': code})
        self.assertEqual(200, reponse.status_code)
        with self.client.session_transaction() as session:
            self.assertNotIn('_user_id', session)

    def test_code_expire_est_refuse(self):
        self._inscrire()
        code = self.envois[0]['code']
        with self.app.app_context():
            utilisateur = User.query.filter_by(email=self.email).first()
            utilisateur.code_verification_expire = _maintenant_naif() - timedelta(seconds=1)
            db.session.commit()

        reponse = self.client.post('/verify', data={'code': code})
        self.assertEqual(200, reponse.status_code)
        self.assertIn('expiré', self._html(reponse))
        utilisateur = self._utilisateur()
        self.assertFalse(utilisateur.email_verifie)
        self.assertIsNone(utilisateur.code_verification_hash)
        with self.client.session_transaction() as session:
            self.assertNotIn('_user_id', session)

    # ------------------------------------------------------------------
    # Renvoi
    # ------------------------------------------------------------------
    def test_renvoi_genere_un_nouveau_code_et_respecte_delai(self):
        self._inscrire()
        premier_code = self.envois[0]['code']
        with self.app.app_context():
            premier_hash = User.query.filter_by(
                email=self.email
            ).first().code_verification_hash

        bloque = self.client.post('/verify/renvoyer')
        self.assertEqual(302, bloque.status_code)
        self.assertEqual('/verify', self._chemin(bloque))
        self.assertEqual(1, len(self.envois))
        self.assertIn('patienter', self._html(self.client.get('/verify')))

        with self.client.session_transaction() as session:
            session['verify_dernier_envoi'] = datetime.now(
                timezone.utc
            ).timestamp() - 61
        reponse = self.client.post('/verify/renvoyer')
        self.assertEqual(302, reponse.status_code)
        self.assertEqual('/verify', self._chemin(reponse))
        self.assertEqual(2, len(self.envois))

        nouveau_code = self.envois[-1]['code']
        self.assertNotEqual(premier_code, nouveau_code)
        utilisateur = self._utilisateur()
        self.assertNotEqual(premier_hash, utilisateur.code_verification_hash)
        self.assertEqual(0, utilisateur.tentatives_code)
        validation = self.client.post('/verify', data={'code': nouveau_code})
        self.assertEqual('/', self._chemin(validation))

    def test_renvoi_protege_par_csrf_global(self):
        self._inscrire()
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.addCleanup(
            self.app.config.__setitem__, 'WTF_CSRF_ENABLED', False
        )

        sans_jeton = self.client.post('/verify/renvoyer')
        self.assertEqual(400, sans_jeton.status_code)
        self.assertEqual(1, len(self.envois))

        page = self.client.get('/verify')
        correspondance = re.search(
            r'name="csrf_token" value="([^"]+)"', self._html(page)
        )
        self.assertIsNotNone(correspondance)
        with self.client.session_transaction() as session:
            session['verify_dernier_envoi'] = datetime.now(
                timezone.utc
            ).timestamp() - 61
        avec_jeton = self.client.post('/verify/renvoyer', data={
            'csrf_token': correspondance.group(1),
        })
        self.assertEqual(302, avec_jeton.status_code)
        self.assertEqual(2, len(self.envois))

    # ------------------------------------------------------------------
    # Connexion des comptes non vérifiés
    # ------------------------------------------------------------------
    def test_connexion_bloquee_si_compte_non_verifie(self):
        self.username, self.email = self._identifiants()
        with self.app.app_context():
            utilisateur = User(username=self.username, email=self.email)
            utilisateur.set_password(MOT_DE_PASSE)
            utilisateur.email_verifie = False
            db.session.add(utilisateur)
            db.session.commit()
            identifiant = utilisateur.id

        reponse = self.client.post('/login', data={
            'username': self.username,
            'password': MOT_DE_PASSE,
        })
        self.assertEqual(302, reponse.status_code)
        self.assertEqual('/verify', self._chemin(reponse))
        with self.client.session_transaction() as session:
            self.assertEqual(self.email, session['verify_email'])
            self.assertNotIn('_user_id', session)

        with self.client.session_transaction() as session:
            session['verify_dernier_envoi'] = datetime.now(
                timezone.utc
            ).timestamp() - 61
        self.client.post('/verify/renvoyer')
        code = self.envois[0]['code']
        validation = self.client.post('/verify', data={'code': code})
        self.assertEqual('/', self._chemin(validation))
        with self.client.session_transaction() as session:
            self.assertEqual(str(identifiant), session.get('_user_id'))

    def test_comptes_deja_verifies_se_connectent_directement(self):
        self.username, self.email = self._identifiants()
        with self.app.app_context():
            utilisateur = User(username=self.username, email=self.email)
            utilisateur.set_password(MOT_DE_PASSE)
            utilisateur.email_verifie = True
            db.session.add(utilisateur)
            db.session.commit()

        reponse = self.client.post('/login', data={
            'username': self.email,
            'password': MOT_DE_PASSE,
        })
        self.assertEqual(302, reponse.status_code)
        self.assertEqual('/', self._chemin(reponse))
        self.assertEqual(0, len(self.envois))

    # ------------------------------------------------------------------
    # Mode sans SMTP et navigation
    # ------------------------------------------------------------------
    def test_sans_smtp_code_affiche_une_seule_fois_avec_avertissement(self):
        self.smtp_mock.return_value = False
        reponse = self._inscrire()
        self.assertEqual('/verify', self._chemin(reponse))
        self.assertEqual(0, len(self.envois))

        page = self.client.get('/verify')
        html = self._html(page)
        self.assertIn('SMTP non configuré', html)
        correspondance = re.search(r'votre code est\s*([0-9]{6})', html)
        self.assertIsNotNone(correspondance, msg=html[:2000])
        code = correspondance.group(1)

        # Le code n'apparaît qu'une fois : le flash est consommé au 1er rendu.
        self.assertEqual(1, html.count(code))
        self.assertNotIn(code, self._html(self.client.get('/verify')))
        utilisateur = self._utilisateur()
        self.assertNotEqual(code, utilisateur.code_verification_hash)
        self.assertEqual(
            '/',
            self._chemin(self.client.post('/verify', data={'code': code})),
        )

    def test_service_smtp_sans_configuration_ne_lve_pas(self):
        self.smtp_configure.stop()
        self.envoyer.stop()
        self.app.config['MAIL_SERVER'] = ''
        with self.app.app_context():
            self.assertFalse(service_mail.smtp_configure())
            self.assertFalse(service_mail.envoyer_code(self.email, '123456', 'utilisateur'))
        self.smtp_configure.start()
        self.envoyer.start()

    def test_navigation_anonyme_sans_liens_connectes(self):
        self._inscrire()
        page = self.client.get('/verify')
        html = self._html(page)
        nav = re.search(
            r'<ul\s+class="topbar-nav"[^>]*>(.*?)</ul>', html, re.S
        )
        self.assertIsNotNone(nav)
        for lien in ('Catalogue', 'Démo', 'Saisie des tâches', 'Déconnexion'):
            self.assertNotIn(lien, nav.group(1))
        liens = re.findall(r'<link[^>]+>', html)
        self.assertTrue(liens)
        self.assertIn('design.css', liens[-1])

    # ------------------------------------------------------------------
    # Mode désactivé : non-régression stricte
    # ------------------------------------------------------------------
    def test_mode_desactive_comportement_historique_inchange(self):
        self.app.config['EMAIL_VERIFICATION_ENABLED'] = False
        reponse = self._inscrire()
        self.assertEqual(302, reponse.status_code)
        self.assertEqual('/', self._chemin(reponse))
        self.assertEqual(0, len(self.envois))
        with self.client.session_transaction() as session:
            self.assertIn('_user_id', session)
            self.assertNotIn('verify_email', session)
        self.assertEqual(200, self.client.get('/profile').status_code)

        self.client.get('/logout')
        connexion = self.client.post('/login', data={
            'username': self.email,
            'password': MOT_DE_PASSE,
        })
        self.assertEqual(302, connexion.status_code)
        self.assertEqual('/', self._chemin(connexion))


if __name__ == '__main__':
    unittest.main()
