"""Tests unitaires du service de rapport PDF serveur (EXP-02 à EXP-04)."""

import unittest

from logic.pert import compute
from services.rapport import generer_rapport_pdf


def graphe_lineaire():
    """Petit graphe A(2) -> B(3) -> C(4), calculé par le moteur canonique."""
    taches = {
        "A": {"description": "Analyse", "duree": 2, "depend_de": []},
        "B": {"description": "Conception", "duree": 3, "depend_de": ["A"]},
        "C": {"description": "Déploiement", "duree": 4, "depend_de": ["B"]},
    }
    resultat = compute(taches)
    return taches, resultat


class RapportPdfTestCase(unittest.TestCase):
    """Le PDF est généré côté serveur, à partir des résultats du moteur."""

    def test_generation_pdf_structure_et_textes(self):
        taches, resultat = graphe_lineaire()

        pdf = generer_rapport_pdf(
            "Projet démo", taches, resultat.resultats,
            resultat.duree_totale, resultat.chemins_critiques,
            date_debut="01/12/2026",
        )

        self.assertIsInstance(pdf, bytes)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 2000)
        self.assertIn(b"%%EOF", pdf)
        # Le flux de page n'est pas compressé : les textes restent extractibles.
        self.assertIn(b"Rapport PERT/CPM", pdf)
        self.assertIn(b"01/12/2026", pdf)
        self.assertIn(b"A -> B -> C", pdf)

    def test_resultat_manquant_leve_value_error(self):
        taches, resultat = graphe_lineaire()
        resultats_incomplets = dict(resultat.resultats)
        del resultats_incomplets["B"]

        with self.assertRaises(ValueError) as contexte:
            generer_rapport_pdf(
                "Projet démo", taches, resultats_incomplets,
                resultat.duree_totale, resultat.chemins_critiques,
            )

        self.assertIn("Résultat manquant", str(contexte.exception))
        self.assertIn("B", str(contexte.exception))

    def test_caracteres_speciaux_sans_erreur(self):
        taches = {
            "A<&": {
                "description": "Analyse & conception <initiale> — éàç",
                "duree": 1,
                "depend_de": [],
            },
            "B": {
                "description": "Suite > 1 & 2",
                "duree": 2,
                "depend_de": ["A<&"],
            },
        }
        resultat = compute(taches)

        pdf = generer_rapport_pdf(
            "Projet <alpha> & bêta", taches, resultat.resultats,
            resultat.duree_totale, resultat.chemins_critiques,
        )

        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 2000)
        self.assertIn(b"%%EOF", pdf)

    def test_chemins_critiques_multiples(self):
        taches = {
            "A": {"description": "Analyse", "duree": 2, "depend_de": []},
            "B": {"description": "Voie B", "duree": 3, "depend_de": ["A"]},
            "C": {"description": "Voie C", "duree": 3, "depend_de": ["A"]},
            "D": {"description": "Fusion", "duree": 2, "depend_de": ["B", "C"]},
        }
        resultat = compute(taches)
        self.assertEqual(
            [["A", "B", "D"], ["A", "C", "D"]],
            resultat.chemins_critiques,
        )

        pdf = generer_rapport_pdf(
            "Projet à deux chemins", taches, resultat.resultats,
            resultat.duree_totale, resultat.chemins_critiques,
        )

        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertIn(b"%%EOF", pdf)
        self.assertIn(b"A -> B -> D", pdf)
        self.assertIn(b"A -> C -> D", pdf)


if __name__ == "__main__":
    unittest.main()
