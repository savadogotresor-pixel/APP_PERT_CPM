"""Tests du conseiller IA maison (règles déterministes + route protégée).

Aucun réseau n'est sollicité : les règles sont testées directement sur les
résultats du moteur canonique, puis la route est vérifiée avec le client Flask
en reprenant les helpers d'inscription de ``tests/test_auth_projects.py``.
"""

import os
import re
import unittest
import uuid

from sqlalchemy import create_engine

from logic.pert import compute
from models import Dependency, Project, Task, User
from services.conseiller import GRAVITES, generer_conseils

CLES_CONSEIL = {
    'id_regle', 'titre', 'gravite', 'explication', 'preuve', 'action',
}

# Graphes volontairement petits : chaque règle est testée sur un cas où elle
# se déclenche, sans dépendre de la démo historique (durée 30).
GRAPHE_UNIQUE = {
    'A': {'description': 'Analyse', 'duree': 2, 'depend_de': []},
    'B': {'description': 'Conception', 'duree': 3, 'depend_de': ['A']},
    'C': {'description': 'Documentation', 'duree': 1, 'depend_de': ['A']},
    'D': {'description': 'Intégration', 'duree': 2, 'depend_de': ['B', 'C']},
    'E': {'description': 'Jalon final', 'duree': 0, 'depend_de': ['D']},
}

GRAPHE_DENSE = {
    'A': {'description': 'Amorçage', 'duree': 1, 'depend_de': []},
    'B': {'description': 'Étude', 'duree': 1, 'depend_de': ['A']},
    'C': {'description': 'Revue', 'duree': 1, 'depend_de': ['A', 'B']},
    'D': {'description': 'Livraison', 'duree': 1, 'depend_de': ['A', 'B', 'C']},
}

GRAPHE_MARGES = {
    'A': {'description': 'Amorçage', 'duree': 1, 'depend_de': []},
    'B': {'description': 'Marge libre nulle', 'duree': 1, 'depend_de': ['A']},
    'C': {'description': 'Branche critique', 'duree': 1, 'depend_de': ['A']},
    'D': {'description': 'Suite courte', 'duree': 1, 'depend_de': ['B']},
    'E': {'description': 'Suite longue', 'duree': 3, 'depend_de': ['C']},
    'F': {'description': 'Convergence', 'duree': 1, 'depend_de': ['D', 'E']},
}

GRAPHE_SOURCES = {
    'A': {'description': 'Source A', 'duree': 1, 'depend_de': []},
    'B': {'description': 'Source B', 'duree': 1, 'depend_de': []},
    'C': {'description': 'Convergence', 'duree': 1, 'depend_de': ['A', 'B']},
}

GRAPHE_JALONS = {
    'A': {'description': 'Jalon initial', 'duree': 0, 'depend_de': []},
    'B': {'description': 'Jalon final', 'duree': 0, 'depend_de': ['A']},
}


class ConseillerReglesTestCase(unittest.TestCase):
    """Unités pures : contrat, règles, tri et absence de chiffres inventés."""

    @staticmethod
    def _conseils(graphe):
        return generer_conseils(compute(graphe), graphe)

    @staticmethod
    def _par_regle(conseils):
        return {conseil['id_regle']: conseil for conseil in conseils}

    def test_contrat_et_tri_stable(self):
        for graphe in (GRAPHE_UNIQUE, GRAPHE_DENSE, GRAPHE_MARGES,
                       GRAPHE_SOURCES, GRAPHE_JALONS):
            conseils = self._conseils(graphe)
            self.assertTrue(conseils)
            for conseil in conseils:
                self.assertEqual(CLES_CONSEIL, set(conseil))
                self.assertIn(conseil['gravite'], GRAVITES)
                for cle in CLES_CONSEIL:
                    self.assertIsInstance(conseil[cle], str)
                    self.assertTrue(conseil[cle].strip(), msg=f'{cle} vide')

            rangs = [GRAVITES.index(c['gravite']) for c in conseils]
            self.assertEqual(sorted(rangs), rangs, msg='Tri par gravité attendu')
            for gravite in GRAVITES:
                ids = [c['id_regle'] for c in conseils if c['gravite'] == gravite]
                self.assertEqual(sorted(ids), ids, msg='Tri par id_regle attendu')

    def test_regle_chemin_critique_unique(self):
        conseils = self._par_regle(self._conseils(GRAPHE_UNIQUE))
        self.assertIn('R01_CHEMIN_CRITIQUE_UNIQUE', conseils)
        regle = conseils['R01_CHEMIN_CRITIQUE_UNIQUE']
        self.assertEqual('warning', regle['gravite'])
        self.assertIn('durée totale = 7', regle['preuve'])
        self.assertIn('chemins critiques = 1', regle['preuve'])
        self.assertIn('marge totale du chemin = 0', regle['preuve'])
        self.assertIn('A → B → D → E', regle['explication'])

    def test_regles_chemins_multiples_et_sources(self):
        conseils = self._par_regle(self._conseils(GRAPHE_SOURCES))
        self.assertIn('R02_CHEMINS_CRITIQUES_MULTIPLES', conseils)
        self.assertEqual('critical', conseils['R02_CHEMINS_CRITIQUES_MULTIPLES']['gravite'])
        self.assertIn('chemins critiques = 2',
                      conseils['R02_CHEMINS_CRITIQUES_MULTIPLES']['preuve'])

        self.assertIn('R07_SOURCES_MULTIPLES', conseils)
        self.assertEqual('info', conseils['R07_SOURCES_MULTIPLES']['gravite'])
        self.assertIn('tâches sans prédécesseur = 2',
                      conseils['R07_SOURCES_MULTIPLES']['preuve'])

    def test_regle_tache_critique_avec_successeurs(self):
        conseils = self._par_regle(self._conseils(GRAPHE_UNIQUE))
        self.assertIn('R03_TACHE_CRITIQUE_MULTI_SUCCESSEURS', conseils)
        regle = conseils['R03_TACHE_CRITIQUE_MULTI_SUCCESSEURS']
        self.assertEqual('critical', regle['gravite'])
        self.assertIn('successeurs maximum = 2', regle['preuve'])
        self.assertIn('A', regle['explication'])

    def test_regle_forte_densite(self):
        conseils = self._par_regle(self._conseils(GRAPHE_DENSE))
        self.assertIn('R04_FORTE_DENSITE_DEPENDANCES', conseils)
        regle = conseils['R04_FORTE_DENSITE_DEPENDANCES']
        self.assertEqual('warning', regle['gravite'])
        self.assertIn('liens de dépendance = 6', regle['preuve'])
        self.assertIn('tâches = 4', regle['preuve'])

    def test_regle_jalons_en_fin_de_chaine(self):
        conseils = self._par_regle(self._conseils(GRAPHE_UNIQUE))
        self.assertIn('R05_JALONS_FIN_CHAINE', conseils)
        regle = conseils['R05_JALONS_FIN_CHAINE']
        self.assertEqual('info', regle['gravite'])
        self.assertIn('jalons terminaux = 1', regle['preuve'])
        self.assertIn('E', regle['explication'])

    def test_regle_marge_libre_nulle_et_marge_totale_positive(self):
        resultat = compute(GRAPHE_MARGES)
        self.assertEqual(0, resultat.resultats['B']['marge_libre'])
        self.assertEqual(2, resultat.resultats['B']['marge_totale'])

        conseils = self._par_regle(self._conseils(GRAPHE_MARGES))
        self.assertIn('R06_MARGE_LIBRE_NULLE_MT_POSITIVE', conseils)
        regle = conseils['R06_MARGE_LIBRE_NULLE_MT_POSITIVE']
        self.assertEqual('warning', regle['gravite'])
        self.assertIn('marge libre = 0', regle['preuve'])
        self.assertIn('marge totale maximale = 2', regle['preuve'])
        self.assertIn('B', regle['explication'])

    def test_regle_duree_totale_nulle(self):
        conseils = self._par_regle(self._conseils(GRAPHE_JALONS))
        self.assertIn('R08_DUREE_TOTALE_NULLE', conseils)
        self.assertIn('durée totale = 0', conseils['R08_DUREE_TOTALE_NULLE']['preuve'])

    @staticmethod
    def _nombres_calcules(resultat):
        """Tous les entiers que le moteur a réellement produits."""
        valeurs = [
            resultat.duree_totale,
            len(resultat.resultats),
            len(resultat.chemins_critiques),
            sum(len(succs) for succs in resultat.successeurs.values()),
            sum(1 for r in resultat.resultats.values() if r.get('critique')),
        ]
        for chemin in resultat.chemins_critiques:
            valeurs.append(len(chemin))
        for code, r in resultat.resultats.items():
            valeurs.append(len(resultat.successeurs.get(code, [])))
            for cle in ('ES', 'EF', 'LS', 'LF', 'marge', 'marge_totale', 'marge_libre'):
                valeurs.append(r.get(cle, 0))
        return {
            str(int(v)) for v in valeurs
            if isinstance(v, (int, float)) and float(v).is_integer()
        }

    def test_aucune_donnee_inventee_dans_les_preuves(self):
        for graphe in (GRAPHE_UNIQUE, GRAPHE_DENSE, GRAPHE_MARGES,
                       GRAPHE_SOURCES, GRAPHE_JALONS):
            resultat = compute(graphe)
            autorises = self._nombres_calcules(resultat)
            for conseil in self._conseils(graphe):
                nombres = re.findall(r'\d+', conseil['preuve'])
                self.assertTrue(nombres, msg='Une preuve doit contenir des chiffres')
                for nombre in nombres:
                    self.assertIn(
                        nombre, autorises,
                        msg=f'{conseil["id_regle"]} : {nombre!r} ne vient pas du moteur '
                            f'({conseil["preuve"]!r})',
                    )

    def test_preuves_suivent_le_moteur_apres_mise_a_lechelle(self):
        facteur = 7
        graphe_scale = {
            code: dict(infos, duree=infos['duree'] * facteur)
            for code, infos in GRAPHE_UNIQUE.items()
        }
        resultat = compute(graphe_scale)
        self.assertEqual(49, resultat.duree_totale)

        conseils = self._conseils(graphe_scale)
        self.assertTrue(conseils)
        for conseil in conseils:
            self.assertIn('durée totale = 49', conseil['preuve'], msg=conseil['preuve'])
            self.assertNotIn('durée totale = 7', conseil['preuve'], msg=conseil['preuve'])

        autorises = self._nombres_calcules(resultat)
        for conseil in conseils:
            for nombre in re.findall(r'\d+', conseil['preuve']):
                self.assertIn(nombre, autorises)

    def test_aucune_reference_a_un_modele_externe(self):
        textes = ' '.join(
            ' '.join(conseil.values())
            for graphe in (GRAPHE_UNIQUE, GRAPHE_DENSE, GRAPHE_MARGES,
                           GRAPHE_SOURCES, GRAPHE_JALONS)
            for conseil in self._conseils(graphe)
        ).lower()
        for terme in ('openai', 'gpt', 'claude', 'modèle externe', 'service externe',
                      'appel réseau', 'clé api', 'estimation', '≈'):
            self.assertNotIn(terme, textes, msg=f'Terme interdit : {terme}')

    def test_sans_resultat_ou_sans_taches(self):
        self.assertEqual([], generer_conseils(None, GRAPHE_UNIQUE))
        self.assertEqual([], generer_conseils(compute({}), {}))


class ConseillerRoutesTestCase(unittest.TestCase):
    """Tests HTTP : endpoint, propriété stricte et graphe invalide."""

    @classmethod
    def setUpClass(cls):
        # La configuration de test DOIT être sélectionnée avant l'import de app.
        os.environ['APP_SETTINGS'] = 'config.TestConfig'

        from app import app, db
        from routes_conseiller import conseiller_bp

        cls.app = app
        db = app.extensions.get('sqlalchemy', db)
        cls.db = db
        app.config.from_object('config.TestConfig')
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
                          SECRET_KEY='test-conseiller')

        # Le coordonnateur enregistre le blueprint dans app.py ; en attendant,
        # l'enregistrement ici rend le test autonome et reste idempotent.
        if 'conseiller' not in app.blueprints:
            app.register_blueprint(conseiller_bp)

        with app.app_context():
            db.session.remove()
            moteur = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
            moteurs = db._app_engines.setdefault(app, {})
            ancien = moteurs.get(None)
            if ancien is not None:
                ancien.dispose()
            moteurs[None] = moteur
            db.create_all()

        cls._utilisateurs = []
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        # On ne supprime que nos propres comptes : les autres suites de tests
        # partagent parfois la même base et leurs données doivent survivre.
        with cls.app.app_context():
            for user_id in cls._utilisateurs:
                utilisateur = cls.db.session.get(User, user_id)
                if utilisateur is not None:
                    cls.db.session.delete(utilisateur)
            cls.db.session.commit()
            cls.db.session.remove()
        os.environ.pop('APP_SETTINGS', None)

    @staticmethod
    def _html(reponse):
        return reponse.get_data(as_text=True)

    def _inscrire(self, prefixe, client=None):
        client = client or self.client
        username = f'{prefixe}_{uuid.uuid4().hex[:8]}'
        reponse = client.post('/register', data={
            'username': username,
            'email': f'{username}@example.com',
            'password': 'Password123',
            'password_confirm': 'Password123',
        })
        self.assertIn(reponse.status_code, (200, 302), msg=self._html(reponse)[:1000])
        with self.app.app_context():
            utilisateur = User.query.filter_by(username=username).first()
        self.assertIsNotNone(utilisateur)
        type(self)._utilisateurs.append(utilisateur.id)
        return username

    def _nouveau_projet(self, nom=None, client=None):
        client = client or self.client
        nom = nom or f'Projet conseils {uuid.uuid4().hex[:8]}'
        reponse = client.post('/projects/new', data={
            'name': nom,
            'description': 'Projet de test du conseiller',
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

    def test_endpoint_et_url(self):
        with self.app.test_request_context():
            from flask import url_for
            self.assertEqual('/projects/123/conseils',
                             url_for('conseiller.conseils', project_id=123))
        self.assertIn('conseiller', self.app.blueprints)

    def test_route_proprietaire_affiche_les_conseils(self):
        self._inscrire('conseiller_proprietaire')
        projet_id = self._nouveau_projet('Projet conseils propriétaire')
        self._nouvelle_tache(projet_id, 'A', 2)
        self._nouvelle_tache(projet_id, 'B', 3, 'A')

        reponse = self.client.get(f'/projects/{projet_id}/conseils')
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1500])
        html = self._html(reponse)
        self.assertIn('Conseiller IA maison', html)
        self.assertIn('stat-grid', html)
        self.assertIn('card-grid', html)
        self.assertIn('badge-warning', html)
        self.assertIn('R01_CHEMIN_CRITIQUE_UNIQUE', html)
        self.assertIn('Preuve (chiffres du moteur)', html)
        self.assertIn('durée totale = 5', html)
        self.assertIn('design.css', html)
        # design.css doit rester la dernière feuille de style chargée.
        self.assertLess(html.index('style.css'), html.index('design.css'))

    def test_autre_utilisateur_et_admin_recoivent_404(self):
        self._inscrire('conseiller_secret_proprio')
        projet_id = self._nouveau_projet('Projet conseils privé')
        code_secret = 'TACHE_CONFIDENTIELLE_XYZ'
        self._nouvelle_tache(projet_id, code_secret, 4)

        intrus = self.app.test_client()
        username = self._inscrire('conseiller_intrus', client=intrus)
        refuse = intrus.get(f'/projects/{projet_id}/conseils')
        self.assertEqual(404, refuse.status_code)
        self.assertNotIn(code_secret, self._html(refuse))

        # Même un administrateur n'est pas propriétaire : contrôle strict.
        with self.app.app_context():
            utilisateur = User.query.filter_by(username=username).first()
            utilisateur.is_admin = True
            self.db.session.commit()

        admin = intrus.get(f'/projects/{projet_id}/conseils')
        self.assertEqual(404, admin.status_code)
        self.assertNotIn(code_secret, self._html(admin))

    def test_anonyme_redirige_vers_login(self):
        self._inscrire('conseiller_anonyme')
        projet_id = self._nouveau_projet('Projet conseils anonyme')
        anonyme = self.app.test_client()
        reponse = anonyme.get(f'/projects/{projet_id}/conseils')
        self.assertEqual(302, reponse.status_code)
        self.assertIn('/login', reponse.headers.get('Location', ''))

    def test_projet_sans_tache_est_affiche_sans_conseil(self):
        self._inscrire('conseiller_vide')
        projet_id = self._nouveau_projet('Projet conseils vide')
        reponse = self.client.get(f'/projects/{projet_id}/conseils')
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1500])
        html = self._html(reponse)
        self.assertIn('Aucune tâche', html)
        self.assertIn('Conseils tracés (0)', html)

    def test_graphe_invalide_est_affiche_proprement(self):
        self._inscrire('conseiller_cycle')
        projet_id = self._nouveau_projet('Projet conseils cycle')
        self._nouvelle_tache(projet_id, 'A', 2)
        self._nouvelle_tache(projet_id, 'B', 3, 'A')

        with self.app.app_context():
            tache_a = Task.query.filter_by(project_id=projet_id, code='A').first()
            tache_b = Task.query.filter_by(project_id=projet_id, code='B').first()
            self.db.session.add(
                Dependency(successor_id=tache_a.id, predecessor_id=tache_b.id)
            )
            self.db.session.commit()

        reponse = self.client.get(f'/projects/{projet_id}/conseils')
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1500])
        html = self._html(reponse)
        self.assertIn('Planning non calculable', html)
        self.assertIn('circulaire', html.lower())
        self.assertIn('Conseils tracés (0)', html)


if __name__ == '__main__':
    unittest.main()
