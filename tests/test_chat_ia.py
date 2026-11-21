"""Tests de l'espace de discussion libre avec l'IA maison.

Deux niveaux : le moteur pur (intentions, sources vérifiables, bornes) et les
routes Flask (droits d'accès, contrat JSON, audit sans contenu libre, rendu de
la page). Aucun appel réseau n'est possible : le service n'a pas de client HTTP.
"""

import os
import re
import unittest
import uuid

from sqlalchemy import create_engine

from logic.pert import compute
from models import AuditLog, Project, User
from services.chat_ia import (
    HISTORIQUE_MAX,
    INTENTIONS,
    MESSAGE_MAX,
    _historique_borne,
    repondre,
)

CLES_CONTRAT = {'texte', 'intention', 'sources'}

GRAPHE = {
    'A': {'description': 'Analyse', 'duree': 2, 'depend_de': []},
    'B': {'description': 'Conception', 'duree': 3, 'depend_de': ['A']},
    'C': {'description': 'Documentation', 'duree': 1, 'depend_de': ['A']},
    'D': {'description': 'Intégration', 'duree': 2, 'depend_de': ['B', 'C']},
    'E': {'description': 'Jalon final', 'duree': 0, 'depend_de': ['D']},
}

PROJET = {'name': 'Demo', 'description': 'Planning de demonstration'}

# Une question par intention, avec des formulations naturelles différentes.
QUESTIONS = (
    ('salutation', 'Bonjour !'),
    ('chemin_critique', 'Quel est le chemin critique de ce planning ?'),
    ('duree_totale', 'Combien de temps dure le projet au total ?'),
    ('marges', 'Quelles tâches ont le plus de marge ?'),
    ('tache_precise', 'La tâche B est-elle critique ?'),
    ('optimisation', 'Comment raccourcir le projet ?'),
    ('dependances', 'Quelles dépendances bloquent les tâches ?'),
    ('risques', 'Y a-t-il un risque de retard ?'),
    ('explication_pert', 'Explique-moi la méthode PERT, s il te plaît.'),
    ('aide', 'Que peux-tu faire ?'),
    ('projet', 'Parle-moi du projet, son nom et sa description.'),
    ('scenario_date', 'Je veux livrer avant le 20 décembre, est-ce possible ?'),
    ('remerciement', 'Merci beaucoup !'),
    ('hors_cadre', 'Quelle est la météo à Paris demain ?'),
    ('tache_inconnue', 'Parle-moi de la tâche ZZZ.'),
)


class ChatIAMoteurTestCase(unittest.TestCase):
    """Unités pures du moteur conversationnel maison."""

    @staticmethod
    def _nombres_autorises(graphe, resultat):
        valeurs = [
            resultat.duree_totale,
            len(graphe),
            len(resultat.chemins_critiques),
            sum(len(succs) for succs in resultat.successeurs.values()),
        ]
        for code, infos in graphe.items():
            valeurs.append(infos.get('duree', 0))
            r = resultat.resultats.get(code, {})
            valeurs.extend(r.get(cle, 0) for cle in (
                'ES', 'EF', 'LS', 'LF', 'marge', 'marge_totale', 'marge_libre',
            ))
            valeurs.append(1 if r.get('critique') else 0)
        return {str(int(v)) for v in valeurs
                if isinstance(v, (int, float)) and float(v).is_integer()}

    def test_contrat_et_toutes_les_intentions(self):
        resultat = compute(GRAPHE)
        detected = set()
        self.assertLessEqual({nom for nom, _ in QUESTIONS}, set(INTENTIONS))
        for intention, question in QUESTIONS:
            with self.subTest(intention=intention):
                reponse = repondre(question, GRAPHE, resultat, projet=PROJET)
                self.assertEqual(CLES_CONTRAT, set(reponse))
                self.assertIsInstance(reponse['texte'], str)
                self.assertTrue(reponse['texte'].strip())
                self.assertIsInstance(reponse['sources'], list)
                self.assertEqual(intention, reponse['intention'])
                detected.add(reponse['intention'])
        self.assertEqual({nom for nom, _ in QUESTIONS}, detected)
        self.assertGreaterEqual(len(INTENTIONS), 10)

    def test_aucun_chiffre_hors_du_moteur(self):
        resultat = compute(GRAPHE)
        autorises = self._nombres_autorises(GRAPHE, resultat)
        for intention, question in QUESTIONS:
            if intention == 'sans_taches':
                continue
            with self.subTest(intention=intention):
                reponse = repondre(question, GRAPHE, resultat, projet=PROJET)
                for nombre in re.findall(r'\d+', reponse['texte']):
                    self.assertIn(
                        nombre, autorises,
                        msg=f'{intention} : {nombre!r} ne vient pas du moteur',
                    )
                for source in reponse['sources']:
                    for nombre in re.findall(r'\d+', source):
                        self.assertIn(nombre, autorises, msg=source)

    def test_sources_reelles_pour_duree_et_marge(self):
        resultat = compute(GRAPHE)
        duree = repondre('Quelle est la durée totale ?', GRAPHE, resultat)
        self.assertEqual('duree_totale', duree['intention'])
        self.assertIn('durée totale = 7 j', duree['sources'])
        self.assertIn('7 jours', duree['texte'])

        marge = repondre('Quelles tâches ont le plus de marge ?', GRAPHE, resultat)
        self.assertEqual('marges', marge['intention'])
        self.assertTrue(
            any(source.startswith('marge totale de ') for source in marge['sources'])
        )
        self.assertEqual(2, resultat.resultats['C']['marge_totale'])
        self.assertTrue(any('= 2 j' in source for source in marge['sources']))

    def test_projet_sans_taches_sans_chiffre(self):
        for intention, question in QUESTIONS:
            with self.subTest(question=question):
                reponse = repondre(question, {}, compute({}))
                self.assertIn(reponse['intention'], ('sans_taches', intention))
                self.assertIn('ajoutez', reponse['texte'].lower())
                self.assertEqual([], re.findall(r'\d+', reponse['texte']))
                self.assertEqual([], reponse['sources'])

    def test_historique_borne_et_reprise(self):
        longs = [
            {'role': 'user', 'texte': 'A' * (MESSAGE_MAX + 500)}
            for _ in range(HISTORIQUE_MAX + 12)
        ]
        messages = _historique_borne(longs)
        self.assertEqual(HISTORIQUE_MAX, len(messages))
        self.assertTrue(all(len(m['texte']) <= MESSAGE_MAX for m in messages))
        self.assertTrue(all(m['role'] == 'user' for m in messages))
        self.assertEqual([], _historique_borne([{'role': 'systeme', 'texte': 'x'}]))

        resultat = compute(GRAPHE)
        historique = [{'role': 'user', 'texte': 'Quel est le chemin critique ?'}]
        reprise = repondre('et alors ?', GRAPHE, resultat, historique=historique)
        self.assertEqual('chemin_critique', reprise['intention'])

    def test_optimisation_reutilise_le_conseiller_sans_gain_promis(self):
        from services.conseiller import generer_conseils

        resultat = compute(GRAPHE)
        reponse = repondre('Comment raccourcir le projet ?', GRAPHE, resultat)
        self.assertEqual('optimisation', reponse['intention'])
        for conseil in generer_conseils(resultat, GRAPHE):
            self.assertIn(conseil['titre'], reponse['texte'])
        self.assertTrue(
            any('conseiller' in s.lower() for s in reponse['sources'])
        )
        self.assertNotIn('%', reponse['texte'])

    def test_aucune_dependance_reseau(self):
        import services.chat_ia as module

        self.assertTrue(callable(module.repondre))
        for nom in ('requests', 'urllib', 'openai', 'IADisponibiliteError', 'configuration'):
            self.assertFalse(hasattr(module, nom), msg=f'{nom} ne doit pas etre importe')
        with open(module.__file__, encoding='utf-8') as fichier:
            source = fichier.read()
        for interdit in ('IA_API_KEY', 'urlopen', 'api.openai.com', 'http://'):
            self.assertNotIn(interdit, source)


class ChatIARoutesTestCase(unittest.TestCase):
    """Parcours HTTP du chat : droits, contrat JSON, audit et rendu."""

    @classmethod
    def setUpClass(cls):
        os.environ['APP_SETTINGS'] = 'config.TestConfig'

        from app import app, db
        from routes_chat_ia import chat_ia_bp

        cls.app = app
        db = app.extensions.get('sqlalchemy', db)
        cls.db = db
        app.config.from_object('config.TestConfig')
        app.config.update(
            TESTING=True, WTF_CSRF_ENABLED=False, SECRET_KEY='test-chat-ia'
        )
        if 'chat_ia' not in app.blueprints:
            app.register_blueprint(chat_ia_bp)
        # L'accueil historique utilise url_for('catalogue.index') : on rend le
        # test autonome si le coordonnateur n'a pas encore branché ce blueprint.
        try:
            from routes_catalogue import catalogue_bp
        except ImportError:
            catalogue_bp = None
        if catalogue_bp is not None and 'catalogue' not in app.blueprints:
            app.register_blueprint(catalogue_bp)

        with app.app_context():
            db.session.remove()
            moteur = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
            moteurs = db._app_engines.setdefault(app, {})
            ancien = moteurs.get(None)
            if ancien is not None:
                ancien.dispose()
            moteurs[None] = moteur
            db.create_all()

        cls.client = app.test_client()
        cls._utilisateurs = []

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            for user_id in cls._utilisateurs:
                utilisateur = cls.db.session.get(User, user_id)
                if utilisateur is not None:
                    cls.db.session.delete(utilisateur)
            cls.db.session.commit()
            cls.db.session.remove()
        os.environ.pop('APP_SETTINGS', None)

    def setUp(self):
        self.client = self.app.test_client()

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

    def _nouveau_projet(self, nom=None):
        nom = nom or f'Projet chat {uuid.uuid4().hex[:8]}'
        reponse = self.client.post('/projects/new', data={
            'name': nom,
            'description': 'Projet de test du chat IA',
            'date_reference': '2026-11-21',
        })
        self.assertIn(reponse.status_code, (200, 302), msg=self._html(reponse)[:1000])
        with self.app.app_context():
            projet = Project.query.filter_by(name=nom).first()
        self.assertIsNotNone(projet, msg=self._html(reponse)[:1000])
        return projet.id

    def _nouvelle_tache(self, projet_id, code, duree, predecesseurs=''):
        reponse = self.client.post(f'/projects/{projet_id}/tasks/new', data={
            'code': code,
            'description': f'Tâche {code}',
            'duree': str(duree),
            'predecesseurs': predecesseurs,
        })
        self.assertIn(reponse.status_code, (200, 302), msg=self._html(reponse)[:1000])

    def _envoyer(self, projet_id, message, json=True, client=None):
        client = client or self.client
        if json:
            return client.post('/ia/discussion/message', json={
                'message': message, 'projet_id': projet_id,
            })
        return client.post('/ia/discussion/message', data={
            'message': message, 'projet_id': str(projet_id),
        })

    def test_endpoint_et_inscription_du_blueprint(self):
        self.assertIn('chat_ia', self.app.blueprints)
        with self.app.test_request_context():
            from flask import url_for
            self.assertEqual('/ia/discussion', url_for('chat_ia.discussion'))
            self.assertEqual('/ia/discussion/message', url_for('chat_ia.message'))
            self.assertEqual('/ia/discussion/historique', url_for('chat_ia.historique'))

    def test_page_discussion_du_proprietaire(self):
        self._inscrire('chat_page')
        projet_id = self._nouveau_projet('Projet chat page')
        self._nouvelle_tache(projet_id, 'A', 2)

        reponse = self.client.get(f'/ia/discussion?projet={projet_id}')
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1500])
        html = self._html(reponse)
        self.assertIn('name="csrf-token"', html)
        self.assertIn('id="projet-select"', html)
        self.assertIn('id="message"', html)
        self.assertIn('maxlength="1000"', html)
        self.assertIn('Quel est le chemin critique ?', html)
        self.assertIn('Comment raccourcir le projet ?', html)
        self.assertIn('Quelles tâches ont le plus de marge ?', html)
        self.assertIn('X-CSRFToken', html)
        self.assertIn('fetch(', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('Assistant IA', html)
        self.assertNotIn('Saisie des tâches', html)
        # design.css doit rester la dernière feuille de style chargée.
        feuilles = re.findall(r'<link[^>]+rel="stylesheet"[^>]*>', html)
        self.assertTrue(feuilles)
        self.assertIn('design.css', feuilles[-1])

    def test_page_sans_projet_est_lisible(self):
        self._inscrire('chat_vide')
        reponse = self.client.get('/ia/discussion')
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1500])
        html = self._html(reponse)
        self.assertIn('Aucun projet accessible', html)
        self.assertIn('disabled', html)

    def test_acces_anonyme_et_intrus(self):
        self._inscrire('chat_proprietaire')
        projet_id = self._nouveau_projet('Projet chat prive')
        self._nouvelle_tache(projet_id, 'SECRET_CHAT', 4)

        anonyme = self.app.test_client()
        for url in ('/ia/discussion', f'/ia/discussion?projet={projet_id}',
                    '/ia/discussion/historique?projet=1'):
            reponse = anonyme.get(url)
            self.assertEqual(302, reponse.status_code)
            self.assertIn('/login', reponse.headers.get('Location', ''))

        intrus = self.app.test_client()
        self._inscrire('chat_intrus', client=intrus)
        self.assertEqual(403, intrus.get(f'/ia/discussion?projet={projet_id}').status_code)
        self.assertEqual(403, self._envoyer(projet_id, 'bonjour', client=intrus).status_code)
        self.assertEqual(
            403,
            intrus.get(f'/ia/discussion/historique?projet={projet_id}').status_code,
        )

    def test_reponse_ancrée_sur_le_planning(self):
        self._inscrire('chat_duree')
        projet_id = self._nouveau_projet('Projet chat duree')
        self._nouvelle_tache(projet_id, 'A', 2)
        self._nouvelle_tache(projet_id, 'B', 3, 'A')

        reponse = self._envoyer(projet_id, 'Quelle est la durée totale du projet ?')
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1000])
        donnees = reponse.get_json(silent=True)
        self.assertIsInstance(donnees, dict)
        self.assertEqual({'texte', 'intention', 'sources', 'projet_id'}, set(donnees))
        self.assertEqual(projet_id, donnees['projet_id'])
        self.assertEqual('duree_totale', donnees['intention'])
        self.assertIn('5 jours', donnees['texte'])
        self.assertIn('durée totale = 5 j', donnees['sources'])

        formulaire = self._envoyer(projet_id, 'Quel est le chemin critique ?', json=False)
        self.assertEqual(200, formulaire.status_code)
        self.assertEqual('chemin_critique', formulaire.get_json()['intention'])

    def test_messages_invalides_et_borne_longueur(self):
        self._inscrire('chat_invalide')
        projet_id = self._nouveau_projet('Projet chat invalide')
        self._nouvelle_tache(projet_id, 'A', 2)

        self.assertEqual(400, self._envoyer(projet_id, '   ').status_code)
        vide_form = self.client.post('/ia/discussion/message', data={
            'projet_id': str(projet_id),
        })
        self.assertEqual(400, vide_form.status_code)
        self.assertEqual(400, self._envoyer(999999, 'bonjour').status_code)
        self.assertEqual(400, self.client.post(
            '/ia/discussion/message', json={'message': 'bonjour', 'projet_id': 'abc'}
        ).status_code)
        self.assertEqual(200, self._envoyer(projet_id, 'bonjour').status_code)

        long_message = self._envoyer(projet_id, 'marge ' * 400)
        self.assertEqual(200, long_message.status_code)
        with self.client.session_transaction() as session:
            messages = session['chat_ia_historique'][str(projet_id)]
        self.assertTrue(all(len(m['texte']) <= MESSAGE_MAX for m in messages))

    def test_historique_route_et_borne_session(self):
        self._inscrire('chat_historique')
        projet_id = self._nouveau_projet('Projet chat historique')
        self._nouvelle_tache(projet_id, 'A', 2)
        self._nouvelle_tache(projet_id, 'B', 3, 'A')

        self.assertEqual(200, self._envoyer(projet_id, 'Bonjour').status_code)
        self.assertEqual(200, self._envoyer(projet_id, 'Quelle est la durée totale ?').status_code)
        reponse = self.client.get(f'/ia/discussion/historique?projet={projet_id}')
        self.assertEqual(200, reponse.status_code)
        donnees = reponse.get_json(silent=True)
        self.assertEqual(projet_id, donnees['projet_id'])
        self.assertEqual(4, len(donnees['historique']))
        self.assertEqual(400, self.client.get('/ia/discussion/historique').status_code)

        for rang in range(HISTORIQUE_MAX + 6):
            self.assertEqual(
                200, self._envoyer(projet_id, f'Question {rang} ?').status_code
            )
        apres = self.client.get(
            f'/ia/discussion/historique?projet={projet_id}'
        ).get_json()
        self.assertLessEqual(len(apres['historique']), HISTORIQUE_MAX)

    def test_audit_sans_contenu_libre(self):
        self._inscrire('chat_audit')
        projet_id = self._nouveau_projet('Projet chat audit')
        self._nouvelle_tache(projet_id, 'A', 2)
        secret = 'question TRES CONFIDENTIELLE chat audit'

        with self.app.app_context():
            avant = AuditLog.query.filter_by(action='CHAT_IA').count()
        reponse = self._envoyer(projet_id, secret)
        self.assertEqual(200, reponse.status_code)
        with self.app.app_context():
            journaux = AuditLog.query.filter_by(action='CHAT_IA').all()
            details = '\n'.join(j.details or '' for j in journaux[avant:])
        self.assertGreater(len(journaux), avant)
        self.assertNotIn(secret, details)
        self.assertNotIn('CONFIDENTIELLE', details)
        self.assertNotIn(reponse.get_json()['texte'], details)

    def test_lien_assistant_de_la_nav_pointe_vers_la_discussion(self):
        self._inscrire('chat_nav')
        projet_id = self._nouveau_projet('Projet chat nav')
        reponse = self.client.get('/')
        self.assertEqual(200, reponse.status_code, msg=self._html(reponse)[:1000])
        self.assertIn('/ia/discussion', self._html(reponse))
        self.assertIn(f'projet={projet_id}', self._html(reponse))


if __name__ == '__main__':
    unittest.main()
