"""Configuration de l'application PERT/CPM (développement, production, tests)."""

import os


def _database_uri():
    """URI SQLAlchemy, avec normalisation du préfixe PostgreSQL de Render."""
    uri = os.environ.get('DATABASE_URL', 'sqlite:///pert_cpm.db')
    if uri.startswith('postgres://'):
        uri = 'postgresql://' + uri[len('postgres://'):]
    return uri


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-a-changer'
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024
    WTF_CSRF_ENABLED = True


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False
    SECRET_KEY = 'test'
