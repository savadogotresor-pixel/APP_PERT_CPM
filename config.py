"""Configuration de l'application PERT/CPM (développement, production, tests)."""

import os
from datetime import timedelta


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

    # « Se souvenir de moi » : cookie persistant Flask-Login explicite.
    REMEMBER_COOKIE_NAME = 'remember_token'
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_REFRESH_EACH_REQUEST = True
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    # Assistant IA (§19) : la clé reste exclusivement côté serveur. Elle n'est
    # jamais sérialisée dans un template, un JSON ou le journal d'audit.
    IA_API_KEY = os.environ.get('IA_API_KEY')
    IA_API_URL = os.environ.get(
        'IA_API_URL', 'https://api.openai.com/v1/chat/completions'
    )
    IA_MODEL = os.environ.get('IA_MODEL', 'gpt-4o-mini')
    IA_TIMEOUT = float(os.environ.get('IA_TIMEOUT', '10.0'))
    IA_ENABLED = os.environ.get('IA_ENABLED', '1') != '0'
    IA_MAX_APPELS_PAR_HEURE = int(os.environ.get('IA_MAX_APPELS_PAR_HEURE', '60'))

    # Vérification de l'adresse e-mail (code à 6 chiffres, valable 15 minutes).
    # En développement, la vérification reste désactivée par défaut : on
    # l'active explicitement avec EMAIL_VERIFICATION_ENABLED=1.
    EMAIL_VERIFICATION_ENABLED = os.environ.get('EMAIL_VERIFICATION_ENABLED', '0') == '1'

    # SMTP : aucune dépendance externe, uniquement smtplib/email de la stdlib.
    # Sans MAIL_SERVER, le code est affiché une fois dans un flash (dev uniquement).
    MAIL_SERVER = os.environ.get('MAIL_SERVER', '')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', '587'))
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', '1') != '0'
    MAIL_DEFAULT_SENDER = os.environ.get(
        'MAIL_DEFAULT_SENDER', 'PERT/CPM <no-reply@pert-cpm.local>'
    )


class _SecretProduction:
    """Descriptor : exige une variable d'environnement au moment de sa lecture.

    La validation est volontairement différée : ``config`` doit rester
    importable en développement et sous les tests, où aucun secret n'est
    défini. Flask lit les attributs via ``getattr`` dans ``from_object``,
    donc la configuration de production échoue au démarrage si une variable
    manque, sans toucher à Config/TestConfig.
    """

    def __init__(self, variable, normaliser_postgres=False):
        self.variable = variable
        self.normaliser_postgres = normaliser_postgres

    def __get__(self, instance, owner):
        valeur = os.environ.get(self.variable)
        if not valeur:
            raise RuntimeError(
                f"ProductionConfig exige la variable d'environnement "
                f"{self.variable} (à définir dans Render avant le démarrage)."
            )
        if self.normaliser_postgres and valeur.startswith('postgres://'):
            valeur = 'postgresql://' + valeur[len('postgres://'):]
        return valeur


class ProductionConfig(Config):
    """Configuration de production : secrets obligatoires et cookies sûrs."""

    SECRET_KEY = _SecretProduction('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = _SecretProduction('DATABASE_URL', normaliser_postgres=True)
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    PREFERRED_URL_SCHEME = 'https'
    WTF_CSRF_ENABLED = True
    # Activée en production, désactivable avec EMAIL_VERIFICATION_ENABLED=0.
    EMAIL_VERIFICATION_ENABLED = os.environ.get('EMAIL_VERIFICATION_ENABLED', '1') != '0'


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False
    SECRET_KEY = 'test'
    # La suite historique conserve son comportement : connexion directe.
    EMAIL_VERIFICATION_ENABLED = False
