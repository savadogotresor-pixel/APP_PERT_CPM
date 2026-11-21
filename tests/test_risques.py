"""Tests unitaires de l'analyse assistée des risques (IA-RISK-01 à IA-RISK-05)."""

import copy
import unittest
from unittest.mock import patch

from logic.data import taches as TACHES_DEMO
from logic.pert import compute
from models import AlerteRisque
from services import ia as service_ia
from services import risques

CLES_ALERTE = {
    'code_tache', 'titre', 'niveau', 'justification', 'origine',
    'recommandation', 'appreciation_ia', 'preuve', 'elements_justificatifs',
}
MENTION_INDICATIVE = (
    "Niveau indicatif : aucune histoire statistique validée, "
    "pas de prédiction de date d'achèvement."
)


def t(duree, *depend_de):
    """Raccourci pour construire une tâche au format logic.pert."""
    return {"duree": duree, "depend_de": list(depend_de), "description": ""}


class AnalyseRisquesTestCase(unittest.TestCase):
    """Règles déterministes, garde-fous IA et contrat des alertes."""

    def setUp(self):
        resultat = compute(TACHES_DEMO)
        self.taches = copy.deepcopy(TACHES_DEMO)
        self.resultats = copy.deepcopy(resultat.resultats)
        self.duree = resultat.duree_totale
        self.chemins = copy.deepcopy(resultat.chemins_critiques)
        self.successeurs = copy.deepcopy(resultat.successeurs)

    def analyser(self, **kwargs):
        return risques.analyser_risques(
            self.taches, self.resultats, self.duree, self.chemins,
            self.successeurs, **kwargs
        )

    def test_demo_12_taches_marges_nulles_et_bloquante(self):
        alertes = self.analyser(avec_ia=False)

        nulles = {
            a['code_tache'] for a in alertes
            if a['titre'].startswith('Marge nulle sur la tâche ')
        }
        self.assertEqual({'A', 'B', 'G', 'H', 'K', 'L'}, nulles)
        bloquantes = [a for a in alertes if a['titre'].startswith('Tâche bloquante')]
        self.assertTrue(bloquantes)
        self.assertIn('A', {a['code_tache'] for a in bloquantes})
        convergences = [
            a for a in alertes if a['titre'].startswith('Convergence de dépendances')
        ]
        self.assertIn('H', {a['code_tache'] for a in convergences})

    def test_marge_faible_quand_marge_totale_vaut_un(self):
        taches = {"A": t(1), "B": t(1, "A"), "C": t(3)}
        resultat = compute(taches)
        alertes = risques.analyser_risques(
            taches, resultat.resultats, resultat.duree_totale,
            resultat.chemins_critiques, resultat.successeurs, avec_ia=False,
        )

        faibles = [a for a in alertes if a['titre'].startswith('Marge faible')]
        self.assertEqual({'A', 'B'}, {a['code_tache'] for a in faibles})
        b = next(a for a in faibles if a['code_tache'] == 'B')
        self.assertEqual(1, b['preuve']['marge'])
        self.assertEqual(30, self.duree)  # la démo reste intacte
        self.assertIn("marge totale = 1 jour", b['elements_justificatifs'])

    def test_convergence_avec_trois_predecesseurs(self):
        taches = {
            "A": t(1), "B": t(1), "C": t(1),
            "D": t(1, "A", "B", "C"),
        }
        resultat = compute(taches)
        alertes = risques.analyser_risques(
            taches, resultat.resultats, resultat.duree_totale,
            resultat.chemins_critiques, resultat.successeurs, avec_ia=False,
        )

        convergence = next(
            a for a in alertes if a['titre'] == 'Convergence de dépendances sur D'
        )
        self.assertEqual({'predecesseurs': ['A', 'B', 'C']}, convergence['preuve'])
        self.assertIn("3 prédécesseurs : A, B, C",
                      convergence['elements_justificatifs'])

    def test_contrat_complet_et_niveau_indicatif(self):
        alertes = self.analyser(avec_ia=False)
        self.assertTrue(alertes)

        for alerte in alertes:
            self.assertEqual(CLES_ALERTE, set(alerte))
            self.assertEqual('indicatif', alerte['niveau'])
            self.assertIn(MENTION_INDICATIVE, alerte['justification'])
            self.assertEqual('regle', alerte['origine'])
            self.assertIsNone(alerte['appreciation_ia'])
            self.assertTrue(alerte['recommandation'])
            self.assertIsInstance(alerte['preuve'], dict)
            self.assertTrue(alerte['elements_justificatifs'])
            self.assertTrue(all(isinstance(e, str) for e in
                                alerte['elements_justificatifs']))

        globales = [a for a in alertes if a['titre'] == 'Date de début non renseignée']
        self.assertEqual(1, len(globales))
        self.assertIsNone(globales[0]['code_tache'])
        self.assertEqual(['aucune date de début fournie'],
                         globales[0]['elements_justificatifs'])

    def test_elements_justificatifs_contiennent_les_valeurs_calculees(self):
        alertes = self.analyser(avec_ia=False)

        a = next(x for x in alertes if x['titre'] == 'Marge nulle sur la tâche A')
        self.assertEqual({'marge': 0, 'ES': 0, 'EF': 3, 'LS': 0, 'LF': 3},
                         a['preuve'])
        for element in (
            "marge totale = 0 jour", "ES = 0", "EF = 3", "LS = 0", "LF = 3"
        ):
            self.assertIn(element, a['elements_justificatifs'])

        bloquante = next(
            x for x in alertes if x['titre'] == 'Tâche bloquante : A'
        )
        self.assertEqual({'successeurs': ['B', 'C', 'D']}, bloquante['preuve'])
        self.assertIn("3 successeurs : B, C, D",
                      bloquante['elements_justificatifs'])

    def test_sans_ia_aucun_appel_au_service(self):
        with patch.object(risques.service_ia, 'est_disponible') as disponible:
            alertes = self.analyser(avec_ia=False)
        disponible.assert_not_called()
        self.assertTrue(all(a['origine'] == 'regle' and
                            a['appreciation_ia'] is None for a in alertes))

        with patch.object(risques.service_ia, 'est_disponible',
                          return_value=False) as disponible:
            alertes = self.analyser()
        disponible.assert_called_once_with()
        self.assertTrue(all(a['origine'] == 'regle' and
                            a['appreciation_ia'] is None for a in alertes))

    def test_avec_ia_appreciation_seulement(self):
        references = self.analyser(avec_ia=False)
        questions = []

        def faux_repondre(question, taches, resultats, duree_totale,
                          chemins_critiques, historique, successeurs):
            questions.append(question)
            return {'texte': 'Risque à surveiller, les marges calculées sont faibles.',
                    'source': 'ia'}

        with patch.object(risques.service_ia, 'est_disponible', return_value=True), \
                patch.object(risques.service_ia, 'repondre',
                             side_effect=faux_repondre), \
                patch.object(risques.service_ia, 'verifier_nombres',
                             return_value=True) as verifier:
            alertes = self.analyser(avec_ia=True)

        self.assertEqual(len(references), len(alertes))
        self.assertEqual(len(alertes), len(questions))
        self.assertTrue(all(q.startswith('Commenter ce risque :') for q in questions))
        self.assertEqual(len(alertes), verifier.call_count)
        for avant, apres in zip(references, alertes):
            self.assertEqual('regle+ia', apres['origine'])
            self.assertTrue(apres['appreciation_ia'])
            self.assertEqual(avant['recommandation'], apres['recommandation'])
            self.assertEqual(avant['justification'], apres['justification'])
            self.assertEqual(avant['preuve'], apres['preuve'])
            self.assertEqual(avant['niveau'], apres['niveau'])

    def test_ia_chiffre_non_autorise_rejete_sans_exception(self):
        with patch.object(risques.service_ia, 'est_disponible', return_value=True), \
                patch.object(risques.service_ia, 'repondre',
                             return_value={'texte': 'Retard de 42 jours.',
                                           'source': 'ia'}), \
                patch.object(risques.service_ia, 'verifier_nombres',
                             return_value=False):
            alertes = self.analyser(avec_ia=True)

        self.assertTrue(alertes)
        self.assertTrue(all(a['appreciation_ia'] is None and
                            a['origine'] == 'regle' for a in alertes))

    def test_ia_qui_leve_exception_reste_sans_effet(self):
        with patch.object(risques.service_ia, 'est_disponible', return_value=True), \
                patch.object(risques.service_ia, 'repondre',
                             side_effect=RuntimeError('fournisseur indisponible')):
            alertes = self.analyser(avec_ia=True)
        self.assertTrue(alertes)
        self.assertTrue(all(a['appreciation_ia'] is None and
                            a['origine'] == 'regle' for a in alertes))

        with patch.object(risques.service_ia, 'est_disponible',
                          side_effect=OSError('panne')):
            alertes = self.analyser(avec_ia=True)
        self.assertTrue(all(a['appreciation_ia'] is None and
                            a['origine'] == 'regle' for a in alertes))

    def test_repli_deterministe_du_service_n_est_pas_une_appreciation_ia(self):
        with patch.object(risques.service_ia, 'est_disponible', return_value=True), \
                patch.object(risques.service_ia, 'repondre',
                             return_value={'texte': 'Réponse calculée',
                                           'source': 'moteur'}):
            alertes = self.analyser(avec_ia=True)
        self.assertTrue(all(a['appreciation_ia'] is None and
                            a['origine'] == 'regle' for a in alertes))

    def test_entrees_jamais_modifiees(self):
        avant = (
            copy.deepcopy(self.taches), copy.deepcopy(self.resultats),
            copy.deepcopy(self.duree), copy.deepcopy(self.chemins),
            copy.deepcopy(self.successeurs),
        )
        with patch.object(risques.service_ia, 'est_disponible', return_value=True), \
                patch.object(risques.service_ia, 'repondre',
                             return_value={'texte': 'Aucun chiffre nouveau.',
                                           'source': 'ia'}):
            self.analyser(avec_ia=True)
        self.assertEqual(avant, (
            self.taches, self.resultats, self.duree, self.chemins,
            self.successeurs,
        ))
        self.assertEqual(TACHES_DEMO, self.taches)

    def test_projet_vide_ne_produit_aucune_alerte(self):
        with patch.object(risques.service_ia, 'est_disponible',
                          return_value=True) as disponible:
            self.assertEqual(
                [], risques.analyser_risques({}, {}, 0, [], {}, avec_ia=True)
            )
        disponible.assert_not_called()
        self.assertEqual([], risques.analyser_risques(
            {}, {}, None, [], None, avec_ia=False
        ))

    def test_aucune_alerte_date_quand_une_date_est_fournie(self):
        taches = copy.deepcopy(self.taches)
        taches['A']['date_debut'] = '2026-01-05'
        alertes = risques.analyser_risques(
            taches, self.resultats, self.duree, self.chemins,
            self.successeurs, avec_ia=False,
        )
        self.assertFalse(any(a['titre'] == 'Date de début non renseignée'
                             for a in alertes))
        self.assertEqual(TACHES_DEMO, self.taches)

    def test_modele_alerte_risque_importable(self):
        self.assertEqual('alerte_risque', AlerteRisque.__tablename__)
        colonnes = set(AlerteRisque.__table__.columns.keys())
        self.assertEqual({
            'id', 'project_id', 'code_tache', 'titre', 'niveau',
            'justification', 'recommandation', 'origine', 'appreciation_ia',
            'statut', 'commentaire', 'created_at',
        }, colonnes)
        table = AlerteRisque.__table__
        self.assertFalse(table.columns['titre'].nullable)
        self.assertFalse(table.columns['project_id'].nullable)
        self.assertEqual('indicatif', AlerteRisque.niveau.default.arg)
        self.assertEqual('regle', AlerteRisque.origine.default.arg)
        self.assertEqual('en_attente', AlerteRisque.statut.default.arg)
        self.assertTrue(hasattr(AlerteRisque, 'projet'))
        self.assertTrue(hasattr(service_ia, 'repondre'))


if __name__ == '__main__':
    unittest.main()
