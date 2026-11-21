"""Tests unitaires du moteur PERT/CPM canonique et de la façade Flask."""

import time
import unittest

from logic.calcul import (
    calculer_marges,
    liste_chemin_critique,
    liste_chemins_critiques,
    passe_arriere,
    passe_avant,
    valider_taches,
    generer_conseils,
)
from logic.data import taches as taches_demo
from logic.pert import (
    EPSILON,
    PERTResult,
    ValidationError,
    compute,
    topological_order,
    validate,
)


def t(duree, *depend_de):
    """Raccourci pour construire une tâche de test."""
    return {"duree": duree, "depend_de": list(depend_de)}


class PertEngineTestCase(unittest.TestCase):
    def test_chaine_simple(self):
        taches = {"A": t(2), "B": t(3, "A"), "C": t(4, "B")}
        resultat = compute(taches)

        self.assertIsInstance(resultat, PERTResult)
        self.assertEqual(9, resultat.duree_totale)
        self.assertEqual(["A", "B", "C"], resultat.ordre)
        self.assertEqual(
            {"A": {"ES": 0, "EF": 2}, "B": {"ES": 2, "EF": 5},
             "C": {"ES": 5, "EF": 9}},
            {nom: {"ES": r["ES"], "EF": r["EF"]}
             for nom, r in resultat.resultats.items()},
        )
        for nom in taches:
            self.assertEqual(0, resultat.resultats[nom]["marge"])
            self.assertEqual(0, resultat.resultats[nom]["marge_totale"])
            self.assertEqual(0, resultat.resultats[nom]["marge_libre"])
            self.assertTrue(resultat.resultats[nom]["critique"])
        self.assertEqual([["A", "B", "C"]], resultat.chemins_critiques)

    def test_branches_paralleles(self):
        taches = {
            "A": t(2),
            "B": t(5, "A"),
            "C": t(3, "A"),
            "D": t(1, "B", "C"),
        }
        r = compute(taches)

        self.assertEqual(8, r.duree_totale)
        self.assertEqual(7, r.resultats["D"]["ES"])
        self.assertEqual(2, r.resultats["C"]["marge_totale"])
        self.assertEqual(2, r.resultats["C"]["marge_libre"])
        self.assertEqual(2, r.resultats["C"]["ES"])
        self.assertEqual(5, r.resultats["C"]["EF"])
        self.assertFalse(r.resultats["C"]["critique"])
        self.assertEqual([["A", "B", "D"]], r.chemins_critiques)

    def test_deux_chemins_critiques(self):
        taches = {
            "A": t(2),
            "B": t(3, "A"),
            "C": t(3, "A"),
            "D": t(2, "B", "C"),
        }
        r = compute(taches)

        self.assertEqual(7, r.duree_totale)
        self.assertEqual([["A", "B", "D"], ["A", "C", "D"]], r.chemins_critiques)
        for nom in ("A", "B", "C", "D"):
            self.assertTrue(r.resultats[nom]["critique"], nom)
            self.assertEqual(0, r.resultats[nom]["marge_totale"])
        self.assertEqual([["A", "B", "D"], ["A", "C", "D"]],
                         liste_chemins_critiques(taches))

    def test_predecesseur_inexistant(self):
        taches = {"A": t(1, "Z"), "B": t(2, "A")}
        erreurs = validate(taches)
        self.assertTrue(any("n'existe pas" in e for e in erreurs), erreurs)

        with self.assertRaises(ValidationError) as ctx:
            compute(taches)
        self.assertIn("Z", str(ctx.exception))
        self.assertIn("n'existe pas", str(ctx.exception))

    def test_cycle_leve_erreur_rapidement(self):
        taches = {"A": t(1, "B"), "B": t(1, "A")}
        erreurs = validate(taches)
        self.assertTrue(any("circulaire" in e for e in erreurs), erreurs)
        self.assertTrue(any("A → B → A" in e for e in erreurs), erreurs)

        debut = time.monotonic()
        with self.assertRaises(ValidationError) as ctx:
            compute(taches)
        duree = time.monotonic() - debut
        self.assertLess(duree, 2.0, "la détection de cycle ne doit pas bloquer")
        self.assertIn("Dépendance circulaire détectée :", str(ctx.exception))
        self.assertIn("A → B → A", str(ctx.exception))

        with self.assertRaises(ValidationError):
            topological_order(taches)

    def test_cycle_avec_tache_en_aval(self):
        taches = {
            "A": t(1, "C"),
            "B": t(1, "A"),
            "C": t(1, "B"),
            "D": t(1, "A"),
        }
        with self.assertRaises(ValidationError) as ctx:
            compute(taches)
        message = str(ctx.exception)
        self.assertIn("Dépendance circulaire détectée :", message)
        self.assertEqual(4, len(message.split("→")), message)

    def test_auto_dependance_est_un_cycle(self):
        with self.assertRaises(ValidationError) as ctx:
            compute({"A": t(1, "A")})
        self.assertIn("A → A", str(ctx.exception))

    def test_duree_negative(self):
        taches = {"A": t(-1), "B": t(2, "A")}
        erreurs = validate(taches)
        self.assertTrue(any("négative" in e for e in erreurs), erreurs)
        with self.assertRaises(ValidationError) as ctx:
            compute(taches)
        self.assertIn("négative", str(ctx.exception))

    def test_durees_invalides(self):
        for mauvaise in (True, False, "3", None, float("nan"), float("inf")):
            with self.subTest(duree=mauvaise):
                taches = {"A": {"duree": mauvaise, "depend_de": []}}
                self.assertTrue(validate(taches), f"durée {mauvaise!r} doit être refusée")
                with self.assertRaises(ValidationError):
                    compute(taches)

    def test_duree_nulle_est_valide(self):
        taches = {"A": t(0), "B": t(2, "A")}
        r = compute(taches)

        self.assertEqual([], validate(taches))
        self.assertEqual(2, r.duree_totale)
        self.assertEqual({"ES": 0, "EF": 0}, {k: r.resultats["A"][k] for k in ("ES", "EF")})
        self.assertEqual({"ES": 0, "EF": 2}, {k: r.resultats["B"][k] for k in ("ES", "EF")})
        self.assertEqual([["A", "B"]], r.chemins_critiques)

    def test_identifiants_invalides_et_doublons(self):
        self.assertTrue(any("vide" in e for e in validate({"": t(1)})))
        self.assertTrue(any("double" in e for e in validate({"A": t(1), "A ": t(1)})))

    def test_epsilon_absorbe_les_residus_flottants(self):
        self.assertEqual(1e-9, EPSILON)
        r = compute({"A": t(0.1), "B": t(0.2, "A")})
        for nom in ("A", "B"):
            self.assertEqual(0, r.resultats[nom]["marge_totale"])
            self.assertTrue(r.resultats[nom]["critique"])
        self.assertEqual([["A", "B"]], r.chemins_critiques)

    def test_graphe_vide(self):
        r = compute({})
        self.assertEqual(0, r.duree_totale)
        self.assertEqual({}, r.resultats)
        self.assertEqual([], r.chemins_critiques)


class DemoReferenceTestCase(unittest.TestCase):
    """Valeurs de référence de la démo 12 tâches (logic/data.py)."""

    ESPEREES_MT = {
        "A": 0, "B": 0, "C": 2, "D": 2, "E": 4, "F": 2,
        "G": 0, "H": 0, "I": 2, "J": 2, "K": 0, "L": 0,
    }
    ESPEREES_ML = {
        "A": 0, "B": 0, "C": 0, "D": 2, "E": 4, "F": 2,
        "G": 0, "H": 0, "I": 0, "J": 2, "K": 0, "L": 0,
    }

    def test_demo_12_taches(self):
        self.assertEqual(12, len(taches_demo))
        r = compute(taches_demo)

        self.assertEqual(30, r.duree_totale)
        self.assertEqual(self.ESPEREES_MT,
                         {nom: res["marge_totale"] for nom, res in r.resultats.items()})
        self.assertEqual(self.ESPEREES_ML,
                         {nom: res["marge_libre"] for nom, res in r.resultats.items()})
        self.assertEqual(self.ESPEREES_MT,
                         {nom: res["marge"] for nom, res in r.resultats.items()})
        self.assertEqual([["A", "B", "G", "H", "K", "L"]], r.chemins_critiques)
        self.assertEqual(["A", "B", "G", "H", "K", "L"],
                         liste_chemin_critique(r.resultats))

    def test_annexe_a(self):
        taches = {
            "A": t(3),
            "B": t(4, "A"),
            "C": t(2, "A"),
            "D": t(6, "B", "C"),
        }
        r = compute(taches)

        self.assertEqual(13, r.duree_totale)
        self.assertEqual(["A", "B", "D"], [nom for nom, res in r.resultats.items()
                                           if res["critique"]])
        self.assertEqual(2, r.resultats["C"]["marge_totale"])
        self.assertEqual(2, r.resultats["C"]["marge_libre"])
        self.assertEqual([["A", "B", "D"]], r.chemins_critiques)

    def test_multi_departs_multi_fins(self):
        taches = {
            "A": t(2),
            "X": t(1),
            "B": t(3, "A"),
            "C": t(2, "X"),
        }
        r = compute(taches)

        self.assertEqual(5, r.duree_totale)
        self.assertEqual(2, r.resultats["C"]["marge_totale"])
        self.assertEqual(2, r.resultats["C"]["marge_libre"])
        self.assertEqual(2, r.resultats["X"]["marge_totale"])
        self.assertEqual(["A", "B"], [nom for nom, res in r.resultats.items()
                                      if res["critique"]])
        self.assertEqual([["A", "B"]], r.chemins_critiques)

    def test_multi_chemins_avec_departs_et_fins_distincts(self):
        taches = {
            "A": t(2),
            "X": t(2),
            "B": t(3, "A"),
            "C": t(3, "X"),
            "D": t(1, "B"),
            "E": t(1, "C"),
        }
        r = compute(taches)

        self.assertEqual(6, r.duree_totale)
        self.assertEqual([["A", "B", "D"], ["X", "C", "E"]], r.chemins_critiques)


class FacadeCompatibiliteTestCase(unittest.TestCase):
    def test_fonctions_historiques(self):
        taches = {"A": t(2), "B": t(3, "A"), "C": t(4, "B")}
        av = passe_avant(taches)
        ar = passe_arriere(taches, av)
        marges = calculer_marges(taches, av, ar)

        self.assertEqual({"A": {"ES": 0, "EF": 2}, "B": {"ES": 2, "EF": 5},
                          "C": {"ES": 5, "EF": 9}}, av)
        self.assertEqual({"A": {"LS": 0, "LF": 2}, "B": {"LS": 2, "LF": 5},
                          "C": {"LS": 5, "LF": 9}}, ar)
        self.assertEqual({"ES": 0, "EF": 2, "LS": 0, "LF": 2,
                          "marge": 0, "critique": True}, marges["A"])
        self.assertEqual(["A", "B", "C"], liste_chemin_critique(marges))
        self.assertEqual([], valider_taches(taches))

    def test_generer_conseils_accepte_resultats_etendus(self):
        r = compute(taches_demo)
        conseils = generer_conseils(
            taches_demo, r.resultats, liste_chemin_critique(r.resultats), r.duree_totale
        )
        self.assertTrue(conseils)
        self.assertTrue(all({"type", "texte"} <= set(c) for c in conseils))
        self.assertEqual("critique", conseils[0]["type"])
        self.assertIn("A → B → G → H → K → L", conseils[0]["texte"])


if __name__ == '__main__':
    unittest.main()
