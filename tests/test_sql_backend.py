"""Vérifie que la persistance est bien SQL et que le moteur est configurable.

L'application n'utilise aucun fichier JSON, pickle ou stockage propriétaire
pour ses données métier : comptes, projets, tâches, dépendances, accès,
scénarios, audits, alertes et clé RSA sont des tables SQLAlchemy. Ce module
vérifie également que ``DATABASE_URL`` accepte SQLite, PostgreSQL et
MySQL/MariaDB.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine, inspect

import config
import crypto_rsa
from config import Config, ProductionConfig, TestConfig, _normaliser_uri_sql
from extensions import db
from models import (
    AlerteRisque, AuditLog, ClefCrypto, Dependency, Project, ProjectAccess,
    Scenario, Task, User,
)


class SqlBackendTestCase(unittest.TestCase):
    def test_uri_postgres_alias_est_normalise(self):
        self.assertEqual(
            'postgresql://u:p@hote:5432/base',
            _normaliser_uri_sql('postgres://u:p@hote:5432/base'),
        )
        self.assertEqual(
            'postgresql://u:p@hote:5432/base',
            _normaliser_uri_sql('postgresql://u:p@hote:5432/base'),
        )
        # Une URI explicite avec un autre pilote PostgreSQL reste inchangée.
        self.assertEqual(
            'postgresql+psycopg://u:p@hote/base',
            _normaliser_uri_sql('postgresql+psycopg://u:p@hote/base'),
        )

    def test_uri_mysql_et_mariadb_utilisent_pymysql(self):
        self.assertEqual(
            'mysql+pymysql://u:p@hote:3306/base',
            _normaliser_uri_sql('mysql://u:p@hote:3306/base'),
        )
        self.assertEqual(
            'mysql+pymysql://u:p@hote:3306/base',
            _normaliser_uri_sql('mariadb://u:p@hote:3306/base'),
        )
        self.assertEqual(
            'mysql+pymysql://u:p@hote/base',
            _normaliser_uri_sql('mysql+pymysql://u:p@hote/base'),
        )

    def test_uri_sqlite_est_conservee(self):
        self.assertEqual(
            'sqlite:///pert_cpm.db',
            _normaliser_uri_sql('sqlite:///pert_cpm.db'),
        )
        self.assertEqual(
            'sqlite:///:memory:',
            _normaliser_uri_sql('sqlite:///:memory:'),
        )

    def test_default_et_test_config_sont_sqlite(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual('sqlite:///pert_cpm.db', config._database_uri())
        self.assertTrue(Config.SQLALCHEMY_DATABASE_URI.startswith('sqlite:'))
        self.assertEqual('sqlite:///:memory:', TestConfig.SQLALCHEMY_DATABASE_URI)

    def test_production_accepte_nimporte_quelle_uri_sql(self):
        with patch.dict(os.environ, {
            'DATABASE_URL': 'mysql://u:p@hote:3306/base',
            'SECRET_KEY': 'cle-de-test',
        }):
            self.assertEqual(
                'mysql+pymysql://u:p@hote:3306/base',
                ProductionConfig.SQLALCHEMY_DATABASE_URI,
            )
        with patch.dict(os.environ, {
            'DATABASE_URL': 'postgres://u:p@hote/base',
            'SECRET_KEY': 'cle-de-test',
        }):
            self.assertEqual(
                'postgresql://u:p@hote/base',
                ProductionConfig.SQLALCHEMY_DATABASE_URI,
            )

    def test_tous_les_modeles_metier_sont_des_tables_sql(self):
        modeles = (
            User, Project, Task, Dependency, ProjectAccess,
            Scenario, AuditLog, AlerteRisque, ClefCrypto,
        )
        self.assertEqual(9, len(modeles))
        for modele in modeles:
            with self.subTest(modele=modele.__name__):
                self.assertTrue(issubclass(modele, db.Model))
                self.assertTrue(hasattr(modele, '__tablename__'))
                self.assertTrue(modele.__tablename__)

    def test_schema_sql_se_cree_sur_un_moteur_sql_neuf(self):
        moteur = create_engine('sqlite:///:memory:')
        try:
            db.Model.metadata.create_all(moteur)
            tables = set(inspect(moteur).get_table_names())
        finally:
            moteur.dispose()
        self.assertEqual({
            'alerte_risque', 'audit_log', 'clef_crypto', 'dependency',
            'project', 'project_access', 'scenario', 'task', 'user',
        }, tables)

    def test_cle_rsa_survit_a_un_redemarrage(self):
        """Une nouvelle instance lit la clé en SQL : les mots de passe restent valides."""
        with tempfile.TemporaryDirectory() as dossier:
            uri = 'sqlite:///' + os.path.join(dossier, 'cle.db')
            clefs = []
            for _ in range(2):
                application = Flask(__name__)
                application.config.update(
                    SQLALCHEMY_DATABASE_URI=uri,
                    SQLALCHEMY_TRACK_MODIFICATIONS=False,
                    SECRET_KEY='test-cle',
                )
                db.init_app(application)
                with application.app_context():
                    db.create_all()
                crypto_rsa.initialiser(application)
                clefs.append(crypto_rsa.cle_publique_pem())
                with application.app_context():
                    db.session.remove()
                    db.engine.dispose()
            self.assertEqual(clefs[0], clefs[1])


if __name__ == '__main__':
    unittest.main()
