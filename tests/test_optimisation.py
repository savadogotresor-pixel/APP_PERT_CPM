"""Tests unitaires du service de simulation d'optimisation (OPT-01..05)."""

import copy
import unittest

from flask import Flask

from config import TestConfig
from extensions import db
from logic.pert import compute
from models import Project, Scenario, User, init_db
from services.optimisation import simuler


def t(duree, *depend_de):
    """Raccourci pour construire une tâche au format logic.pert."""
    return {"duree": duree, "depend_de": list(depend_de)}


class SimulationOptimisationTestCase(unittest.TestCase):
    """Le service recalcule les deux plannings via le moteur canonique."""

    def test_reduction_sur_chemin_critique(self):
        taches = {"A": t(2), "B": t(3, "A"), "C": t(4, "B")}
        avant = copy.deepcopy(taches)

        resultat = simuler(taches, {"B": 1})

        self.assertEqual(9, resultat["initial"]["duree_totale"])
        self.assertEqual(7, resultat["simule"]["duree_totale"])
        self.assertEqual(2, resultat["gains"])
        self.assertEqual([["A", "B", "C"]], resultat["initial"]["chemins_critiques"])
        self.assertEqual([["A", "B", "C"]], resultat["simule"]["chemins_critiques"])
        self.assertEqual(
            [{"code": "B", "avant": 3, "apres": 1}], resultat["modifications"]
        )
        self.assertEqual(["A", "B", "C"], resultat["taches_accelerables"])
        self.assertEqual(avant, taches)
        self.assertEqual(
            {"duree_totale", "chemins_critiques", "resultats"},
            set(resultat["initial"]),
        )
        self.assertEqual(
            {"duree_totale", "chemins_critiques", "resultats"},
            set(resultat["simule"]),
        )

        # Les chiffres rendus sont exactement ceux du moteur, pas des estimations.
        self.assertEqual(compute(taches).resultats, resultat["initial"]["resultats"])
        simule_attendu = copy.deepcopy(taches)
        simule_attendu["B"]["duree"] = 1
        self.assertEqual(compute(simule_attendu).resultats,
                         resultat["simule"]["resultats"])

    def test_reduction_tache_non_critique_sans_gain(self):
        taches = {
            "A": t(2),
            "B": t(3, "A"),
            "C": t(1, "A"),
            "D": t(1, "B", "C"),
        }

        resultat = simuler(taches, {"C": 0})

        self.assertEqual(6, resultat["initial"]["duree_totale"])
        self.assertEqual(6, resultat["simule"]["duree_totale"])
        self.assertEqual(0, resultat["gains"])
        self.assertEqual(["A", "B", "D"], resultat["taches_accelerables"])
        self.assertFalse(resultat["initial"]["resultats"]["C"]["critique"])
        self.assertFalse(resultat["simule"]["resultats"]["C"]["critique"])

    def test_deux_chemins_critiques_reduction_insuffisante(self):
        taches = {"A": t(2), "B": t(3, "A"), "C": t(3, "A"), "D": t(2, "B", "C")}

        resultat = simuler(taches, {"B": 2})

        self.assertEqual(7, resultat["initial"]["duree_totale"])
        self.assertEqual(
            [["A", "B", "D"], ["A", "C", "D"]],
            resultat["initial"]["chemins_critiques"],
        )
        self.assertEqual(7, resultat["simule"]["duree_totale"])
        self.assertEqual(0, resultat["gains"])
        self.assertEqual([["A", "C", "D"]], resultat["simule"]["chemins_critiques"])
        self.assertFalse(resultat["simule"]["resultats"]["B"]["critique"])
        self.assertEqual(["A", "B", "C", "D"], resultat["taches_accelerables"])

    def test_reduction_a_zero(self):
        taches = {"A": t(2), "B": t(3, "A"), "C": t(4, "B")}

        resultat = simuler(taches, {"B": 0})

        self.assertEqual(9, resultat["initial"]["duree_totale"])
        self.assertEqual(6, resultat["simule"]["duree_totale"])
        self.assertEqual(3, resultat["gains"])
        self.assertEqual(0, resultat["modifications"][0]["apres"])
        self.assertEqual(3, resultat["modifications"][0]["avant"])

    def test_plusieurs_reductions(self):
        taches = {"A": t(2), "B": t(3, "A"), "C": t(4, "B")}

        resultat = simuler(taches, {"B": 1, "A": 1})

        self.assertEqual(6, resultat["simule"]["duree_totale"])
        self.assertEqual(3, resultat["gains"])
        self.assertEqual(
            [{"code": "B", "avant": 3, "apres": 1},
             {"code": "A", "avant": 2, "apres": 1}],
            resultat["modifications"],
        )

    def test_erreurs_de_demande(self):
        taches = {"A": t(2), "B": t(3, "A"), "C": t(4, "B")}

        cas = (
            ("Z", 1, "inconnue"),
            ("B", -1, "négative"),
            ("B", 1.5, "entier"),
            ("B", True, "entier"),
            ("B", "1", "entier"),
        )
        for code, duree, attendu in cas:
            with self.subTest(code=code, duree=duree):
                with self.assertRaises(ValueError) as contexte:
                    simuler(taches, {code: duree})
                self.assertIn(attendu, str(contexte.exception))

        with self.assertRaises(ValueError) as contexte:
            simuler(taches, {})
        self.assertIn("Aucune réduction", str(contexte.exception))

    def test_erreur_graphe_invalide_convertie(self):
        cyclique = {"A": t(1, "B"), "B": t(1, "A")}

        with self.assertRaises(ValueError) as contexte:
            simuler(cyclique, {"A": 0})
        self.assertIn("Graphe de tâches invalide", str(contexte.exception))
        self.assertIn("circulaire", str(contexte.exception))

    def test_planning_recu_nest_pas_muté(self):
        taches = {
            "A": {"description": "Analyse", "duree": 2, "depend_de": []},
            "B": {"description": "Conception", "duree": 3, "depend_de": ["A"]},
        }
        avant = copy.deepcopy(taches)

        resultat = simuler(taches, {"B": 1})

        self.assertEqual(avant, taches)
        self.assertEqual(avant["B"]["duree"], taches["B"]["duree"])
        self.assertEqual(1, resultat["modifications"][0]["apres"])

        # Les résultats rendus ne partagent pas leurs dictionnaires avec l'entrée.
        resultat["simule"]["resultats"]["A"]["ES"] = 99
        self.assertEqual(avant, taches)

    def test_hypotheses_et_limites_explicites(self):
        taches = {"A": t(2), "B": t(3, "A"), "C": t(4, "B")}

        resultat = simuler(taches, {"B": 1})

        hypotheses = resultat["hypotheses"]
        for attendu in ("Simulation déterministe", "B", "déterministe",
                        "planning de référence", "moteur PERT/CPM", "gain",
                        "critiques"):
            with self.subTest(hypothese=attendu):
                self.assertIn(attendu, hypotheses)

        limites = resultat["limites"]
        for attendu in ("coût", "ressources", "FS", "réalisable",
                        "planning de référence", "inchangé", "confirmation"):
            with self.subTest(limite=attendu):
                self.assertIn(attendu, limites)


class ScenarioModeleTestCase(unittest.TestCase):
    """Le nouveau modèle est persisté sans casser les relations existantes."""

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.from_object(TestConfig)
        db.init_app(self.app)
        self.app_context = self.app.app_context()
        self.app_context.push()
        init_db(self.app)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_creation_scenario(self):
        utilisateur = User(username="alice", email="alice@example.com")
        utilisateur.set_password("secret")
        projet = Project(user_id=None, name="Projet simulation")
        projet.owner = utilisateur
        db.session.add_all([utilisateur, projet])
        db.session.commit()

        scenario = Scenario(
            project_id=projet.id,
            nom="Accélérer B",
            hypotheses='{"B": 1}',
            resultats='{"gains": 2}',
        )
        db.session.add(scenario)
        db.session.commit()

        self.assertEqual(1, Scenario.query.count())
        self.assertEqual("Accélérer B", scenario.nom)
        self.assertEqual(projet.id, scenario.project_id)
        self.assertEqual([scenario], projet.scenarios)
        self.assertEqual(projet, scenario.projet)


if __name__ == "__main__":
    unittest.main()
