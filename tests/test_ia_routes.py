"""Tests HTTP de l'assistant IA (blueprint ``/ia``, lot IA-1).

Les scénarios passent par le client Flask : statut sans secret, contrôle
d'accès, rendu de la page, contrat de réponse du service, historique de session,
limitation d'usage et journal d'audit.
"""

import os
import re
import sys
import unittest
import uuid
from contextlib import ExitStack
from unittest.mock import patch

from sqlalchemy import create_engine

from models import AuditLog, Project, User

CLES_CONTRAT = {
    'texte', 'source', 'donnees_utilisees', 'donnees_manquantes',
    'avertissements', 'horodatage', 'appel_ia', 'tokens',
}

QUESTION_DUREE = 'Quelle est la durée totale du projet ?'


class AssistantIARoutesTestCase(unittest.TestCase):
    """Parcours HTTP de l'assistant IA sur une base SQLite mémoire."""

    @classmethod
    def setUpClass(cls):
        # La configuration de test DOIT être choisie avant l'import de app.
        os.environ['APP_SETTINGS'] = 'config.TestConfig'

        from app import app, db

        cls.app = app
        # Flask-SQLAlchemy 3.x attache son moteur à l'app : on reprend
        # l'instance réellement utilisée par les modèles (cf. test_auth_projects).
        db = app.extensions.get('sqlalchemy', db)
        cls.db = db
        app.config.from_object('config.TestConfig')
        app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY='test-ia',
        )

        with app.app_context():
            db.session.remove()
            moteur = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
            moteurs = db._app_engines.setdefault(app, {})
            ancien = moteurs.get(None)
            if ancien is not None:
                ancien.dispose()
            moteurs[None] = moteur
            db.drop_all()
            db.create_all()

        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._vider_compteurs()
        with cls.app.app_context():
            cls.db.session.remove()
            cls.db.drop_all()
        os.environ.pop('APP_SETTINGS', None)

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    @staticmethod
    def _html(reponse):
        return reponse.get_data(as_text=True)

    @staticmethod
    def _module(nom):
        module = sys.modules.get(nom)
        if module is not None:
            return module
        try:
            return __import__(nom, fromlist=['*'])
        except Exception:
            return None

    @classmethod
    def _vider_compteurs(cls):
        """Remet à zéro les compteurs de limitation des modules IA."""
        for nom_module in ('routes_ia', 'services.ia'):
            module = cls._module(nom_module)
            if module is None:
                continue
            for nom in dir(module):
                if re.search(r'(APPEL|QUOTA|RATE|USAGE|COMPTEUR|TOKEN)', nom, re.I):
                    valeur = getattr(module, nom)
                    if hasattr(valeur, 'clear') and callable(valeur.clear):
                        valeur.clear()
                    elif isinstance(valeur, int) and not isinstance(valeur, bool):
                        try:
                            setattr(module, nom, 0)
                        except Exception:
                            pass

    def setUp(self):
        self.client = self.app.test_client()
        self._vider_compteurs()

    @staticmethod
    def _patcher(pile, max_appels=None, disponible=None):
        """Patch routes_ia et services.ia, quel que soit le sens de l'import."""
        # Configuration complète : le service lit ``active``/``api_key`` et
        # les routes lisent ``max_appels_par_heure``/``modele``.
        config = {
            'api_key': '',
            'api_url': 'https://exemple.invalid/v1/chat/completions',
            'url': 'https://exemple.invalid/v1/chat/completions',
            'model': 'modele-test',
            'modele': 'modele-test',
            'timeout': 1,
            'enabled': True,
            'active': bool(disponible),
        }
        if max_appels is not None:
            config['max_appels_par_heure'] = max_appels

        for nom in ('routes_ia', 'services.ia'):
            module = AssistantIARoutesTestCase._module(nom)
            if module is None:
                continue
            if max_appels is not None and hasattr(module, 'configuration'):
                pile.enter_context(patch.object(
                    module, 'configuration',
                    side_effect=lambda *a, **k: dict(config),
                ))
            if max_appels is not None:
                for attribut in dir(module):
                    if (
                        re.search(r'(APPEL|QUOTA|RATE)', attribut, re.I)
                        and 'HISTOR' not in attribut.upper()
                        and isinstance(getattr(module, attribut), int)
                    ):
                        pile.enter_context(
                            patch.object(module, attribut, max_appels)
                        )
            if disponible is not None and hasattr(module, 'est_disponible'):
                pile.enter_context(patch.object(
                    module, 'est_disponible', return_value=disponible,
                ))

    def _inscrire(self, username, email, client=None):
        client = client or self.client
        reponse = client.post('/register', data={
            'username': username,
            'email': email,
            'password': 'Password123',
            'password_confirm': 'Password123',
        })
        self.assertIn(reponse.status_code, (200, 302), msg=self._html(reponse)[:1000])
        return username

    def _nouvel_utilisateur(self, prefixe='ia_user', client=None):
        suffixe = uuid.uuid4().hex[:10]
        username = f'{prefixe}_{suffixe}'
        self._inscrire(username, f'{username}@example.com', client=client)
        return username

    def _nouveau_projet(self, nom=None, client=None):
        client = client or self.client
        nom = nom or f'Projet IA {uuid.uuid4().hex[:8]}'
        reponse = client.post('/projects/new', data={
            'name': nom,
            'description': 'Projet de test assistant IA',
            'date_reference': '2026-11-21',
        })
        self.assertIn(reponse.status_code, (200, 302), msg=self._html(reponse)[:1000])
        with self.app.app_context():
            projet = Project.query.filter_by(name=nom).first()
        self.assertIsNotNone(projet, msg=self._html(reponse)[:1000])
        return projet.id

    def _nouvelle_tache(self, projet_id, code, duree, predecesseurs='', client=None):
        client = client or self.client
        reponse = client.post(f'/projects/{projet_id}/tasks/new', data={
            'code': code,
            'description': f'Tâche {code}',
            'duree': str(duree),
            'predecesseurs': predecesseurs,
        })
        self.assertIn(reponse.status_code, (200, 302), msg=self._html(reponse)[:1000])

    def _url_assistant(self, projet_id):
        return f'/ia/projets/{projet_id}/assistant'

    def _question(self, projet_id, question, client=None, json=True):
        client = client or self.client
        if json:
            return client.post(self._url_assistant(projet_id), json={'question': question})
        return client.post(self._url_assistant(projet_id), data={'question': question})

    def _historique(self, projet_id, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            historique = session.get('ia_historique') or {}
            for cle in (str(projet_id), projet_id):
                if cle in historique:
                    return list(historique[cle] or [])
        return []

    def _details_audits_ia(self):
        with self.app.app_context():
            return [
                journal.details or ''
                for journal in AuditLog.query.filter_by(action='IA_ASSISTANT').all()
            ]

    # ------------------------------------------------------------------
    # 1. Statut : contrat et absence de fuite du secret
    # ------------------------------------------------------------------
    def test_statut_sans_fuite_de_secret(self):
        secret_original = os.environ.get('IA_API_KEY')
        os.environ['IA_API_KEY'] = 'secret-xyz'
        try:
            reponse = self.client.get('/ia/statut')
            self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1000])
            donnees = reponse.get_json(silent=True)
            self.assertIsInstance(donnees, dict)
            self.assertIn('disponible', donnees)
            self.assertIn('limite_appels_heure', donnees)
            self.assertNotIn(
                'secret-xyz', self._html(reponse),
                "La réponse /ia/statut ne doit jamais contenir la clé API.",
            )
        finally:
            if secret_original is None:
                os.environ.pop('IA_API_KEY', None)
            else:
                os.environ['IA_API_KEY'] = secret_original

    # ------------------------------------------------------------------
    # 2. Accès anonyme et accès refusé
    # ------------------------------------------------------------------
    def test_acces_anonyme_et_non_autorise(self):
        self._nouvel_utilisateur('ia_proprietaire')
        projet_id = self._nouveau_projet()

        anonyme = self.app.test_client()
        reponse = anonyme.get(self._url_assistant(projet_id))
        self.assertEqual(302, reponse.status_code)
        self.assertIn('/login', reponse.headers.get('Location', ''))

        intrus = self.app.test_client()
        self._nouvel_utilisateur('ia_intrus', client=intrus)

        reponse = intrus.get(self._url_assistant(projet_id))
        self.assertEqual(403, reponse.status_code)

        avant = len(self._details_audits_ia())
        reponse = self._question(projet_id, QUESTION_DUREE, client=intrus)
        self.assertEqual(403, reponse.status_code)
        apres = len(self._details_audits_ia())
        self.assertEqual(
            avant, apres,
            "Un refus d'accès ne doit pas créer d'audit IA_ASSISTANT.",
        )

    # ------------------------------------------------------------------
    # 3. Page HTML de l'assistant
    # ------------------------------------------------------------------
    def test_page_assistant_contenu(self):
        self._nouvel_utilisateur('ia_page')
        projet_id = self._nouveau_projet()

        reponse = self.client.get(self._url_assistant(projet_id))
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1000])
        html = self._html(reponse)
        minuscules = html.lower()

        self.assertIn('assistant ia', minuscules)
        self.assertIn('données transmises', minuscules)
        self.assertTrue(
            'mode sans ia' in minuscules or 'mode' in minuscules,
            "La page doit documenter le mode de fonctionnement.",
        )
        self.assertTrue(
            re.search(r'<textarea[^>]*name=["\']question["\']', html, re.I),
            "Un textarea name=\"question\" est attendu.",
        )
        self.assertTrue(
            re.search(r'name=["\']csrf_token["\']', html, re.I),
            "Le champ CSRF doit être présent dans le formulaire.",
        )
        self.assertTrue(
            any(m in minuscules for m in (
                'suggestion', 'exemple', 'question suggérée',
                'durée totale', 'chemin critique',
            )),
            "Des suggestions de questions sont attendues.",
        )
        self.assertIn(f'/projects/{projet_id}', html)

    # ------------------------------------------------------------------
    # 4. Question sur la durée : contrat de réponse et chiffre du moteur
    # ------------------------------------------------------------------
    def test_question_duree_reponse_verifiee(self):
        self._nouvel_utilisateur('ia_duree')
        projet_id = self._nouveau_projet()
        self._nouvelle_tache(projet_id, 'A', 2)
        self._nouvelle_tache(projet_id, 'B', 3, predecesseurs='A')

        # On force le mode moteur pour que le chiffre vérifié soit déterministe.
        with ExitStack() as pile:
            self._patcher(pile, max_appels=100, disponible=False)
            secret_original = os.environ.pop('IA_API_KEY', None)
            try:
                reponse = self._question(projet_id, QUESTION_DUREE)
            finally:
                if secret_original is not None:
                    os.environ['IA_API_KEY'] = secret_original

        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1000])
        donnees = reponse.get_json(silent=True)
        self.assertIsInstance(donnees, dict)
        self.assertTrue(
            CLES_CONTRAT.issubset(set(donnees)),
            msg=f"Clés manquantes : {sorted(CLES_CONTRAT - set(donnees))}",
        )
        self.assertTrue(str(donnees['texte']).strip(), "Le texte ne doit pas être vide.")
        self.assertIn(donnees['source'], ('moteur', 'ia'))
        self.assertEqual('moteur', donnees['source'])
        self.assertTrue(donnees['horodatage'])
        self.assertIsInstance(donnees['donnees_utilisees'], list)
        self.assertTrue(
            re.search(r'5\s*jours?', str(donnees['texte']), re.I),
            msg=f"La durée totale (5 jours) est absente de : {donnees['texte']!r}",
        )

    # ------------------------------------------------------------------
    # 5. Question vide, historique et plafond de 20 messages
    # ------------------------------------------------------------------
    def test_question_vide_et_historique(self):
        self._nouvel_utilisateur('ia_historique')
        projet_id = self._nouveau_projet()

        with ExitStack() as pile:
            self._patcher(pile, max_appels=100000)

            vide = self._question(projet_id, '   ')
            self.assertIn(vide.status_code, (200, 302, 400), msg=self._html(vide)[:1000])
            if vide.status_code != 400:
                with self.client.session_transaction() as session:
                    flashes = str(session.get('_flashes', ''))
                contenu = (self._html(vide) + flashes).lower()
                self.assertTrue(
                    any(m in contenu for m in ('question', 'vide', 'obligatoire')),
                    "Une question vide doit être signalée (400 ou flash).",
                )

            reponse = self._question(projet_id, QUESTION_DUREE)
            self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1000])
            self.assertGreaterEqual(len(self._historique(projet_id)), 2)

            for rang in range(25):
                reponse = self._question(projet_id, f'Question de test {rang} ?')
                self.assertEqual(
                    200, reponse.status_code,
                    msg=f"Appel {rang} : {self._html(reponse)[:1000]}",
                )

            historique = self._historique(projet_id)
            self.assertLessEqual(
                len(historique), 20,
                "L'historique de session doit être plafonné à 20 messages.",
            )
            self.assertGreater(len(historique), 0)

    # ------------------------------------------------------------------
    # 6. Limitation d'usage et journalisation sans contenu sensible
    # ------------------------------------------------------------------
    def test_limitation_et_audit(self):
        self._nouvel_utilisateur('ia_limite')
        projet_id = self._nouveau_projet()

        question = 'Quel est le chemin critique avec un code bien précis ?'
        with ExitStack() as pile:
            self._patcher(pile, max_appels=2)
            self._vider_compteurs()
            avant = len(self._details_audits_ia())

            statuts = []
            for _ in range(3):
                reponse = self._question(projet_id, question)
                statuts.append(reponse.status_code)
                if reponse.status_code == 429:
                    message = self._html(reponse).lower()
                    self.assertTrue(
                        any(m in message for m in (
                            'limite', 'quota', 'trop', 'patientez', 'attendez',
                        )),
                        msg=f"Message 429 peu clair : {message[:500]}",
                    )

            self.assertEqual([200, 200, 429], statuts)

            journaux = self._details_audits_ia()
            self.assertGreater(len(journaux), avant, "Un audit IA_ASSISTANT est attendu.")
            details = '\n'.join(journaux[avant:])
            self.assertNotIn(question, details)
            for morceau in ('chemin critique', 'bien précis'):
                self.assertNotIn(morceau, details)

    # ------------------------------------------------------------------
    # 7. Aucune donnée de projet ne fuit vers un utilisateur non autorisé
    # ------------------------------------------------------------------
    def test_fiche_projet_non_autorise_aucune_donnee(self):
        self._nouvel_utilisateur('ia_secret_proprio')
        projet_id = self._nouveau_projet()
        code_secret = 'TACHE_SECRETE_XYZ'
        self._nouvelle_tache(projet_id, code_secret, 4)

        intrus = self.app.test_client()
        self._nouvel_utilisateur('ia_secret_intrus', client=intrus)

        get_refuse = intrus.get(self._url_assistant(projet_id))
        self.assertEqual(403, get_refuse.status_code)
        self.assertNotIn(code_secret, self._html(get_refuse))

        post_refuse = self._question(
            projet_id, 'Parle-moi du planning', client=intrus,
        )
        self.assertEqual(403, post_refuse.status_code)
        self.assertNotIn(code_secret, self._html(post_refuse))


if __name__ == '__main__':
    unittest.main()
