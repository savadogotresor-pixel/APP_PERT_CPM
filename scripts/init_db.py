"""Crée/migre la base SQL de l'application PERT/CPM.

Usage local ::

    python scripts/init_db.py

La configuration est lue depuis ``APP_SETTINGS`` (par défaut ``config.Config``),
donc ``DATABASE_URL`` est respectée : PostgreSQL en production, SQLite dans
``instance/pert_cpm.db`` en local. L'opération est idempotente : elle peut être
relancée sans détruire les comptes ni les projets existants.
"""

import os
import sys

CHEMIN_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CHEMIN_RACINE not in sys.path:
    sys.path.insert(0, CHEMIN_RACINE)

from flask import Flask  # noqa: E402
from sqlalchemy import inspect  # noqa: E402

from extensions import db  # noqa: E402
from models import init_db  # noqa: E402


def main():
    application = Flask(
        'init_db',
        root_path=CHEMIN_RACINE,
        instance_path=os.path.join(CHEMIN_RACINE, 'instance'),
    )
    application.config.from_object(
        os.environ.get('APP_SETTINGS', 'config.Config')
    )
    db.init_app(application)
    init_db(application)

    with application.app_context():
        print('Chemin de la base :', db.engine.url)
        inspecteur = inspect(db.engine)
        tables = sorted(inspecteur.get_table_names())
        print('Base SQL prête :', application.config['SQLALCHEMY_DATABASE_URI'])
        print('Tables :', ', '.join(tables))
        for table in tables:
            nombre = db.session.execute(
                db.text('SELECT COUNT(*) FROM "' + table + '"')
            ).scalar()
            print(f'  {table}: {nombre} ligne(s)')


if __name__ == '__main__':
    main()
