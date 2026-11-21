"""Tests unitaires des modèles, de la conversion PERT et des droits d'accès."""

import unittest
from datetime import date

from flask import Flask
from sqlalchemy.exc import IntegrityError

from config import TestConfig
from extensions import csrf, db, login_manager
from models import (
    Dependency,
    Project,
    ProjectAccess,
    Task,
    User,
    init_db,
    project_to_taches,
    user_can_access,
)


class ModelsTestCase(unittest.TestCase):
    """Chaque test dispose de sa propre application et de sa propre base mémoire."""

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.from_object(TestConfig)
        db.init_app(self.app)
        login_manager.init_app(self.app)
        csrf.init_app(self.app)

        self.app_context = self.app.app_context()
        self.app_context.push()
        init_db(self.app)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def creer_utilisateur(self, username='alice', email='alice@example.com',
                          admin=False, password='secret'):
        utilisateur = User(
            username=username, email=email, is_admin=admin
        )
        utilisateur.set_password(password)
        db.session.add(utilisateur)
        db.session.commit()
        return utilisateur

    def creer_projet(self, proprietaire, name='Projet test'):
        projet = Project(
            user_id=proprietaire.id,
            name=name,
            description='Description',
            date_reference=date(2026, 11, 21),
        )
        db.session.add(projet)
        db.session.commit()
        return projet

    def test_mot_de_passe_hache(self):
        utilisateur = User(username='bob', email='bob@example.com')
        utilisateur.set_password('motdepasse')

        self.assertNotEqual('motdepasse', utilisateur.password_hash)
        self.assertTrue(utilisateur.check_password('motdepasse'))
        self.assertFalse(utilisateur.check_password('incorrect'))
        self.assertNotIn('motdepasse', utilisateur.password_hash)

    def test_unicite_username_et_email(self):
        self.creer_utilisateur()

        doublon_username = User(username='alice', email='autre@example.com')
        doublon_username.set_password('secret')
        db.session.add(doublon_username)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        doublon_email = User(username='autre', email='alice@example.com')
        doublon_email.set_password('secret')
        db.session.add(doublon_email)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        self.assertEqual(1, User.query.count())

    def test_creation_projet_taches_dependances(self):
        proprietaire = self.creer_utilisateur()
        projet = self.creer_projet(proprietaire)

        tache_a = Task(
            project_id=projet.id, code='A', description='Analyse', duree=2
        )
        tache_b = Task(
            project_id=projet.id, code='B', description='Conception', duree=3
        )
        db.session.add_all([tache_a, tache_b])
        db.session.commit()

        dependance = Dependency(
            successor_id=tache_b.id, predecessor_id=tache_a.id, type_relation='FS'
        )
        db.session.add(dependance)
        db.session.commit()

        self.assertEqual(2, Task.query.count())
        self.assertEqual(1, Dependency.query.count())
        self.assertEqual('actif', projet.status)
        self.assertEqual(projet.id, tache_a.project_id)
        self.assertEqual(tache_a.id, dependance.predecessor_id)
        self.assertEqual(tache_b.id, dependance.successor_id)
        self.assertEqual([tache_a.code], [d.predecessor.code for d in tache_b.dependances])

    def test_project_to_taches(self):
        proprietaire = self.creer_utilisateur()
        projet = self.creer_projet(proprietaire)

        tache_a = Task(project_id=projet.id, code='A', description='Analyse', duree=2)
        tache_b = Task(project_id=projet.id, code='B', description='Conception', duree=3)
        db.session.add_all([tache_a, tache_b])
        db.session.commit()
        db.session.add(Dependency(successor_id=tache_b.id, predecessor_id=tache_a.id))
        db.session.commit()

        attendu = {
            'A': {'description': 'Analyse', 'duree': 2, 'depend_de': []},
            'B': {'description': 'Conception', 'duree': 3, 'depend_de': ['A']},
        }
        self.assertEqual(attendu, project_to_taches(projet))

    def test_user_can_access(self):
        proprietaire = self.creer_utilisateur()
        autre = self.creer_utilisateur('bob', 'bob@example.com')
        admin = self.creer_utilisateur('admin', 'admin@example.com', admin=True)
        projet = self.creer_projet(proprietaire)

        self.assertTrue(user_can_access(projet, proprietaire))
        self.assertFalse(user_can_access(projet, autre))
        self.assertTrue(user_can_access(projet, admin))

        db.session.add(ProjectAccess(project_id=projet.id, user_id=autre.id,
                                     niveau='lecture'))
        db.session.commit()
        self.assertTrue(user_can_access(projet, autre))

    def test_cascade_suppression_projet(self):
        proprietaire = self.creer_utilisateur()
        projet = self.creer_projet(proprietaire)

        tache_a = Task(project_id=projet.id, code='A', description='Analyse', duree=2)
        tache_b = Task(project_id=projet.id, code='B', description='Conception', duree=3)
        db.session.add_all([tache_a, tache_b])
        db.session.commit()
        db.session.add(Dependency(successor_id=tache_b.id, predecessor_id=tache_a.id))
        db.session.commit()

        db.session.delete(projet)
        db.session.commit()

        self.assertEqual(0, Project.query.count())
        self.assertEqual(0, Task.query.count())
        self.assertEqual(0, Dependency.query.count())


if __name__ == '__main__':
    unittest.main()
