"""Tests unitaires du service d'optimisation assistée (IA-OPT-01 à IA-OPT-06)."""

import copy
import re
import unittest

from logic.pert import compute
from services.optimisation import simuler
from services.optimisation_assistee import MAX_SCENARIOS, suggerer_scenarios


def t(duree, *depend_de):
    """Raccourci pour construire une tâche au format logic.pert."""
    return {"duree": duree, "depend_de": list(depend_de)}


NOMBRE_RE = re.compile(r"\d+(?:[.,]\d+)?")

CLES_SCENARIO = {
    "titre", "description", "type", "reductions", "dependance_retiree",
    "hypotheses", "limites", "compromis", "resultat",
    "planning_reference_modifie",
}
CLES_SIMULATION = {
    "initial", "simule", "gains", "modifications", "taches_accelerables",
    "hypotheses", "limites",
}
CLES_RESUME = {"duree_totale", "chemins_critiques", "resultats"}


def nombres(texte):
    return set(NOMBRE_RE.findall(str(texte)))


def nombres_resultat(resultat):
    """Tous les nombres calculés contenus dans un résultat de simulation."""
    autorises = set()

    def visiter(valeur):
        if isinstance(valeur, bool) or valeur is None:
            return
        if isinstance(valeur, (int, float)):
            if isinstance(valeur, float) and valeur.is_integer():
                autorises.add(str(int(valeur)))
            else:
                autorises.add(str(valeur).replace(".", ","))
            return
        if isinstance(valeur, str):
            autorises.update(nombres(valeur))
            return
        if isinstance(valeur, dict):
            for cle, sous_valeur in valeur.items():
                visiter(cle)
                visiter(sous_valeur)
            return
        if isinstance(valeur, (list, tuple, set)):
            for sous_valeur in valeur:
                visiter(sous_valeur)

    visiter(resultat)
    return autorises


class OptimisationAssisteeTestCase(unittest.TestCase):
    """Les suggestions sont calculées, comparées et jamais appliquées."""

    def contexte(self, taches):
        resultat = compute(taches)
        return (
            copy.deepcopy(resultat.resultats),
            resultat.duree_totale,
            copy.deepcopy(resultat.chemins_critiques),
            copy.deepcopy(resultat.successeurs),
        )

    def test_chaine_a4_b6_c2_gains_et_simuler(self):
        taches = {"A": t(4), "B": t(6, "A"), "C": t(2, "B")}
        avant = copy.deepcopy(taches)
        resultats, duree, chemins, successeurs = self.contexte(taches)

        scenarios = suggerer_scenarios(
            taches, resultats, duree, chemins, successeurs
        )

        self.assertEqual(avant, taches)
        compression = [s for s in scenarios if s["type"] == "crashing"]
        self.assertEqual(3, len(compression))
        self.assertEqual(
            {"A": 3, "B": 5, "C": 1},
            {code: duree for s in compression for code, duree in s["reductions"].items()},
        )
        for scenario in compression:
            reductions = scenario["reductions"]
            attendu = simuler(avant, reductions)
            self.assertEqual(attendu, scenario["resultat"])
            self.assertEqual(12, attendu["initial"]["duree_totale"])
            self.assertEqual(11, attendu["simule"]["duree_totale"])
            self.assertEqual(1, attendu["gains"])

        # Le gain du fast-tracking vient du compute() sur la copie sans arête.
        copie = copy.deepcopy(avant)
        copie["B"]["depend_de"] = []
        gain_attendu = duree - compute(copie).duree_totale
        rapide = [s for s in scenarios if s["type"] == "fast_tracking"]
        self.assertEqual(1, len(rapide))
        self.assertEqual(("A", "B"), rapide[0]["dependance_retiree"])
        self.assertEqual(gain_attendu, rapide[0]["resultat"]["gains"])

    def test_tri_par_gain_decroissant_puis_titre(self):
        taches = {"A": t(4), "B": t(4, "A")}
        resultats, duree, chemins, successeurs = self.contexte(taches)

        scenarios = suggerer_scenarios(
            taches, resultats, duree, chemins, successeurs
        )

        self.assertEqual(
            scenarios,
            sorted(
                scenarios,
                key=lambda s: (-s["resultat"]["gains"], s["titre"]),
            ),
        )
        gains = [s["resultat"]["gains"] for s in scenarios]
        self.assertEqual(sorted(gains, reverse=True), gains)
        self.assertGreater(gains[0], gains[1])

    def test_reduction_dix_pourcent_arrondie_au_plus_proche(self):
        taches = {"A": t(15), "B": t(20, "A")}
        avant = copy.deepcopy(taches)
        resultats, duree, chemins, successeurs = self.contexte(taches)

        scenarios = suggerer_scenarios(
            taches, resultats, duree, chemins, successeurs
        )

        reductions = [
            s["reductions"] for s in scenarios if s["type"] == "crashing"
        ]
        # 10 % de 15 = 1,5 -> 2 ; 10 % de 20 = 2.
        self.assertIn({"A": 13}, reductions)
        self.assertIn({"B": 18}, reductions)
        for scenario in scenarios:
            if scenario["type"] == "crashing":
                self.assertEqual(
                    simuler(avant, scenario["reductions"]), scenario["resultat"]
                )

    def test_planning_et_donnees_recus_jamais_modifies(self):
        taches = {
            "A": {"description": "Analyse", "duree": 3, "depend_de": []},
            "B": {"description": "Conception", "duree": 5, "depend_de": ["A"]},
        }
        resultats, duree, chemins, successeurs = self.contexte(taches)
        avant_taches = copy.deepcopy(taches)
        avant_resultats = copy.deepcopy(resultats)
        avant_chemins = copy.deepcopy(chemins)
        avant_successeurs = copy.deepcopy(successeurs)

        scenarios = suggerer_scenarios(
            taches, resultats, duree, chemins, successeurs
        )

        self.assertEqual(avant_taches, taches)
        self.assertEqual(avant_resultats, resultats)
        self.assertEqual(avant_chemins, chemins)
        self.assertEqual(avant_successeurs, successeurs)
        self.assertTrue(scenarios)
        for scenario in scenarios:
            self.assertFalse(scenario["planning_reference_modifie"])

        # Les résultats rendus ne partagent pas leurs dicts avec les entrées.
        scenarios[0]["resultat"]["initial"]["resultats"]["A"]["ES"] = 999
        scenarios[0]["reductions"]["A"] = 999
        self.assertEqual(avant_taches, taches)
        self.assertEqual(avant_resultats, resultats)

    def test_contrat_du_resultat_pour_chaque_scenario(self):
        taches = {"A": t(4), "B": t(6, "A"), "C": t(2, "B")}
        resultats, duree, chemins, successeurs = self.contexte(taches)

        scenarios = suggerer_scenarios(
            taches, resultats, duree, chemins, successeurs
        )

        self.assertTrue(scenarios)
        for scenario in scenarios:
            with self.subTest(titre=scenario["titre"]):
                self.assertEqual(CLES_SCENARIO, set(scenario))
                self.assertIsInstance(scenario["titre"], str)
                self.assertIsInstance(scenario["description"], str)
                self.assertIn(scenario["type"], ("crashing", "fast_tracking"))
                self.assertIsInstance(scenario["hypotheses"], str)
                self.assertTrue(scenario["hypotheses"].strip())
                self.assertIsInstance(scenario["limites"], str)
                self.assertTrue(scenario["limites"].strip())
                self.assertFalse(scenario["planning_reference_modifie"])

                resultat = scenario["resultat"]
                self.assertEqual(CLES_SIMULATION, set(resultat))
                self.assertEqual(CLES_RESUME, set(resultat["initial"]))
                self.assertEqual(CLES_RESUME, set(resultat["simule"]))
                self.assertIsInstance(resultat["gains"], (int, float))
                self.assertIsInstance(resultat["modifications"], list)
                self.assertIsInstance(resultat["taches_accelerables"], list)
                if scenario["type"] == "crashing":
                    self.assertIsNone(scenario["dependance_retiree"])
                else:
                    self.assertIsInstance(scenario["dependance_retiree"], tuple)
                    self.assertEqual({}, scenario["reductions"])

    def test_fast_tracking_hypothese_et_limite_explicites(self):
        taches = {"A": t(4), "B": t(6, "A"), "C": t(2, "B")}
        resultats, duree, chemins, successeurs = self.contexte(taches)

        scenarios = suggerer_scenarios(
            taches, resultats, duree, chemins, successeurs
        )

        rapides = [s for s in scenarios if s["type"] == "fast_tracking"]
        self.assertEqual(1, len(rapides))
        scenario = rapides[0]
        self.assertEqual(("A", "B"), scenario["dependance_retiree"])
        self.assertIn(
            "suppose que le chevauchement est autorisé par les contraintes métier",
            scenario["hypotheses"],
        )
        self.assertIn(
            "aucune contrainte métier n'a été fournie : ce scénario est une "
            "hypothèse à valider, pas une recommandation",
            scenario["limites"],
        )
        self.assertIn("moteur PERT/CPM", scenario["hypotheses"])
        self.assertIn("planning de référence", scenario["limites"])
        self.assertFalse(scenario["planning_reference_modifie"])

    def test_aucune_tache_critique(self):
        taches = {"A": t(3), "B": t(2, "A")}
        _, duree, chemins, successeurs = self.contexte(taches)
        sans_criticite = {
            "A": {"critique": False, "marge": 1, "marge_libre": 0},
            "B": {"critique": False, "marge": 1, "marge_libre": 1},
        }

        self.assertEqual(
            [], suggerer_scenarios(taches, sans_criticite, duree, chemins, successeurs)
        )
        self.assertEqual([], suggerer_scenarios(taches, {}, duree, chemins, successeurs))

    def test_durees_critiques_nulles_sans_scenario(self):
        taches = {"A": t(0), "B": t(0, "A")}
        resultats, duree, chemins, successeurs = self.contexte(taches)
        self.assertTrue(all(v["critique"] for v in resultats.values()))

        self.assertEqual(
            [], suggerer_scenarios(taches, resultats, duree, chemins, successeurs)
        )

    def test_projet_vide(self):
        self.assertEqual([], suggerer_scenarios({}, {}, 0, [], None))
        self.assertEqual([], suggerer_scenarios({}, {}, 0, [], {}))

    def test_limite_a_six_scenarios(self):
        taches = {}
        precedent = None
        for code in "ABCDEFGH":
            taches[code] = t(2, precedent) if precedent else t(2)
            precedent = code
        avant = copy.deepcopy(taches)
        resultats, duree, chemins, successeurs = self.contexte(taches)

        scenarios = suggerer_scenarios(
            taches, resultats, duree, chemins, successeurs
        )

        self.assertEqual(avant, taches)
        self.assertEqual(MAX_SCENARIOS, len(scenarios))
        self.assertTrue(
            any(s["type"] == "fast_tracking" for s in scenarios)
        )
        gains = [s["resultat"]["gains"] for s in scenarios]
        self.assertEqual(sorted(gains, reverse=True), gains)
        for scenario in scenarios:
            self.assertIn(str(MAX_SCENARIOS), scenario["limites"])

    def test_compromis_contient_le_gain_reel_et_uniquement_des_nombres_calcules(self):
        taches = {"A": t(4), "B": t(6, "A"), "C": t(2, "B")}
        avant = copy.deepcopy(taches)
        resultats, duree, chemins, successeurs = self.contexte(taches)

        scenarios = suggerer_scenarios(
            taches, resultats, duree, chemins, successeurs
        )

        self.assertTrue(scenarios)
        for scenario in scenarios:
            with self.subTest(titre=scenario["titre"]):
                resultat = scenario["resultat"]
                texte = scenario["compromis"]
                self.assertIn(str(resultat["gains"]), texte)
                self.assertIn("Gain", texte)
                self.assertIn("critique", texte.lower())
                self.assertIn("marge", texte.lower())
                self.assertIn("coût", texte)
                self.assertIn("ressource", texte)
                self.assertTrue(nombres(texte).issubset(nombres_resultat(resultat)))
                if scenario["type"] == "crashing":
                    self.assertEqual(simuler(avant, scenario["reductions"]), resultat)

    def test_compromis_suit_les_changements_de_chemin_critique(self):
        # Deux chemins critiques : réduire C ne doit pas laisser croire que
        # le gain est illimité ; le texte suit exactement le résultat calculé.
        taches = {"A": t(2), "B": t(3, "A"), "C": t(3, "A"), "D": t(2, "B", "C")}
        avant = copy.deepcopy(taches)
        resultats, duree, chemins, successeurs = self.contexte(taches)

        scenarios = suggerer_scenarios(
            taches, resultats, duree, chemins, successeurs
        )

        self.assertTrue(scenarios)
        for scenario in scenarios:
            resultat = scenario["resultat"]
            self.assertEqual(
                resultat["initial"]["chemins_critiques"],
                compute(avant).chemins_critiques,
            )
            self.assertIn(
                "chemins critiques",
                scenario["description"],
            )
            self.assertIn(str(resultat["gains"]), scenario["compromis"])
            self.assertTrue(
                nombres(scenario["compromis"]).issubset(nombres_resultat(resultat))
            )
            if scenario["type"] == "crashing":
                self.assertEqual(simuler(avant, scenario["reductions"]), resultat)


if __name__ == "__main__":
    unittest.main()
