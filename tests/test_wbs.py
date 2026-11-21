"""Tests unitaires du service de génération WBS (IA-GEN-01 à IA-GEN-06).

Aucun appel réseau : la génération est déterministe et le moteur PERT/CPM
est utilisé uniquement pour valider les propositions.
"""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from logic.pert import compute
from services.wbs import generer_wbs, valider_proposition


class GenerationWBSTestCase(unittest.TestCase):
    """Génération par règles, validation et garde-fous de la proposition."""

    @staticmethod
    def toutes_les_taches(wbs):
        return [t for phase in wbs["phases"] for t in phase["taches"]]

    def test_objectif_web_cinq_phases_et_graphe_valide(self):
        wbs = generer_wbs("Créer un site web de réservation en ligne")

        self.assertEqual(
            ["Cadrage", "Conception", "Développement", "Tests", "Déploiement"],
            [phase["nom"] for phase in wbs["phases"]],
        )
        taches = self.toutes_les_taches(wbs)
        codes = [tache["code"] for tache in taches]
        self.assertEqual(len(codes), len(set(codes)), "codes de tâches uniques")
        self.assertTrue(all(2 <= len(p["taches"]) <= 3 for p in wbs["phases"]))
        self.assertTrue(
            all(sum(1 for t in p["taches"] if t["jalon"]) == 1
                for p in wbs["phases"]),
            "un jalon en fin de chaque phase",
        )
        self.assertTrue(all(isinstance(t["duree"], int) for t in taches))
        self.assertEqual("regles", wbs["source"])

        graphe, erreurs = valider_proposition(wbs)
        self.assertEqual([], erreurs)
        resultat = compute(graphe)
        self.assertGreater(resultat.duree_totale, 0)
        self.assertTrue(resultat.chemins_critiques)

    def test_objectif_mobile(self):
        wbs = generer_wbs("Développer une application mobile Android et iOS")

        noms = [phase["nom"] for phase in wbs["phases"]]
        self.assertEqual(5, len(noms))
        self.assertEqual("Publication", noms[-1])
        graphe, erreurs = valider_proposition(wbs)
        self.assertEqual([], erreurs)
        self.assertGreater(compute(graphe).duree_totale, 0)

    def test_objectif_donnees_ia_six_phases(self):
        wbs = generer_wbs("Mettre en place un pipeline de données et un modèle d'IA")

        self.assertEqual(6, len(wbs["phases"]))
        self.assertEqual(
            ["Cadrage", "Collecte", "Préparation", "Modélisation",
             "Validation", "Restitution"],
            [phase["nom"] for phase in wbs["phases"]],
        )
        graphe, erreurs = valider_proposition(wbs)
        self.assertEqual([], erreurs)

    def test_objectif_generique(self):
        wbs = generer_wbs("Refonte du processus d'accueil des visiteurs")

        self.assertEqual(5, len(wbs["phases"]))
        self.assertEqual("Clôture", wbs["phases"][-1]["nom"])
        codes = [t["code"] for t in self.toutes_les_taches(wbs)]
        self.assertEqual(len(codes), len(set(codes)))
        graphe, erreurs = valider_proposition(wbs)
        self.assertEqual([], erreurs)
        self.assertGreater(compute(graphe).duree_totale, 0)

    def test_objectif_vide_leve_value_error(self):
        for objectif in ("", "   \n  ", None):
            with self.subTest(objectif=objectif):
                with self.assertRaises(ValueError) as contexte:
                    generer_wbs(objectif)
                self.assertIn("objectif", str(contexte.exception).lower())

    def test_collision_code_avec_tache_existante(self):
        wbs = generer_wbs("Créer un site web")
        existantes = {
            "C1": {"description": "Tâche existante", "duree": 2, "depend_de": []}
        }

        graphe, erreurs = valider_proposition(wbs, existantes)

        self.assertTrue(any("collision" in e.lower() for e in erreurs))
        self.assertEqual("Tâche existante", graphe["C1"]["description"])
        # Le graphe fusionné reste calculable malgré l'erreur signalée.
        self.assertGreater(compute(graphe).duree_totale, 0)

    def test_predecesseur_inexistant_injecte(self):
        wbs = generer_wbs("Créer un site web")
        wbs["phases"][1]["taches"][0]["predecesseurs"] = ["INEXISTANT"]

        _, erreurs = valider_proposition(wbs)

        self.assertTrue(
            any("inexistant" in e.lower() or "n'existe pas" in e.lower()
                for e in erreurs),
            erreurs,
        )

    def test_duree_negative_injectee(self):
        wbs = generer_wbs("Créer un site web")
        wbs["phases"][0]["taches"][0]["duree"] = -2

        _, erreurs = valider_proposition(wbs)

        self.assertTrue(
            any("durée" in e.lower() and "négative" in e.lower() for e in erreurs),
            erreurs,
        )

    def test_cycle_injecte_est_detecte(self):
        wbs = generer_wbs("Créer un site web")
        wbs["phases"][0]["taches"][0]["predecesseurs"] = ["C2"]
        wbs["phases"][0]["taches"][1]["predecesseurs"] = ["C1"]

        _, erreurs = valider_proposition(wbs)

        self.assertTrue(
            any("circulaire" in e.lower() or "cycle" in e.lower() for e in erreurs),
            erreurs,
        )

    def test_non_mutation_des_entrees(self):
        wbs = generer_wbs("Créer un site web")
        existantes = {
            "X1": {"description": "Existant", "duree": 1, "depend_de": []}
        }
        avant_wbs = copy.deepcopy(wbs)
        avant_existantes = copy.deepcopy(existantes)

        valider_proposition(wbs, existantes)

        self.assertEqual(avant_wbs, wbs)
        self.assertEqual(avant_existantes, existantes)

    def test_mentions_indicatif_et_proposition(self):
        wbs = generer_wbs("Créer un site web")

        texte = (wbs["hypotheses"] + " " + wbs["limites"]).lower()
        self.assertIn("indicatif", texte)
        self.assertIn("proposition", texte)
        self.assertIn("non garanties", texte)
        self.assertIn("ressource/coût", texte)
        self.assertIn("relations fs", texte)
        self.assertTrue(wbs["avertissements"])

    def test_mode_sans_ia_sans_appel_reseau(self):
        with patch("services.ia.est_disponible", return_value=False), \
                patch("urllib.request.urlopen",
                      side_effect=AssertionError("appel réseau interdit")):
            wbs = generer_wbs("Créer un site web")
            graphe, erreurs = valider_proposition(wbs)

        self.assertEqual("regles", wbs["source"])
        self.assertEqual([], erreurs)
        self.assertGreater(compute(graphe).duree_totale, 0)


if __name__ == "__main__":
    unittest.main()
