"""Tests unitaires du parser de saisie PERT/CPM (unittest, stdlib uniquement)."""

import unittest

from logic.data import MAX_LIGNES, parser_taches


class ParserTachesTests(unittest.TestCase):
    def assert_sans_erreur(self, texte):
        taches, erreurs = parser_taches(texte)
        self.assertEqual(erreurs, [])
        return taches

    def test_duree_normale(self):
        taches = self.assert_sans_erreur("A;Analyse;3\nB;Conception;4;A")
        self.assertEqual(set(taches), {"A", "B"})
        self.assertEqual(taches["A"]["description"], "Analyse")
        self.assertEqual(taches["A"]["duree"], 3)
        self.assertIsInstance(taches["A"]["duree"], int)
        self.assertEqual(taches["B"]["depend_de"], ["A"])

    def test_predecesseurs_separes_par_virgule(self):
        taches = self.assert_sans_erreur("A;;3\nB;Conception;4; A , C ")
        self.assertEqual(taches["B"]["depend_de"], ["A", "C"])

    def test_predecesseurs_separes_par_point_virgule(self):
        taches = self.assert_sans_erreur("A;Analyse;3;B;C")
        self.assertEqual(taches["A"]["depend_de"], ["B", "C"])

    def test_champ_quatre_vide_est_autorise(self):
        taches = self.assert_sans_erreur("A;Analyse;3;")
        self.assertEqual(taches["A"]["depend_de"], [])

    def test_chaine_vide(self):
        self.assertEqual(parser_taches(""), ({}, []))
        self.assertEqual(parser_taches("   \n\t\n"), ({}, []))

    def test_nom_vide(self):
        taches, erreurs = parser_taches(";Analyse;3")
        self.assertEqual(taches, {})
        self.assertTrue(any("Ligne 1" in e and "nom" in e for e in erreurs))

    def test_doublon(self):
        taches, erreurs = parser_taches("A;Analyse;3\nA;Autre;2")
        self.assertEqual(set(taches), {"A"})
        self.assertTrue(
            any("Ligne 2" in e and "plusieurs fois" in e for e in erreurs)
        )

    def test_format_insuffisant(self):
        taches, erreurs = parser_taches("A;Analyse")
        self.assertEqual(taches, {})
        self.assertTrue(any("Ligne 1" in e and "format invalide" in e for e in erreurs))

    def test_duree_negative(self):
        taches, erreurs = parser_taches("A;Analyse;-2")
        self.assertEqual(taches, {})
        self.assertTrue(any("Ligne 1" in e and "négat" in e for e in erreurs))

    def test_duree_exposant_carre_ne_leve_pas_exception(self):
        taches, erreurs = parser_taches("A;Analyse;²")
        self.assertEqual(taches, {})
        self.assertEqual(len(erreurs), 1)
        self.assertIn("Ligne 1", erreurs[0])
        self.assertIn("durée", erreurs[0])

    def test_duree_non_entiere(self):
        taches, erreurs = parser_taches("A;Analyse;1.5")
        self.assertEqual(taches, {})
        self.assertTrue(any("Ligne 1" in e and "entier" in e for e in erreurs))

    def test_duree_vide(self):
        taches, erreurs = parser_taches("A;Analyse;")
        self.assertEqual(taches, {})
        self.assertTrue(any("Ligne 1" in e and "manquante" in e for e in erreurs))

    def test_duree_zero_acceptee(self):
        taches = self.assert_sans_erreur("A;Jalon;0")
        self.assertEqual(taches["A"]["duree"], 0)

    def test_champs_surnumeraires_avec_champ_vide(self):
        taches, erreurs = parser_taches("A;Analyse;3;;X")
        self.assertTrue(
            any("Ligne 1" in e and "vide" in e and "champ 4" in e for e in erreurs)
        )
        self.assertEqual(taches["A"]["depend_de"], ["X"])

    def test_lignes_vides_ignorees(self):
        taches = self.assert_sans_erreur("\nA;Analyse;3\n   \n\nB;;2;A\n")
        self.assertEqual(set(taches), {"A", "B"})

    def test_numero_de_ligne_original(self):
        taches, erreurs = parser_taches("A;;3\n\nB;;x")
        self.assertEqual(set(taches), {"A"})
        self.assertTrue(any("Ligne 3" in e for e in erreurs))

    def test_type_de_saisie_invalide(self):
        taches, erreurs = parser_taches(None)
        self.assertEqual(taches, {})
        self.assertEqual(len(erreurs), 1)
        self.assertIn("chaîne de caractères", erreurs[0])

    def test_trop_de_lignes(self):
        taches, erreurs = parser_taches("A;;3\n" * (MAX_LIGNES + 1))
        self.assertEqual(taches, {})
        self.assertEqual(len(erreurs), 1)
        self.assertIn("trop de lignes", erreurs[0])


if __name__ == "__main__":
    unittest.main()
