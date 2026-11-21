"""Tests HTTP du lot IA-4 (blueprint ``ia_gestion``).

Ils couvrent les trois parcours livrés par ``routes_ia_gestion.py`` :

* analyse des risques (génération, persistance, statut commenté et audit sans
  donnée libre) ;
* actions en langage naturel (aperçu sans écriture, confirmation explicite,
  confirmation renforcée pour une suppression, recalcul après exécution) ;
* rapport intelligent (chiffres du moteur, sections, lien PDF et audit).

Le contrôle d'accès est vérifié sur les six verbes exposés, avec un code de
tâche distinctif qui ne doit jamais fuir dans une réponse d'erreur.
"""

import html as html_module
import os
import re
import unittest
import uuid

from flask import url_for
from sqlalchemy import create_engine

from logic.pert import compute
from models import (
    AlerteRisque,
    AuditLog,
    Dependency,
    Project,
    Task,
    User,
    project_to_taches,
)

TEXTE_AUGMENTATION = 'augmente la durée de la tâche B de 2 jours'
TEXTE_SUPPRESSION = 'supprime la tâche B'


def _enregistrer_blueprint_si_absent(app):
    """Câble le blueprint sidecar si app.py ne l'a pas encore enregistré.

    Le parlant est appelé avant le premier request : Flask 3.x interdit
    l'enregistrement d'un blueprint après le début du cycle requête/réponse.
    """
    if 'ia_gestion.risques' in app.view_functions:
        return
    try:
        from routes_ia_gestion import ia_gestion_bp

        app.register_blueprint(ia_gestion_bp)
    except Exception:
        # L'agent parallèle a peut-être déjà câblé un blueprint équivalent.
        pass


class IAGestionRoutesTestCase(unittest.TestCase):
    """Parcours client Flask sur une base SQLite mémoire isolée."""

    @classmethod
    def setUpClass(cls):
        # La configuration de test DOIT être choisie avant l'import de app.
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
            SECRET_KEY='test-ia4',
        )
        _enregistrer_blueprint_si_absent(app)

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

        cls.code_secret = f'SECRET_IA4_{uuid.uuid4().hex[:8]}'
        cls.proprietaire = cls._inscrire('ia4_proprio', app.test_client())
        cls.intrus = cls._inscrire('ia4_intrus', app.test_client())
        with app.app_context():
            cls.proprietaire_id = User.query.filter_by(
                username=cls.proprietaire
            ).first().id
            cls.intrus_id = User.query.filter_by(username=cls.intrus).first().id

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            cls.db.session.remove()
            cls.db.drop_all()
        os.environ.pop('APP_SETTINGS', None)

    def setUp(self):
        # Client neuf par test : aucune session ne fuite d'un scénario à l'autre.
        self.client = self.app.test_client()
        self.proprio_client = self.app.test_client()
        self.intrus_client = self.app.test_client()
        self._connecter(self.proprietaire, self.proprio_client)
        self._connecter(self.intrus, self.intrus_client)

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    @staticmethod
    def _html(reponse):
        return reponse.get_data(as_text=True)

    @staticmethod
    def _inscrire(prefixe, client):
        username = f'{prefixe}_{uuid.uuid4().hex[:10]}'
        reponse = client.post('/register', data={
            'username': username,
            'email': f'{username}@example.com',
            'password': 'Password123',
            'password_confirm': 'Password123',
        })
        assert reponse.status_code in (200, 302), IAGestionRoutesTestCase._html(reponse)[:1000]
        return username

    def _connecter(self, username, client):
        reponse = client.post('/login', data={
            'username': username,
            'email': username,
            'password': 'Password123',
        })
        self.assertIn(reponse.status_code, (200, 302), msg=self._html(reponse)[:1000])

    def _get(self, url, client=None, follow_redirects=False):
        """GET avec un unique réessai si le blueprint parallelise son câblage."""
        client = client or self.client
        reponse = client.get(url, follow_redirects=follow_redirects)
        if reponse.status_code == 404 and 'ia_gestion.risques' not in self.app.view_functions:
            _enregistrer_blueprint_si_absent(self.app)
            reponse = client.get(url, follow_redirects=follow_redirects)
        return reponse

    def _post(self, url, donnees, client=None, follow_redirects=False):
        client = client or self.client
        reponse = client.post(url, data=donnees, follow_redirects=follow_redirects)
        if reponse.status_code == 404 and 'ia_gestion.risques' not in self.app.view_functions:
            _enregistrer_blueprint_si_absent(self.app)
            reponse = client.post(url, data=donnees, follow_redirects=follow_redirects)
        return reponse

    @staticmethod
    def _url_risques(project_id):
        return f'/projects/{project_id}/risques'

    @staticmethod
    def _url_actions(project_id):
        return f'/projects/{project_id}/actions/assister'

    @staticmethod
    def _url_rapport(project_id):
        return f'/projects/{project_id}/rapport/intelligent'

    def _creer_projet(self, avec_secret=False):
        """Crée A(2) → B(3) → C(4) via HTTP + tâches cohérentes en base."""
        nom = f'IA4 chaine {uuid.uuid4().hex[:10]}'
        reponse = self.proprio_client.post('/projects/new', data={
            'name': nom,
            'description': 'Chaîne A(2) -> B(3) -> C(4)',
            'date_reference': '2026-11-21',
        })
        self.assertIn(reponse.status_code, (200, 302), msg=self._html(reponse)[:1000])

        with self.app.app_context():
            projet = Project.query.filter_by(name=nom).first()
            self.assertIsNotNone(projet, msg=self._html(reponse)[:1000])
            chaine = (('A', 2, None), ('B', 3, 'A'), ('C', 4, 'B'))
            objets = {}
            for code, duree, _predecesseur in chaine:
                tache = Task(
                    project_id=projet.id,
                    code=code,
                    description=f'Tâche {code}',
                    duree=duree,
                )
                self.db.session.add(tache)
                self.db.session.flush()
                objets[code] = tache
            for code, _duree, predecesseur in chaine:
                if predecesseur is not None:
                    self.db.session.add(Dependency(
                        successor_id=objets[code].id,
                        predecessor_id=objets[predecesseur].id,
                        type_relation='FS',
                    ))
            if avec_secret:
                self.db.session.add(Task(
                    project_id=projet.id,
                    code=self.code_secret,
                    description='Tâche confidentielle',
                    duree=1,
                ))
            self.db.session.commit()
            return projet.id

    def _audits(self, action, project_id, user_id=None):
        with self.app.app_context():
            requete = AuditLog.query.filter(AuditLog.action == action)
            if user_id is not None:
                requete = requete.filter(AuditLog.user_id == user_id)
            return [
                audit for audit in requete.all()
                if str(project_id) in (audit.details or '')
            ]

    def _audits_ia_intrus(self):
        with self.app.app_context():
            return AuditLog.query.filter(
                AuditLog.user_id == self.intrus_id,
                AuditLog.action.like('IA_%'),
            ).count()

    def _duree(self, project_id, code):
        with self.app.app_context():
            tache = Task.query.filter_by(project_id=project_id, code=code).first()
            return None if tache is None else tache.duree

    @staticmethod
    def _contient(page, *attendus):
        """Un des attendus doit figurer dans la page (tolérance de libellé)."""
        return any(attendu in page for attendu in attendus)

    # ------------------------------------------------------------------
    # 1. Sécurité : authentification, périmètre, non-fuite, audit
    # ------------------------------------------------------------------
    def test_securite_toutes_routes(self):
        project_id = self._creer_projet(avec_secret=True)
        urls = (
            self._url_risques(project_id),
            self._url_actions(project_id),
            self._url_rapport(project_id),
        )

        for url in urls:
            reponse = self.client.get(url)
            self.assertEqual(302, reponse.status_code, msg=url)
            self.assertIn('/login', reponse.headers.get('Location', ''))

        for url in urls:
            reponse = self._get(url, self.intrus_client)
            self.assertEqual(403, reponse.status_code, msg=self._html(reponse)[:1000])
            page = html_module.unescape(self._html(reponse))
            self.assertNotIn(self.code_secret, page, msg=url)
            if reponse.is_json:
                self.assertNotIn(self.code_secret, self._html(reponse))

        avant_audits = self._audits_ia_intrus()
        reponses = (
            self.intrus_client.post(self._url_risques(project_id), data={
                'alerte_id': '1', 'statut': 'confirme', 'commentaire': 'intrus',
            }),
            self.intrus_client.post(self._url_actions(project_id), data={
                'etape': 'executer', 'texte': TEXTE_AUGMENTATION,
                'confirmer': 'CONFIRMER',
            }),
        )
        for reponse in reponses:
            self.assertEqual(403, reponse.status_code, msg=self._html(reponse)[:1000])
            self.assertNotIn(
                self.code_secret, html_module.unescape(self._html(reponse))
            )
        self.assertEqual(avant_audits, self._audits_ia_intrus())
        for action in ('IA_RISQUE_STATUT', 'IA_ACTION_EXECUTEE', 'IA_RAPPORT_INTELLIGENT'):
            self.assertEqual([], self._audits(action, project_id))

    # ------------------------------------------------------------------
    # 2. Analyse des risques
    # ------------------------------------------------------------------
    def test_risques_generation_et_stockage(self):
        project_id = self._creer_projet()

        reponse = self._get(self._url_risques(project_id), self.proprio_client)
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        page = html_module.unescape(self._html(reponse))
        for attendu in (
            'Analyse des risques',
            'indicatif',
            'Règle',
            'Marge nulle sur la tâche A',
            'Éléments justificatifs',
        ):
            self.assertIn(attendu, page, msg=f'{attendu} absent du HTML')
        self.assertTrue(
            'marge totale = 0' in page or 'ES = 0' in page,
            msg='Éléments justificatifs calculés absents',
        )

        with self.app.app_context():
            alertes = AlerteRisque.query.filter_by(project_id=project_id).all()
            self.assertTrue(alertes, msg='Aucune alerte persistée')
            self.assertTrue(any(
                alerte.origine == 'regle' and alerte.statut == 'en_attente'
                for alerte in alertes
            ), msg=[(a.origine, a.statut) for a in alertes])
            self.assertTrue(any(alerte.code_tache == 'A' for alerte in alertes))

    def test_risques_statut_commentaire_audit(self):
        project_id = self._creer_projet()
        self._get(self._url_risques(project_id), self.proprio_client)

        with self.app.app_context():
            alerte = AlerteRisque.query.filter_by(project_id=project_id).first()
            self.assertIsNotNone(alerte)
            alerte_id = alerte.id

        reponse = self._post(
            self._url_risques(project_id),
            {
                'alerte_id': str(alerte_id),
                'statut': 'confirme',
                'commentaire': 'suivi',
            },
            self.proprio_client,
            follow_redirects=True,
        )
        self.assertIn(reponse.status_code, (200, 302), msg=self._html(reponse)[:2000])

        with self.app.app_context():
            alerte = self.db.session.get(AlerteRisque, alerte_id)
            self.assertEqual('confirme', alerte.statut)
            self.assertEqual('suivi', alerte.commentaire)

        audits = self._audits(
            'IA_RISQUE_STATUT', project_id, user_id=self.proprietaire_id
        )
        self.assertTrue(audits, msg='Audit IA_RISQUE_STATUT absent')
        for audit in audits:
            self.assertIn('confirme', audit.details or '')
            self.assertNotIn(
                'suivi', audit.details or '',
                msg='Le commentaire ne doit pas être journalisé',
            )

    # ------------------------------------------------------------------
    # 3. Actions en langage naturel
    # ------------------------------------------------------------------
    def test_action_previsualisation_sans_ecriture(self):
        project_id = self._creer_projet()
        reponse = self._post(
            self._url_actions(project_id),
            {'etape': 'preparer', 'texte': TEXTE_AUGMENTATION},
            self.proprio_client,
        )
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        page = html_module.unescape(self._html(reponse))
        for attendu in (
            'confirmation explicite', 'B', '2', 'name="confirmer"',
        ):
            self.assertIn(attendu, page, msg=f'{attendu} absent du HTML')
        self.assertTrue(
            re.search(r'name="etape"[^>]*value="executer"', page)
            or re.search(r'value="executer"[^>]*name="etape"', page),
            msg='Formulaire d’exécution absent',
        )

        self.assertEqual(3, self._duree(project_id, 'B'))
        self.assertEqual([], self._audits('IA_ACTION_EXECUTEE', project_id))

    def test_action_execution_confirmee_et_recalcul(self):
        project_id = self._creer_projet()
        with self.app.app_context():
            taches = project_to_taches(Project.query.get(project_id))
            taches['B']['duree'] = 5
            duree_attendue = compute(taches).duree_totale
        self.assertEqual(11, duree_attendue)

        reponse = self._post(
            self._url_actions(project_id),
            {
                'etape': 'executer',
                'texte': TEXTE_AUGMENTATION,
                'confirmer': 'CONFIRMER',
            },
            self.proprio_client,
            follow_redirects=True,
        )
        self.assertIn(reponse.status_code, (200, 302), msg=self._html(reponse)[:2000])
        page = html_module.unescape(self._html(reponse))
        self.assertIn('Nouvelle durée calculée', page)
        self.assertIn(str(int(duree_attendue)), page)

        self.assertEqual(5, self._duree(project_id, 'B'))
        with self.app.app_context():
            projet = Project.query.get(project_id)
            self.assertEqual(
                duree_attendue, compute(project_to_taches(projet)).duree_totale
            )

        audits = self._audits('IA_ACTION_EXECUTEE', project_id)
        self.assertTrue(audits, msg='Audit IA_ACTION_EXECUTEE absent')
        self.assertTrue(any(
            'modifier_duree' in (audit.details or '')
            and "'cible': 'B'" in (audit.details or '')
            for audit in audits
        ), msg=[audit.details for audit in audits])

    def test_action_sans_confirmation_refusee(self):
        for confirmer in ('', None):
            with self.subTest(confirmer=confirmer):
                project_id = self._creer_projet()
                donnees = {
                    'etape': 'executer',
                    'texte': TEXTE_AUGMENTATION,
                }
                if confirmer is not None:
                    donnees['confirmer'] = confirmer

                reponse = self._post(
                    self._url_actions(project_id),
                    donnees,
                    self.proprio_client,
                    follow_redirects=True,
                )
                self.assertIn(reponse.status_code, (200, 302))
                page = html_module.unescape(self._html(reponse)).lower()
                self.assertTrue(
                    self._contient(
                        page,
                        'confirmation explicite absente',
                        'confirmation explicite',
                        'aucun changement',
                    ),
                    msg=self._html(reponse)[:2000],
                )
                self.assertEqual(3, self._duree(project_id, 'B'))
                self.assertEqual([], self._audits('IA_ACTION_EXECUTEE', project_id))

    def test_action_destructrice_confirmation_renforcee(self):
        project_id = self._creer_projet()
        reponse = self._post(
            self._url_actions(project_id),
            {'etape': 'preparer', 'texte': TEXTE_SUPPRESSION},
            self.proprio_client,
        )
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        self.assertIn('SUPPRIMER', html_module.unescape(self._html(reponse)))

        reponse = self._post(
            self._url_actions(project_id),
            {
                'etape': 'executer',
                'texte': TEXTE_SUPPRESSION,
                'confirmer': 'CONFIRMER',
            },
            self.proprio_client,
            follow_redirects=True,
        )
        self.assertIn(reponse.status_code, (200, 302))
        self.assertIn('confirmation', html_module.unescape(self._html(reponse)).lower())
        self.assertEqual(3, self._duree(project_id, 'B'))
        self.assertEqual([], self._audits('IA_ACTION_EXECUTEE', project_id))

        reponse = self._post(
            self._url_actions(project_id),
            {
                'etape': 'executer',
                'texte': TEXTE_SUPPRESSION,
                'confirmer': 'SUPPRIMER',
            },
            self.proprio_client,
            follow_redirects=True,
        )
        self.assertIn(reponse.status_code, (200, 302), msg=self._html(reponse)[:2000])
        self.assertIsNone(self._duree(project_id, 'B'))
        audits = self._audits('IA_ACTION_EXECUTEE', project_id)
        self.assertTrue(audits)
        self.assertTrue(any('supprimer_tache' in (a.details or '') for a in audits))

    def test_action_inconnue(self):
        project_id = self._creer_projet()
        reponse = self._post(
            self._url_actions(project_id),
            {'etape': 'preparer', 'texte': 'fait le cafe'},
            self.proprio_client,
        )
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        page = html_module.unescape(self._html(reponse)).lower()
        self.assertTrue(
            self._contient(
                page,
                'non comprise',
                'pas comprise',
                'non reconnue',
                'aucune action',
            ),
            msg=self._html(reponse)[:2000],
        )
        for code, duree in (('A', 2), ('B', 3), ('C', 4)):
            self.assertEqual(duree, self._duree(project_id, code))
        self.assertEqual([], self._audits('IA_ACTION_EXECUTEE', project_id))

    # ------------------------------------------------------------------
    # 4. Rapport intelligent et liens du tableau de bord
    # ------------------------------------------------------------------
    def test_rapport_intelligent(self):
        project_id = self._creer_projet()
        avant = len(self._audits('IA_RAPPORT_INTELLIGENT', project_id))

        reponse = self._get(self._url_rapport(project_id), self.proprio_client)
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        page = html_module.unescape(self._html(reponse))
        page_minuscule = page.lower()
        for attendu in (
            'Synthèse intelligente',
            'moteur PERT/CPM',
            '9 jours',
            'Hypothèses',
            'Chemin',
            'Marges',
            'Risques',
            'Recommandations',
        ):
            self.assertIn(
                attendu.lower(), page_minuscule,
                msg=f'{attendu} absent du HTML',
            )

        with self.app.test_request_context():
            url_pdf = url_for('projects.rapport_pdf', project_id=project_id)
        self.assertIn(url_pdf, page, msg='Lien vers le rapport PDF absent')

        apres = self._audits('IA_RAPPORT_INTELLIGENT', project_id)
        self.assertEqual(avant + 1, len(apres))

    def test_liens_dans_detail(self):
        project_id = self._creer_projet()
        reponse = self._get(f'/projects/{project_id}', self.proprio_client)
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:2000])
        page = html_module.unescape(self._html(reponse))
        for libelle in (
            'Analyse des risques',
            'langage naturel',
            'Rapport intelligent',
        ):
            self.assertIn(libelle, page, msg=f'Lien « {libelle} » absent du détail')


if __name__ == '__main__':
    unittest.main()
