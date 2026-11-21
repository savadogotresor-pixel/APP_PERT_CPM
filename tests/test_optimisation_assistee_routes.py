"""Tests HTTP de l'optimisation assistée (blueprint ``optimisation_ia``).

Ils vérifient le contrôle d'accès, le rendu contemplatif, la cohérence des
gains avec ``simuler()``, l'absence de modification du planning de référence
et l'écriture d'un audit non sensible.
"""

import ast
import json
import os
import re
import unittest
import uuid
from collections import Counter
from datetime import date

# La configuration de test DOIT être sélectionnée avant l'import de app.
os.environ['APP_SETTINGS'] = 'config.TestConfig'

from sqlalchemy import create_engine

from app import app, db as app_db
from logic.pert import compute
from models import AuditLog, Dependency, Project, Task, User, project_to_taches
from services.optimisation import simuler
from services.optimisation_assistee import suggerer_scenarios


def _enregistrer_blueprint_si_absent():
    """Câble le blueprint côté test si l'autre agent n'a pas encore édité app.py.

    Le blueprint est enregistré dès l'import du module de test, donc avant le
    premier request : Flask 3.x interdit toute modification de la carte d'URL
    après coup. Dès que ``app.py`` enregistre le blueprint, ce repli devient
    inertie (l'endpoint existe déjà).
    """
    if 'optimisation_ia.assister' in app.view_functions:
        return
    try:
        from routes_optimisation_ia import optimisation_ia_bp
        app.register_blueprint(optimisation_ia_bp)
    except Exception:
        # L'agent parallèle a peut-être déjà enregistré un blueprint équivalent
        # sous un autre nom : laissez la route réelle décider du résultat.
        pass


_enregistrer_blueprint_si_absent()


class OptimisationAssisteeRoutesTestCase(unittest.TestCase):
    """Parcours client Flask sur une base SQLite mémoire isolée."""

    @classmethod
    def setUpClass(cls):
        cls.app = app
        db = app.extensions.get('sqlalchemy', app_db)
        cls.db = db
        app.config.from_object('config.TestConfig')
        app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY='test-optimisation-assistee',
        )

        with app.app_context():
            db.session.remove()
            moteur = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
            moteurs = db._app_engines.setdefault(app, {})
            ancien = moteurs.get(None)
            if ancien is not None:
                ancien.dispose()
            moteurs[None] = moteur
            db.create_all()

        cls._enregistrer_donnees()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            cls.db.session.remove()
            AuditLog.query.filter(
                AuditLog.user_id.in_((cls.owner_id, cls.intrus_id))
            ).delete(synchronize_session=False)
            for identifiant in (cls.projet_id, cls.projet_vide_id):
                projet = cls.db.session.get(Project, identifiant)
                if projet is not None:
                    cls.db.session.delete(projet)
            for identifiant in (cls.owner_id, cls.intrus_id):
                utilisateur = cls.db.session.get(User, identifiant)
                if utilisateur is not None:
                    cls.db.session.delete(utilisateur)
            cls.db.session.commit()
            cls.db.session.remove()
        os.environ.pop('APP_SETTINGS', None)

    @classmethod
    def _enregistrer_donnees(cls):
        suffixe = uuid.uuid4().hex[:8]
        db = cls.db
        with cls.app.app_context():
            proprietaire = User(
                username=f'optia_proprio_{suffixe}',
                email=f'optia_proprio_{suffixe}@example.com',
            )
            proprietaire.set_password('Password123')
            intrus = User(
                username=f'optia_intrus_{suffixe}',
                email=f'optia_intrus_{suffixe}@example.com',
            )
            intrus.set_password('Password123')
            db.session.add_all((proprietaire, intrus))
            db.session.commit()

            projet = Project(
                user_id=proprietaire.id,
                name=f'Optimisation assistée {suffixe}',
                description='Chaîne critique A(4) → B(6) → C(2)',
                date_reference=date(2026, 11, 21),
                status='actif',
            )
            projet_vide = Project(
                user_id=proprietaire.id,
                name=f'Optimisation sans tâche {suffixe}',
                description='Aucune tâche critique',
                date_reference=date(2026, 11, 21),
                status='actif',
            )
            db.session.add_all((projet, projet_vide))
            db.session.commit()

            chai_ne = (
                ('A', 4, ()),
                ('B', 6, ('A',)),
                ('C', 2, ('B',)),
            )
            objets = {}
            for code, duree, _predecesseurs in chai_ne:
                tache = Task(
                    project_id=projet.id,
                    code=code,
                    description=f'Tâche {code}',
                    duree=duree,
                )
                db.session.add(tache)
                db.session.flush()
                objets[code] = tache
            for code, _duree, predecesseurs in chai_ne:
                for predecesseur in predecesseurs:
                    db.session.add(Dependency(
                        successor_id=objets[code].id,
                        predecessor_id=objets[predecesseur].id,
                        type_relation='FS',
                    ))
            db.session.commit()

            cls.owner_id = proprietaire.id
            cls.intrus_id = intrus.id
            cls.owner_username = proprietaire.username
            cls.intrus_username = intrus.username
            cls.projet_id = projet.id
            cls.projet_vide_id = projet_vide.id
            cls.url = f'/projects/{projet.id}/optimisation/assistee'
            cls.url_vide = f'/projects/{projet_vide.id}/optimisation/assistee'

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    @staticmethod
    def _html(reponse):
        return reponse.get_data(as_text=True)

    def _get(self, url, client=None):
        """GET avec un unique réessai si le blueprint parallelise son câblage."""
        client = client or self.client
        reponse = client.get(url)
        if reponse.status_code == 404:
            _enregistrer_blueprint_si_absent()
            reponse = client.get(url)
        return reponse

    def _connecter(self, identifiant, client=None):
        client = client or self.client
        reponse = client.post('/login', data={
            'username': identifiant,
            'email': identifiant,
            'password': 'Password123',
        })
        self.assertIn(
            reponse.status_code, (200, 302),
            msg=self._html(reponse)[:1000],
        )
        return reponse

    def _nettoyer_logs(self):
        with self.app.app_context():
            AuditLog.query.filter(
                AuditLog.action == 'IA_OPTIMISATION',
                AuditLog.user_id.in_((self.owner_id, self.intrus_id)),
            ).delete(synchronize_session=False)
            self.db.session.commit()

    @staticmethod
    def _decoder_details(texte):
        if isinstance(texte, dict):
            return texte
        if not isinstance(texte, str):
            return None
        for decodeur in (ast.literal_eval, json.loads):
            try:
                valeur = decodeur(texte)
            except (ValueError, SyntaxError, TypeError):
                continue
            if isinstance(valeur, dict):
                return valeur
        return None

    def _details_logues(self, projet_id):
        with self.app.app_context():
            logs = AuditLog.query.filter_by(action='IA_OPTIMISATION').all()
            details = [self._decoder_details(log.details) for log in logs]
        return [
            item for item in details
            if isinstance(item, dict) and item.get('project_id') == projet_id
        ]

    def _contexte_attendu(self, projet_id):
        with self.app.app_context():
            projet = self.db.session.get(Project, projet_id)
            taches = project_to_taches(projet)
            resultat = compute(taches)
            scenarios = suggerer_scenarios(
                taches,
                resultat.resultats,
                resultat.duree_totale,
                resultat.chemins_critiques,
                resultat.successeurs,
            )
            return taches, resultat, scenarios

    def _durees_projet(self, projet_id):
        with self.app.app_context():
            taches = (
                self.db.session.query(Task)
                .filter_by(project_id=projet_id)
                .order_by(Task.code)
                .all()
            )
            return {tache.code: tache.duree for tache in taches}

    def setUp(self):
        self.client = self.app.test_client()
        _enregistrer_blueprint_si_absent()
        self._nettoyer_logs()

    # ------------------------------------------------------------------
    # 1. Sécurité
    # ------------------------------------------------------------------
    def test_anonyme_et_intrus_sans_audit(self):
        anonyme = self._get(self.url, client=self.app.test_client())
        self.assertEqual(302, anonyme.status_code)
        self.assertIn('/login', anonyme.headers.get('Location', ''))

        intrus = self.app.test_client()
        self._connecter(self.intrus_username, client=intrus)
        refuse = self._get(self.url, client=intrus)
        self.assertEqual(403, refuse.status_code)
        self.assertEqual([], self._details_logues(self.projet_id))

    # ------------------------------------------------------------------
    # 2. Rendu et chiffres
    # ------------------------------------------------------------------
    def test_chaine_a4_b6_c2_rendu_et_gains_simules(self):
        taches, resultat, scenarios_attendus = self._contexte_attendu(self.projet_id)
        self.assertEqual(12, resultat.duree_totale)
        self.assertTrue(scenarios_attendus)
        self.assertTrue(any(s['type'] == 'crashing' for s in scenarios_attendus))

        gains_attendus = Counter(
            str(scenario['resultat']['gains']) for scenario in scenarios_attendus
        )
        for scenario in scenarios_attendus:
            if scenario['type'] == 'crashing':
                self.assertEqual(
                    simuler(taches, scenario['reductions']),
                    scenario['resultat'],
                )

        avant = self._durees_projet(self.projet_id)
        self._connecter(self.owner_username)
        reponse = self._get(self.url)
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        html = self._html(reponse)

        self.assertIn('12 jours', html)
        self.assertIn('data-type="crashing"', html)
        gains_html = Counter(re.findall(r'data-gain="([^"]+)"', html))
        self.assertEqual(gains_attendus, gains_html)

        self.assertEqual(avant, self._durees_projet(self.projet_id))

    def test_encadres_bandeau_et_avertissement_fast_tracking(self):
        self._connecter(self.owner_username)
        html = self._html(self._get(self.url))

        attestations = (
            "Le planning de référence n'est pas modifié : ces scénarios sont "
            "des hypothèses recalculées par le moteur PERT/CPM.",
            'Avertissement fast-tracking',
            'contraintes métier non fournies',
            'Hypothèses',
            'Limites',
            'Compromis',
            'Modifications envisagées',
            'Planning de référence inchangé',
            'Mes projets',
            'Détail du projet',
            'Déconnexion',
        )
        for attendu in attestations:
            with self.subTest(attendu=attendu):
                self.assertIn(attendu, html)

        self.assertNotIn('Aucun scénario exploitable', html)
        self.assertNotIn('<form', html)
        self.assertNotIn('type="submit"', html)

    def test_planning_reference_non_modifie(self):
        avant = self._durees_projet(self.projet_id)
        self._connecter(self.owner_username)
        self.assertEqual(200, self._get(self.url).status_code)
        self.assertEqual(avant, self._durees_projet(self.projet_id))

    # ------------------------------------------------------------------
    # 3. Cas limite et audit
    # ------------------------------------------------------------------
    def test_projet_sans_tache_critique(self):
        self._connecter(self.owner_username)
        reponse = self._get(self.url_vide)
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        html = self._html(reponse)
        self.assertIn(
            'Aucun scénario exploitable : aucune tâche critique à durée positive.',
            html,
        )
        self.assertEqual(
            [{'project_id': self.projet_vide_id, 'nb_scenarios': 0}],
            self._details_logues(self.projet_vide_id),
        )

    def test_audit_avec_nombre_de_scenarios(self):
        _taches, _resultat, scenarios = self._contexte_attendu(self.projet_id)
        self._connecter(self.owner_username)
        self.assertEqual(200, self._get(self.url).status_code)
        self.assertEqual(
            [{'project_id': self.projet_id, 'nb_scenarios': len(scenarios)}],
            self._details_logues(self.projet_id),
        )


if __name__ == '__main__':
    unittest.main()
