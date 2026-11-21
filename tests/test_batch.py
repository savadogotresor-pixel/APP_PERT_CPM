"""Tests du traitement par lots, de la pagination et des actions groupées.

Chaque test dispose d'une application Flask et d'une base mémoire dédiées :
aucune dépendance envers l'instance globale de ``app.py`` ni envers l'ordre
d'exécution de la suite.
"""

import os
import unittest

from flask import Flask
from sqlalchemy import event

from config import TestConfig
from extensions import csrf, db, login_manager
from models import Dependency, Project, Task, User
from routes_projects import projects_bp
from services.batch import TAILLE_LOT, exporter_projets, recalculer_projets, supprimer_projets

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class BatchTestCase(unittest.TestCase):
    """Jeu de données : 205 projets pour Alice, 3 pour Bob."""

    def setUp(self):
        self.app = Flask(
            __name__,
            template_folder=os.path.join(RACINE, 'templates'),
            static_folder=os.path.join(RACINE, 'static'),
        )
        self.app.config.from_object(TestConfig)
        db.init_app(self.app)
        login_manager.init_app(self.app)
        self._login_view_initial = login_manager.login_view
        login_manager.login_view = 'accueil'
        csrf.init_app(self.app)

        @self.app.route('/')
        def accueil():
            return 'accueil'

        @login_manager.user_loader
        def charger_utilisateur(identifiant):
            return db.session.get(User, int(identifiant))

        self.app.register_blueprint(projects_bp)

        self.contexte = self.app.app_context()
        self.contexte.push()
        db.create_all()

        self.alice = self._utilisateur('alice_batch', 'alice_batch@example.com')
        self.bob = self._utilisateur('bob_batch', 'bob_batch@example.com')
        self.ids_alice = [
            self._projet(self.alice, f'Projet Alpha {i:03d}', i < 2).id
            for i in range(205)
        ]
        self.ids_bob = [
            self._projet(self.bob, f'Projet Beta {i:03d}', i == 0).id
            for i in range(3)
        ]
        db.session.commit()

    def tearDown(self):
        login_manager.login_view = self._login_view_initial
        db.session.remove()
        db.drop_all()
        self.contexte.pop()

    def _utilisateur(self, username, email):
        utilisateur = User(username=username, email=email)
        utilisateur.set_password('secret')
        db.session.add(utilisateur)
        db.session.commit()
        return utilisateur

    def _projet(self, utilisateur, nom, avec_taches=False):
        projet = Project(
            user_id=utilisateur.id,
            name=nom,
            description=f'Description de {nom}',
            status='actif',
        )
        db.session.add(projet)
        db.session.flush()
        if avec_taches:
            tache_a = Task(
                project_id=projet.id, code='A', description='Analyse', duree=2
            )
            tache_b = Task(
                project_id=projet.id, code='B', description='Conception', duree=3
            )
            db.session.add_all([tache_a, tache_b])
            db.session.flush()
            db.session.add(Dependency(successor_id=tache_b.id, predecessor_id=tache_a.id))
        return projet

    def _client_connecte(self, utilisateur):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(utilisateur.id)
            session['_fresh'] = True
        return client

    @staticmethod
    def _compter_selects():
        """Installe un compteur de SELECT et renvoie (compteur, desinstaller)."""
        compteur = {'selects': 0}

        def ecouter(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('SELECT'):
                compteur['selects'] += 1

        moteur = db.engine
        event.listen(moteur, 'before_cursor_execute', ecouter)

        def desinstaller():
            event.remove(moteur, 'before_cursor_execute', ecouter)

        return compteur, desinstaller

    # ------------------------------------------------------------------
    # Services groupés
    # ------------------------------------------------------------------
    def test_lots_de_cent_et_une_requete_par_lot(self):
        self.assertEqual(100, TAILLE_LOT)
        proprietaire_id = self.alice.id  # évite un SELECT de rafraîchissement
        compteur, desinstaller = self._compter_selects()
        try:
            resume = recalculer_projets(self.ids_alice, user_id=proprietaire_id)
        finally:
            desinstaller()
        self.assertEqual(205, resume['traites'])
        self.assertEqual(205, resume['reussis'])
        self.assertEqual(0, resume['echoues'])
        self.assertEqual([], resume['erreurs'])
        # 205 identifiants -> 3 lots -> exactement 3 SELECT de chargement.
        self.assertEqual(3, compteur['selects'])

    def test_recalcul_respecte_la_propriete(self):
        ids = self.ids_alice[:3] + self.ids_bob[:2] + [999999]
        resume = recalculer_projets(ids, user_id=self.alice.id)
        self.assertEqual(6, resume['traites'])
        self.assertEqual(3, resume['reussis'])
        self.assertEqual(3, resume['echoues'])
        self.assertEqual(3, len(resume['erreurs']))
        self.assertTrue(all('accès refusé' in e['message'] for e in resume['erreurs']))
        # Les projets de Bob restent intacts (aucune écriture de toute façon).
        self.assertEqual(3, Project.query.filter_by(user_id=self.bob.id).count())

    def test_sans_proprietaire_aucun_projet_nest_touche(self):
        resume = recalculer_projets(self.ids_alice[:2])
        self.assertEqual(2, resume['echoues'])
        self.assertEqual(0, resume['reussis'])
        self.assertIn('Propriétaire non spécifié', resume['erreurs'][0]['message'])
        self.assertEqual(205, Project.query.filter_by(user_id=self.alice.id).count())

    def test_suppression_groupee_cascade_et_propriete(self):
        ids = self.ids_alice[:2] + self.ids_bob[:1]
        taches_avant = Task.query.count()
        dependances_avant = Dependency.query.count()
        proprietaire_id = self.alice.id
        compteur, desinstaller = self._compter_selects()
        try:
            resume = supprimer_projets(ids, user_id=proprietaire_id)
        finally:
            desinstaller()
        self.assertEqual(3, resume['traites'])
        self.assertEqual(2, resume['reussis'])
        self.assertEqual(1, resume['echoues'])
        self.assertEqual(1, compteur['selects'])
        self.assertEqual(4, taches_avant - Task.query.count())
        self.assertEqual(2, dependances_avant - Dependency.query.count())
        self.assertIsNone(Project.query.filter_by(id=self.ids_alice[0]).first())
        self.assertIsNotNone(Project.query.filter_by(id=self.ids_bob[0]).first())
        self.assertEqual(203, Project.query.filter_by(user_id=self.alice.id).count())

    def test_suppression_par_lots_de_cent(self):
        proprietaire_id = self.alice.id
        compteur, desinstaller = self._compter_selects()
        try:
            resume = supprimer_projets(self.ids_alice, user_id=proprietaire_id)
        finally:
            desinstaller()
        self.assertEqual(205, resume['reussis'])
        self.assertEqual(0, Project.query.filter_by(user_id=self.alice.id).count())
        self.assertEqual(3, Project.query.filter_by(user_id=self.bob.id).count())
        self.assertEqual(0, Task.query.filter_by(project_id=self.ids_alice[0]).count())
        self.assertEqual(3, compteur['selects'])

    def test_export_csv_uniquement_les_projets_du_proprietaire(self):
        ids = self.ids_alice[:2] + self.ids_bob[:1]
        resume = exporter_projets(ids, user_id=self.alice.id)
        self.assertEqual(3, resume['traites'])
        self.assertEqual(2, resume['reussis'])
        self.assertEqual(1, resume['echoues'])
        texte = resume['donnees'].decode('utf-8-sig')
        self.assertIn('Projet Alpha 000', texte)
        self.assertIn('Projet Alpha 001', texte)
        self.assertNotIn('Projet Beta 000', texte)
        self.assertIn('Durée totale (j)', texte)
        self.assertEqual('projets_export.csv', resume['nom_fichier'])

    # ------------------------------------------------------------------
    # Pagination et filtres
    # ------------------------------------------------------------------
    def test_pagination_defaut_et_navigation(self):
        client = self._client_connecte(self.alice)
        premiere = client.get('/projects/')
        self.assertEqual(200, premiere.status_code)
        html = premiere.get_data(as_text=True)
        self.assertEqual(25, html.count('type="checkbox" name="ids"'))
        self.assertIn('205 projet(s) — page 1 sur 9', html)
        self.assertIn('Suivant', html)
        self.assertNotIn('>Précédent</a>', html)

        deuxieme = client.get('/projects/?page=2')
        html = deuxieme.get_data(as_text=True)
        self.assertEqual(25, html.count('type="checkbox" name="ids"'))
        self.assertIn('Précédent', html)
        self.assertIn('page 2 sur 9', html)

    def test_par_page_borne_a_cent_et_page_au_dela_de_la_fin(self):
        client = self._client_connecte(self.alice)
        html = client.get('/projects/?par_page=500').get_data(as_text=True)
        self.assertEqual(100, html.count('type="checkbox" name="ids"'))

        html = client.get('/projects/?par_page=10&page=999').get_data(as_text=True)
        self.assertEqual(5, html.count('type="checkbox" name="ids"'))
        self.assertIn('page 21 sur 21', html)

    def test_filtres_q_et_statut(self):
        projet = db.session.get(Project, self.ids_alice[0])
        projet.name = 'Projet Cible prioritaire'
        db.session.commit()
        client = self._client_connecte(self.alice)

        html = client.get('/projects/?q=Cible').get_data(as_text=True)
        self.assertEqual(1, html.count('type="checkbox" name="ids"'))
        self.assertIn('Projet Cible prioritaire', html)
        self.assertIn('1 projet(s) — page 1 sur 1', html)

        projet.status = 'archive'
        db.session.get(Project, self.ids_alice[1]).status = 'archive'
        db.session.commit()
        html = client.get('/projects/?statut=archive').get_data(as_text=True)
        self.assertEqual(2, html.count('type="checkbox" name="ids"'))
        self.assertIn('2 projet(s)', html)

        html = client.get('/projects/?statut=inconnu').get_data(as_text=True)
        self.assertEqual(25, html.count('type="checkbox" name="ids"'))

    def test_intrus_ne_voit_pas_les_projets_dans_la_liste(self):
        client = self._client_connecte(self.bob)
        html = client.get('/projects/').get_data(as_text=True)
        self.assertEqual(3, html.count('type="checkbox" name="ids"'))
        self.assertNotIn('Projet Alpha 000', html)

    # ------------------------------------------------------------------
    # Routes groupées
    # ------------------------------------------------------------------
    def test_route_recalcul_groupe_avec_propriete(self):
        client = self._client_connecte(self.alice)
        response = client.post('/projects/batch/recalculer', data={
            'ids': [str(self.ids_alice[0]), str(self.ids_alice[1]), str(self.ids_bob[0])],
        })
        self.assertEqual(302, response.status_code)
        self.assertIn('/projects/', response.headers.get('Location', ''))
        html = client.get(response.headers['Location']).get_data(as_text=True)
        self.assertIn('2 projet(s) recalculé(s) avec succès', html)
        self.assertIn('projet(s) n&#39;ont pas pu être recalculés', html)
        self.assertEqual(205, Project.query.filter_by(user_id=self.alice.id).count())
        self.assertEqual(3, Project.query.filter_by(user_id=self.bob.id).count())

    def test_route_suppression_groupe_avec_propriete(self):
        client = self._client_connecte(self.alice)
        ids = [str(self.ids_alice[0]), str(self.ids_bob[0])]
        response = client.post('/projects/batch/supprimer', data={'ids': ids})
        self.assertEqual(302, response.status_code)
        self.assertIsNone(Project.query.filter_by(id=self.ids_alice[0]).first())
        self.assertIsNotNone(Project.query.filter_by(id=self.ids_bob[0]).first())
        html = client.get('/projects/').get_data(as_text=True)
        self.assertIn('1 projet(s) supprimé(s)', html)
        self.assertIn('projet(s) n&#39;ont pas pu être supprimés', html)

    def test_intrus_ne_peut_supprimer_ni_recalculer(self):
        client = self._client_connecte(self.bob)
        ids = [str(i) for i in self.ids_alice[:5]]

        suppression = client.post('/projects/batch/supprimer', data={'ids': ids})
        self.assertEqual(302, suppression.status_code)
        self.assertEqual(205, Project.query.filter_by(user_id=self.alice.id).count())
        self.assertEqual(3, Project.query.filter_by(user_id=self.bob.id).count())

        recalcul = client.post('/projects/batch/recalculer', data={'ids': ids})
        self.assertEqual(302, recalcul.status_code)
        html = client.get('/projects/').get_data(as_text=True)
        self.assertIn('projet(s) n&#39;ont pas pu être recalculés', html)
        self.assertEqual(205, Project.query.filter_by(user_id=self.alice.id).count())

    def test_routes_groupees_exigent_la_connexion(self):
        client = self.app.test_client()
        for chemin in ('/projects/batch/recalculer', '/projects/batch/supprimer'):
            response = client.post(chemin, data={'ids': [str(self.ids_alice[0])]})
            self.assertEqual(302, response.status_code)
            self.assertNotIn('/projects/batch', response.headers.get('Location', ''))
        self.assertEqual(205, Project.query.filter_by(user_id=self.alice.id).count())

    def test_token_csrf_exige_sur_les_actions_groupees(self):
        self.app.config['WTF_CSRF_ENABLED'] = True
        client = self._client_connecte(self.alice)
        donnees = {'ids': [str(self.ids_alice[0])]}
        self.assertEqual(400, client.post('/projects/batch/supprimer', data=donnees).status_code)

        html = client.get('/projects/').get_data(as_text=True)
        import re
        correspondance = re.search(r'name="csrf_token" value="([^"]+)"', html)
        self.assertIsNotNone(correspondance)
        donnees['csrf_token'] = correspondance.group(1)
        self.assertEqual(
            302, client.post('/projects/batch/supprimer', data=donnees).status_code
        )
        self.assertEqual(204, Project.query.filter_by(user_id=self.alice.id).count())

    # ------------------------------------------------------------------
    # Non-régression
    # ------------------------------------------------------------------
    def test_routes_historiques_et_indexes_preserves(self):
        regles = {regle.rule for regle in self.app.url_map.iter_rules()}
        pour_chemins = (
            '/projects/', '/projects/new', '/projects/<int:project_id>/edit',
            '/projects/<int:project_id>/delete', '/projects/<int:project_id>/archive',
            '/projects/<int:project_id>/unarchive', '/projects/<int:project_id>',
            '/projects/<int:project_id>/optimisation',
            '/projects/<int:project_id>/import',
            '/projects/<int:project_id>/export.csv',
            '/projects/<int:project_id>/export.xlsx',
            '/projects/<int:project_id>/rapport.pdf',
            '/projects/<int:project_id>/tasks/new',
            '/projects/<int:project_id>/tasks/<int:task_id>/edit',
            '/projects/<int:project_id>/tasks/<int:task_id>/delete',
            '/projects/batch/recalculer', '/projects/batch/supprimer',
        )
        for chemin in pour_chemins:
            self.assertIn(chemin, regles)

        indexes_projet = {i.name: i for i in Project.__table__.indexes}
        indexes_tache = {i.name: i for i in Task.__table__.indexes}
        self.assertIn('ix_project_user_id_created_at', indexes_projet)
        self.assertEqual(
            {'user_id', 'created_at'},
            {c.name for c in indexes_projet['ix_project_user_id_created_at'].columns},
        )
        self.assertIn('ix_task_project_id_batch', indexes_tache)
        self.assertEqual(
            {'project_id'},
            {c.name for c in indexes_tache['ix_task_project_id_batch'].columns},
        )


if __name__ == '__main__':
    unittest.main()
