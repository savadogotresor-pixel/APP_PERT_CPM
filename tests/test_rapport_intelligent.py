"""Tests unitaires du service de synthèse intelligente (lot IA-4, §19.7).

Les chiffres sont ceux du moteur canonique : le service ne fait que les
présenter. Les tests vérifient aussi l'absence de mutation, le mode sans IA et
le rejet d'un enrichissement IA contenant un chiffre non vérifiable.
"""

import copy
import re
import unittest
from unittest.mock import patch

from logic.pert import compute
from services import ia as service_ia
from services.rapport_intelligent import generer_synthese


CLES_ATTENDUES = {
    "titre",
    "date_generation",
    "donnees_reference",
    "hypotheses",
    "synthese",
    "facteurs_duree",
    "chemin_critique_commentaire",
    "marges_commentaire",
    "risques",
    "recommandations",
    "scenarios_commentaires",
    "source_chiffres",
    "avertissements",
    "appreciation_ia",
}


def graphe_lineaire(avec_tache_libre=False):
    """A(2) -> B(3) -> C(4), éventuellement avec une tâche D(1) autonome."""
    taches = {
        "A": {"description": "Analyse", "duree": 2, "depend_de": []},
        "B": {"description": "Conception", "duree": 3, "depend_de": ["A"]},
        "C": {"description": "Déploiement", "duree": 4, "depend_de": ["B"]},
    }
    if avec_tache_libre:
        taches["D"] = {"description": "Documentation", "duree": 1, "depend_de": []}
    resultat = compute(taches)
    return taches, resultat


class RapportIntelligentTestCase(unittest.TestCase):
    """Structure, chiffres et hypotheses du rapport intelligent."""

    def test_structure_et_chiffres_du_moteur(self):
        taches, resultat = graphe_lineaire()
        rapport = generer_synthese(
            "Projet démo", taches, resultat.resultats,
            resultat.duree_totale, resultat.chemins_critiques,
        )

        self.assertEqual(CLES_ATTENDUES, set(rapport.keys()))
        self.assertEqual("Synthèse intelligente — Projet démo", rapport["titre"])
        self.assertEqual(
            "moteur PERT/CPM (source de vérité)", rapport["source_chiffres"]
        )
        self.assertIsNone(rapport["appreciation_ia"])

        reference = rapport["donnees_reference"]
        self.assertEqual(9, reference["duree_totale"])
        self.assertEqual(3, reference["nombre_taches"])
        self.assertEqual(3, reference["nombre_critiques"])
        self.assertEqual(["A → B → C"], reference["chemins_critiques"])
        self.assertEqual("jours calendaires", reference["unite"])
        self.assertEqual("FS", reference["relations"])

        self.assertIn("9 jours", rapport["synthese"])
        self.assertIn("3 tâches", rapport["synthese"])
        self.assertIn("A → B → C", rapport["synthese"])
        self.assertRegex(
            rapport["date_generation"], r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$"
        )
        self.assertTrue(re.fullmatch(
            r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", rapport["date_generation"]
        ))

        hypotheses = " ".join(rapport["hypotheses"])
        for attendu in (
            "jours calendaires", "FS", "aucune ressource", "planning enregistré",
            "sans prédiction certaine",
        ):
            self.assertIn(attendu, hypotheses.lower() if attendu == "aucune ressource" else hypotheses)

    def test_facteurs_chemin_critique_et_commentaires(self):
        taches, resultat = graphe_lineaire()
        rapport = generer_synthese(
            "Démo", taches, resultat.resultats,
            resultat.duree_totale, resultat.chemins_critiques,
        )

        self.assertEqual(1, len(rapport["facteurs_duree"]))
        facteur = rapport["facteurs_duree"][0]
        self.assertIn("A → B → C", facteur)
        self.assertIn("9 jours calendaires", facteur)
        self.assertIn("tâches critiques : A, B, C", facteur)

        commentaire = rapport["chemin_critique_commentaire"]
        self.assertIn("3 tâches critiques", commentaire)
        self.assertIn("Tout retard", commentaire)
        self.assertIn("retarde la fin", commentaire)
        self.assertIn("toutes les tâches sont critiques",
                      rapport["marges_commentaire"].lower())

    def test_marge_dune_tache_non_critique_est_commentee(self):
        taches, resultat = graphe_lineaire(avec_tache_libre=True)
        rapport = generer_synthese(
            "Démo", taches, resultat.resultats,
            resultat.duree_totale, resultat.chemins_critiques,
        )

        self.assertEqual(4, rapport["donnees_reference"]["nombre_taches"])
        self.assertEqual(3, rapport["donnees_reference"]["nombre_critiques"])
        self.assertIn("D : marge de 8 jours calendaires",
                      rapport["marges_commentaire"])

    def test_risques_avec_origine_et_recommandations(self):
        taches, resultat = graphe_lineaire()
        risques = [
            {
                "titre": "Marge faible sur B",
                "justification": "La tâche B n'a pas de marge disponible.",
                "origine": "regle+ia",
                "code": "B",
                "marge": 0,
            },
            {
                "titre": "Tâche bloquante A",
                "justification": "A précède la tâche B sur le chemin critique.",
                "origine": "regle",
            },
        ]
        rapport = generer_synthese(
            "Démo", taches, resultat.resultats,
            resultat.duree_totale, resultat.chemins_critiques,
            risques=risques,
        )

        textes = rapport["risques"]
        self.assertEqual(2, len(textes))
        self.assertTrue(any("(regle+ia) — Marge faible sur B" in t for t in textes))
        self.assertTrue(any("(regle) — Tâche bloquante A" in t for t in textes))
        self.assertTrue(any("pas de marge disponible" in t for t in textes))
        self.assertTrue(any("A précède la tâche B" in t for t in textes))

        self.assertTrue(rapport["recommandations"])
        self.assertTrue(any(
            "Surveiller la marge de B" in r for r in rapport["recommandations"]
        ))
        self.assertTrue(all(
            "date" not in r.lower() or "s'engager" in r.lower()
            or "sans" in r.lower()
            for r in rapport["recommandations"]
        ))

    def test_scenarios_et_donnees_manquantes(self):
        taches, resultat = graphe_lineaire()
        scenarios = [
            {
                "titre": "Accélérer B",
                "resultat": {
                    "initial": {"duree_totale": 9},
                    "simule": {"duree_totale": 7},
                    "gains": 2,
                },
            },
            {"titre": "Scénario inachevé"},
        ]
        rapport = generer_synthese(
            "Démo", taches, resultat.resultats,
            resultat.duree_totale, resultat.chemins_critiques,
            scenarios=scenarios,
            donnees_manquantes=["date de début non renseignée"],
        )

        self.assertEqual(2, len(rapport["scenarios_commentaires"]))
        self.assertEqual(
            "Accélérer B : 9 → 7 jours (gain 2)",
            rapport["scenarios_commentaires"][0],
        )
        self.assertIn("scénario incomplet", rapport["scenarios_commentaires"][1])
        self.assertIn(
            "Donnée manquante signalée : date de début non renseignée",
            rapport["avertissements"],
        )

    def test_appreciation_ia_rejette_chiffre_non_autorise(self):
        taches, resultat = graphe_lineaire()
        with patch.object(service_ia, "est_disponible", return_value=True), \
                patch.object(service_ia, "repondre", return_value={
                    "texte": "D'après une estimation, la durée serait de 42 jours.",
                    "source": "ia",
                    "appel_ia": True,
                }):
            rapport = generer_synthese(
                "Démo", taches, resultat.resultats,
                resultat.duree_totale, resultat.chemins_critiques,
            )

        self.assertIsNone(rapport["appreciation_ia"])
        self.assertTrue(any("chiffre" in a for a in rapport["avertissements"]))

    def test_appreciation_ia_valide_et_mode_sans_ia(self):
        taches, resultat = graphe_lineaire()
        with patch.object(service_ia, "est_disponible", return_value=False):
            sans_ia = generer_synthese(
                "Démo", taches, resultat.resultats,
                resultat.duree_totale, resultat.chemins_critiques,
            )
        self.assertIsNone(sans_ia["appreciation_ia"])

        with patch.object(service_ia, "est_disponible", return_value=True), \
                patch.object(service_ia, "repondre", return_value={
                    "texte": "La criticité se concentre sur le chemin principal.",
                    "source": "ia",
                    "appel_ia": True,
                }):
            avec_ia = generer_synthese(
                "Démo", taches, resultat.resultats,
                resultat.duree_totale, resultat.chemins_critiques,
            )
        self.assertIn("criticité", avec_ia["appreciation_ia"])

    def test_aucune_mutation_des_arguments(self):
        taches, resultat = graphe_lineaire()
        risques = [{
            "titre": "Marge faible", "justification": "Marge nulle.",
            "origine": "regle+ia", "code": "B", "marge": 0,
        }]
        scenarios = [{
            "titre": "Accélérer B",
            "resultat": {
                "initial": {"duree_totale": 9},
                "simule": {"duree_totale": 8},
                "gains": 1,
            },
        }]
        avant = (
            copy.deepcopy(taches), copy.deepcopy(resultat.resultats),
            copy.deepcopy(resultat.chemins_critiques),
            copy.deepcopy(risques), copy.deepcopy(scenarios),
        )

        generer_synthese(
            "Démo", taches, resultat.resultats,
            resultat.duree_totale, resultat.chemins_critiques,
            risques=risques, scenarios=scenarios,
        )

        self.assertEqual(avant, (
            taches, resultat.resultats, resultat.chemins_critiques,
            risques, scenarios,
        ))

    def test_projet_vide_sans_exception(self):
        rapport = generer_synthese("Projet vide", {}, {}, 0, [])

        self.assertEqual(0, rapport["donnees_reference"]["duree_totale"])
        self.assertEqual(0, rapport["donnees_reference"]["nombre_taches"])
        self.assertEqual(0, rapport["donnees_reference"]["nombre_critiques"])
        self.assertEqual([], rapport["donnees_reference"]["chemins_critiques"])
        self.assertIn("0 jours", rapport["synthese"])
        self.assertTrue(rapport["facteurs_duree"])
        self.assertTrue(rapport["risques"])
        self.assertTrue(rapport["recommandations"])
        self.assertIsNone(rapport["appreciation_ia"])


if __name__ == "__main__":
    unittest.main()
