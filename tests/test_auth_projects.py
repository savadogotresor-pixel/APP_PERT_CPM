"""Tests d'intégration auth / projets de l'application PERT/CPM.

Les scénarios passent exclusivement par le client HTTP de Flask : inscription,
connexion, gestion des projets, création de tâches et détection de cycles.
"""

import os
import re
import unittest

from sqlalchemy import create_engine

from models import Project, Task, User


class AuthProjectsTestCase(unittest.TestCase):
    """Parcours utilisateurs complets (base mémoire partagée par la classe)."""

    @classmethod
    def setUpClass(cls):
        # La configuration de test DOIT être sélectionnée avant l'import de app.
        os.environ['APP_SETTINGS'] = 'config.TestConfig'

        try:
            from app import app, db
        except ImportError:  # app.py encore en cours de câblage : db dans extensions
            from app import app
            from extensions import db

        cls.app = app

        # Un autre module de test importe souvent ``app`` avant nous, avec la
        # configuration par défaut. Flask-SQLAlchemy 3.x crée le moteur dès
        # init_app() : on réapplique TestConfig puis on remplace ce moteur par
        # un moteur mémoire (sans rappeler init_app, interdit après un 1er
        # request). L'instance réellement attachée à l'app est la source de
        # vérité pour les modèles et la session.
        db = app.extensions.get('sqlalchemy', db)
        cls.db = db
        app.config.from_object('config.TestConfig')
        app.config.update(TESTING=True)

        with app.app_context():
            db.session.remove()
            moteur = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
            moteurs = db._app_engines.setdefault(app, {})
            ancien = moteurs.get(None)
            if ancien is not None:
                ancien.dispose()
            moteurs[None] = moteur
            db.create_all()

        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            cls.db.drop_all()
        os.environ.pop('APP_SETTINGS', None)

    def setUp(self):
        # Client neuf par test pour ne pas hériter d'une session précédente.
        self.client = self.app.test_client()

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    @staticmethod
    def _html(response):
        return response.get_data(as_text=True)

    def _assert_post_ok(self, response):
        """Les POST réussis sont soit un flash+redirect, soit un rendu direct."""
        self.assertIn(
            response.status_code, (200, 302),
            msg=self._html(response)[:2000],
        )

    def _inscrire(self, username, email, password='Password123', client=None):
        client = client or self.client
        response = client.post('/register', data={
            'username': username,
            'email': email,
            'password': password,
            'password_confirm': password,
        })
        self._assert_post_ok(response)
        return response

    def _connecter(self, identifiant, password='Password123', client=None):
        client = client or self.client
        response = client.post('/login', data={
            'username': identifiant,
            'email': identifiant,
            'password': password,
        })
        self._assert_post_ok(response)
        return response

    def _deconnecter(self, client=None):
        client = client or self.client
        response = client.post('/logout')
        if response.status_code == 405:  # route historique en GET uniquement
            response = client.get('/logout')
        self._assert_post_ok(response)
        return response

    def _nouveau_projet(self, name, client=None, description='Description'):
        client = client or self.client
        response = client.post('/projects/new', data={
            'name': name,
            'description': description,
            'date_reference': '2026-11-21',
        })
        self._assert_post_ok(response)
        with self.app.app_context():
            projet = Project.query.filter_by(name=name).first()
        self.assertIsNotNone(projet, msg=self._html(response)[:2000])
        return projet.id

    @staticmethod
    def _assert_duree(html, nombre):
        """Accepte « 5 jours » ou une carte « Durée totale » affichant 5."""
        if f'{nombre} jours' in html:
            return
        motif = r'Durée totale[\s\S]{0,200}?>\s*' + str(nombre) + r'\s*(?:<|j)'
        assert re.search(motif, html), f'Durée totale {nombre} introuvable dans le HTML'

    @staticmethod
    def _assert_chemin_ab(html):
        variantes = ('A → B', 'A -&gt; B', 'A -> B', 'A &#8594; B')
        assert any(v in html for v in variantes), 'Chemin A → B introuvable dans le HTML'

    # ------------------------------------------------------------------
    # 1. Inscription / connexion / déconnexion
    # ------------------------------------------------------------------
    def test_register_login_logout(self):
        username = 'alice_inscription'
        response = self._inscrire(username, 'alice_inscription@example.com')
        if response.status_code == 302:
            self.assertTrue(response.headers.get('Location'))

        profil = self.client.get('/profile')
        self.assertEqual(200, profil.status_code)
        self.assertIn(username, self._html(profil))

        self._deconnecter()
        apres = self.client.get('/profile')
        self.assertEqual(302, apres.status_code)
        self.assertIn('/login', apres.headers.get('Location', ''))

    # ------------------------------------------------------------------
    # 2. Connexion par e-mail et mauvais mot de passe
    # ------------------------------------------------------------------
    def test_login_par_email_et_mauvais_mdp(self):
        username = 'bob_email'
        email = 'bob_email@example.com'
        self._inscrire(username, email)
        self._deconnecter()

        connexion = self.client.post('/login', data={
            'email': email,
            'password': 'Password123',
        })
        self._assert_post_ok(connexion)
        self.assertEqual(200, self.client.get('/profile').status_code)

        self._deconnecter()
        mauvais = self.client.post('/login', data={
            'email': email,
            'password': 'MotDePasseIncorrect',
        })
        self.assertEqual(200, mauvais.status_code)
        self.assertIn('Identifiants invalides', self._html(mauvais))

    # ------------------------------------------------------------------
    # 3. Unicité du nom d'utilisateur à l'inscription
    # ------------------------------------------------------------------
    def test_unicite_inscription(self):
        username = 'utilisateur_unique'
        self._inscrire(username, 'unique@example.com')

        doublon = self.client.post('/register', data={
            'username': username,
            'email': 'autre_unique@example.com',
            'password': 'Password123',
            'password_confirm': 'Password123',
        })
        self._assert_post_ok(doublon)
        self.assertIn('déjà utilisé', self._html(doublon))

        with self.app.app_context():
            self.assertEqual(1, User.query.filter_by(username=username).count())
            self.assertEqual(0, User.query.filter_by(email='autre_unique@example.com').count())

    # ------------------------------------------------------------------
    # 4. CRUD projets et contrôle d'accès
    # ------------------------------------------------------------------
    def test_projet_crud_et_acces(self):
        self._inscrire('proprietaire_projet', 'proprietaire_projet@example.com')
        proprietaire = self.client
        nom_initial = 'Projet Alpha'
        projet_id = self._nouveau_projet(nom_initial)

        liste = proprietaire.get('/projects/')
        self.assertEqual(200, liste.status_code)
        self.assertIn(nom_initial, self._html(liste))

        detail = proprietaire.get(f'/projects/{projet_id}')
        self.assertEqual(200, detail.status_code)

        # Un second compte, avec son propre client, ne peut pas voir le projet.
        intrus = self.app.test_client()
        self._inscrire('intrus_projet', 'intrus_projet@example.com', client=intrus)
        refuse = intrus.get(f'/projects/{projet_id}')
        self.assertEqual(403, refuse.status_code)

        nom_modifie = 'Projet Alpha modifié'
        modification = proprietaire.post(f'/projects/{projet_id}/edit', data={
            'name': nom_modifie,
            'description': 'Description mise à jour',
            'date_reference': '2026-11-21',
        })
        self._assert_post_ok(modification)
        with self.app.app_context():
            self.assertEqual(nom_modifie, self.db.session.get(Project, projet_id).name)

        archivage = proprietaire.post(f'/projects/{projet_id}/archive')
        self._assert_post_ok(archivage)
        with self.app.app_context():
            self.assertEqual('archive', self.db.session.get(Project, projet_id).status)

        suppression = proprietaire.post(
            f'/projects/{projet_id}/delete', data={'confirm': 'oui'}
        )
        self._assert_post_ok(suppression)
        with self.app.app_context():
            self.assertIsNone(self.db.session.get(Project, projet_id))

        apres = proprietaire.get('/projects/')
        self.assertNotIn(nom_modifie, self._html(apres))

    # ------------------------------------------------------------------
    # 5. Tâches, dépendances, cycles et récalcul du planning
    # ------------------------------------------------------------------
    def test_taches_et_cycle(self):
        self._inscrire('responsable_taches', 'responsable_taches@example.com')
        projet_id = self._nouveau_projet('Projet Planning')

        self._assert_post_ok(self.client.post(
            f'/projects/{projet_id}/tasks/new',
            data={'code': 'A', 'description': 'Analyse', 'duree': '2', 'predecesseurs': ''},
        ))
        self._assert_post_ok(self.client.post(
            f'/projects/{projet_id}/tasks/new',
            data={'code': 'B', 'description': 'Conception', 'duree': '3',
                  'predecesseurs': 'A'},
        ))

        with self.app.app_context():
            tache_a = Task.query.filter_by(project_id=projet_id, code='A').first()
            self.assertIsNotNone(tache_a)
            id_a = tache_a.id

        tableau = self.client.get(f'/projects/{projet_id}')
        self.assertEqual(200, tableau.status_code)
        html = self._html(tableau)
        self._assert_duree(html, 5)
        self._assert_chemin_ab(html)

        # Auto-cycle : C ne peut pas dépendre d'elle-même.
        auto_cycle = self.client.post(
            f'/projects/{projet_id}/tasks/new',
            data={'code': 'C', 'description': 'Autocycle', 'duree': '1',
                  'predecesseurs': 'C'},
        )
        self._assert_post_ok(auto_cycle)
        self.assertIsNotNone(auto_cycle)
        self.assertTrue(
            any(m in self._html(auto_cycle).lower()
                for m in ('cycle', 'circulaire', 'dépend', 'elle-même', 'soi-même')),
            msg=self._html(auto_cycle)[:2000],
        )

        # Cycle A ← B (via une modification de A) doit aussi être refusé.
        cycle_ab = self.client.post(
            f'/projects/{projet_id}/tasks/{id_a}/edit',
            data={'code': 'A', 'description': 'Analyse', 'duree': '2',
                  'predecesseurs': 'B'},
        )
        self._assert_post_ok(cycle_ab)
        self.assertTrue(
            any(m in self._html(cycle_ab).lower() for m in ('cycle', 'circulaire')),
            msg=self._html(cycle_ab)[:2000],
        )

        with self.app.app_context():
            codes = sorted(t.code for t in Task.query.filter_by(project_id=projet_id))
            self.assertEqual(['A', 'B'], codes, 'Aucune tâche de cycle ne doit être créée')

        # La durée de A passe à 4 : le chemin critique total devient 7 jours.
        self._assert_post_ok(self.client.post(
            f'/projects/{projet_id}/tasks/{id_a}/edit',
            data={'code': 'A', 'description': 'Analyse', 'duree': '4',
                  'predecesseurs': ''},
        ))
        html = self._html(self.client.get(f'/projects/{projet_id}'))
        self._assert_duree(html, 7)
        self._assert_chemin_ab(html)

    # ------------------------------------------------------------------
    # 6. Non-régression des routes historiques publiques
    # ------------------------------------------------------------------
    def test_routes_historiques_intactes(self):
        demo = self.client.get('/demo')
        self.assertEqual(200, demo.status_code)
        self.assertIn('30 jours', self._html(demo))

        sante = self.client.get('/health')
        self.assertEqual(200, sante.status_code)

        resultats = self.client.post('/resultats_custom', data={
            'taches': 'A;Analyse;2;\nB;Suite;3;A',
            'date_debut': '2026-11-21',
        })
        self.assertEqual(200, resultats.status_code)


if __name__ == '__main__':
    unittest.main()
