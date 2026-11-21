"""Tests HTTP de la génération WBS (lot IA-2, blueprint ``ia``).

Ils vérifient le parcours complet : aperçu sans écriture, confirmation
explicite obligatoire, revalidation serveur, création du projet, audit et
présence des liens IA dans la page de détail.
"""

import html as html_module
import os
import unittest
import uuid

from flask import url_for
from sqlalchemy import create_engine

from models import AuditLog, Dependency, Project, Task, User
from services.wbs import generer_wbs


class WBSRoutesTestCase(unittest.TestCase):
    """Parcours WBS sur une base SQLite mémoire (pattern TestConfig)."""

    @classmethod
    def setUpClass(cls):
        os.environ['APP_SETTINGS'] = 'config.TestConfig'

        from app import app, db

        cls.app = app
        # Flask-SQLAlchemy 3.x attache son moteur à l'instance de l'app : on
        # reprend celle réellement utilisée par les modèles.
        db = app.extensions.get('sqlalchemy', db)
        cls.db = db
        app.config.from_object('config.TestConfig')
        app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY='test-wbs-routes',
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

        import routes_ia
        cls.routes_ia = routes_ia
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            cls.db.session.remove()
            cls.db.drop_all()
        os.environ.pop('APP_SETTINGS', None)

    def setUp(self):
        # La limitation d'usage est en mémoire : on la remet à zéro par test.
        with self.routes_ia._compteurs_lock:
            self.routes_ia._appels_par_utilisateur.clear()
        self.client = self.app.test_client()

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    @staticmethod
    def _html(reponse):
        return reponse.get_data(as_text=True)

    @staticmethod
    def _unique(prefixe):
        return f'{prefixe}_{uuid.uuid4().hex[:10]}'

    def _inscrire(self, prefixe, client=None):
        client = client or self.client
        username = self._unique(prefixe)
        reponse = client.post('/register', data={
            'username': username,
            'email': f'{username}@example.com',
            'password': 'Password123',
            'password_confirm': 'Password123',
        })
        self.assertIn(
            reponse.status_code, (200, 302),
            msg=self._html(reponse)[:1000],
        )
        return username

    def _connecter(self, username, client=None):
        client = client or self.client
        reponse = client.post('/login', data={
            'username': username,
            'email': username,
            'password': 'Password123',
        })
        self.assertIn(
            reponse.status_code, (200, 302),
            msg=self._html(reponse)[:1000],
        )

    def _nb_projets(self):
        with self.app.app_context():
            return Project.query.count()

    def _formulaire_wbs(self, objectif, contraintes='', confirmer=False,
                        mutations=None):
        """Construit un POST ``enregistrer`` à partir d'une vraie proposition."""
        wbs = generer_wbs(objectif, contraintes)
        donnees = {
            'etape': 'enregistrer',
            'objectif': objectif,
            'contraintes': contraintes,
            'source': wbs.get('source', 'regles'),
            'hypotheses': wbs.get('hypotheses', ''),
            'limites': wbs.get('limites', ''),
        }
        for position, avertissement in enumerate(wbs.get('avertissements') or []):
            donnees[f'avertissement_{position}'] = avertissement

        index = 0
        for phase_index, phase in enumerate(wbs.get('phases') or []):
            donnees[f'livrables_{phase_index}'] = ' ;; '.join(
                phase.get('livrables') or []
            )
            for tache in phase.get('taches') or []:
                donnees[f'phase_{index}'] = phase.get('nom', '')
                donnees[f'phase_index_{index}'] = str(phase_index)
                donnees[f'code_{index}'] = tache.get('code', '')
                donnees[f'description_{index}'] = tache.get('description', '')
                donnees[f'duree_{index}'] = str(tache.get('duree', 0))
                donnees[f'predecesseurs_{index}'] = ', '.join(
                    tache.get('predecesseurs') or []
                )
                index += 1

        if confirmer:
            donnees['confirmer'] = 'oui'
        if mutations:
            donnees.update(mutations)
        return donnees

    @staticmethod
    def _id_depuis_redirect(reponse):
        return int(reponse.headers['Location'].rstrip('/').split('/')[-1])

    # ------------------------------------------------------------------
    # 1. Accès et formulaire initial
    # ------------------------------------------------------------------
    def test_00_get_anonyme_redirige_vers_login(self):
        reponse = self.client.get('/ia/projets/nouveau')
        self.assertEqual(302, reponse.status_code)
        self.assertIn('/login', reponse.headers.get('Location', ''))

    def test_01_get_connecte_affiche_formulaire_sans_apercu(self):
        username = self._inscrire('wbs_form')
        self._connecter(username)

        reponse = self.client.get('/ia/projets/nouveau')
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1000])
        page = html_module.unescape(self._html(reponse))
        self.assertIn('name="objectif"', page)
        self.assertIn('name="contraintes"', page)
        self.assertIn('Générer la proposition', page)
        self.assertNotIn('name="code_0"', page)

    # ------------------------------------------------------------------
    # 2. Aperçu : aucune écriture
    # ------------------------------------------------------------------
    def test_02_generer_affiche_apercu_sans_ecrire_en_base(self):
        username = self._inscrire('wbs_apercu')
        self._connecter(username)
        avant = self._nb_projets()

        reponse = self.client.post('/ia/projets/nouveau', data={
            'etape': 'generer',
            'objectif': 'application web',
            'contraintes': 'budget limité',
        })
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        page = html_module.unescape(self._html(reponse))
        self.assertIn('name="code_0"', page)
        self.assertIn('name="description_0"', page)
        self.assertIn('name="duree_0"', page)
        self.assertIn('name="predecesseurs_0"', page)
        self.assertIn('Hypothèses', page)
        self.assertIn('Proposition indicative générée par règles', page)
        self.assertIn('confirmation explicite', page)
        self.assertEqual(avant, self._nb_projets())

    # ------------------------------------------------------------------
    # 3. Confirmation explicite
    # ------------------------------------------------------------------
    def test_03_enregistrer_sans_confirmation_ne_cree_rien(self):
        username = self._inscrire('wbs_noconf')
        self._connecter(username)
        avant = self._nb_projets()
        objectif = f'application web sans confirmation {uuid.uuid4().hex[:8]}'

        reponse = self.client.post(
            '/ia/projets/nouveau',
            data=self._formulaire_wbs(objectif, 'délai court', confirmer=False),
        )
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        page = html_module.unescape(self._html(reponse))
        self.assertIn('Confirmation explicite', page)
        self.assertIn('name="code_0"', page)
        self.assertEqual(avant, self._nb_projets())

    def test_04_enregistrer_avec_confirmation_cree_projet_et_audit(self):
        username = self._inscrire('wbs_ok')
        self._connecter(username)
        avant = self._nb_projets()
        objectif = f'application web confirmée {uuid.uuid4().hex[:8]}'

        reponse = self.client.post(
            '/ia/projets/nouveau',
            data=self._formulaire_wbs(objectif, 'livraison rapide', confirmer=True),
        )
        self.assertEqual(302, reponse.status_code, msg=self._html(reponse)[:2000])
        self.assertIn('/projects/', reponse.headers.get('Location', ''))
        self.assertEqual(avant + 1, self._nb_projets())
        project_id = self._id_depuis_redirect(reponse)

        with self.app.app_context():
            utilisateur = User.query.filter_by(username=username).first()
            projet = Project.query.filter_by(name=objectif[:120]).first()
            self.assertIsNotNone(projet)
            self.assertEqual(project_id, projet.id)
            self.assertEqual(utilisateur.id, projet.user_id)

            taches = Task.query.filter_by(project_id=projet.id).all()
            self.assertGreater(len(taches), 0)
            dependances = (
                Dependency.query
                .join(Task, Dependency.successor_id == Task.id)
                .filter(Task.project_id == projet.id)
                .count()
            )
            self.assertGreater(dependances, 0)

            audits = AuditLog.query.filter_by(action='IA_WBS_CREATION').all()
            self.assertTrue(
                any(str(projet.id) in (audit.details or '') for audit in audits),
                msg=[audit.details for audit in audits],
            )

    # ------------------------------------------------------------------
    # 4. Validation serveur et périmètre d'accès
    # ------------------------------------------------------------------
    def test_05_predecesseur_invalide_refuse_sans_ecriture(self):
        username = self._inscrire('wbs_invalide')
        self._connecter(username)
        avant = self._nb_projets()
        objectif = f'application web invalide {uuid.uuid4().hex[:8]}'
        donnees = self._formulaire_wbs(objectif, confirmer=True)
        donnees['predecesseurs_0'] = 'CODE_INEXISTANT'

        reponse = self.client.post('/ia/projets/nouveau', data=donnees)
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        page = html_module.unescape(self._html(reponse))
        self.assertIn('Prédécesseur inexistant', page)
        self.assertEqual(avant, self._nb_projets())

    def test_06_projet_dun_autre_utilisateur_jamais_modifie(self):
        username_a = self._inscrire('wbs_a')
        self._connecter(username_a)

        client_b = self.app.test_client()
        username_b = self._inscrire('wbs_b', client=client_b)
        self._connecter(username_b, client=client_b)
        nom_b = f'Projet B {uuid.uuid4().hex[:8]}'
        reponse_b = client_b.post('/projects/new', data={
            'name': nom_b,
            'description': 'Projet d un autre utilisateur',
            'date_reference': '2026-11-21',
        })
        self.assertIn(reponse_b.status_code, (200, 302))

        with self.app.app_context():
            projet_b = Project.query.filter_by(name=nom_b).first()
            self.assertIsNotNone(projet_b)
            id_b = projet_b.id
            proprietaire_b = projet_b.user_id
            nom_initial_b = projet_b.name
            nb_taches_b = Task.query.filter_by(project_id=id_b).count()

        objectif = f'application web autre acces {uuid.uuid4().hex[:8]}'
        donnees = self._formulaire_wbs(objectif, 'aucune', confirmer=True)
        # Un champ hostile sans rapport est ignoré par la route.
        donnees['project_id'] = str(id_b)
        reponse = self.client.post('/ia/projets/nouveau', data=donnees)
        self.assertEqual(302, reponse.status_code, msg=self._html(reponse)[:2000])
        id_cree = self._id_depuis_redirect(reponse)
        self.assertNotEqual(id_b, id_cree)

        with self.app.app_context():
            projet_b_apres = self.db.session.get(Project, id_b)
            self.assertIsNotNone(projet_b_apres)
            self.assertEqual(proprietaire_b, projet_b_apres.user_id)
            self.assertEqual(nom_initial_b, projet_b_apres.name)
            self.assertEqual(
                nb_taches_b, Task.query.filter_by(project_id=id_b).count()
            )

    # ------------------------------------------------------------------
    # 5. Liens IA dans la page de détail
    # ------------------------------------------------------------------
    def test_07_liens_ia_present_dans_detail_projet(self):
        username = self._inscrire('wbs_liens')
        self._connecter(username)
        nom = f'Projet liens {uuid.uuid4().hex[:8]}'
        reponse = self.client.post('/projects/new', data={
            'name': nom,
            'description': 'Projet pour les liens IA',
            'date_reference': '2026-11-21',
        })
        self.assertIn(reponse.status_code, (200, 302))

        with self.app.app_context():
            projet = Project.query.filter_by(name=nom).first()
            self.assertIsNotNone(projet)
            project_id = projet.id

        reponse = self.client.get(f'/projects/{project_id}')
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        page = html_module.unescape(self._html(reponse))
        self.assertIn('Générer un projet avec l\'IA', page)
        self.assertIn('/ia/projets/nouveau', page)
        self.assertIn('Optimisation assistée (IA)', page)

        with self.app.test_request_context():
            try:
                attendu = url_for(
                    'optimisation_ia.assister', project_id=project_id
                )
            except Exception:
                attendu = None
        if attendu is not None:
            self.assertIn(attendu, page)
        else:
            self.assertIn('/optimisation', page)


if __name__ == '__main__':
    unittest.main()
