"""Tests unitaires du service d'actions contrôlées (lot IA-4, §19.6).

Aucun appel réseau, aucune base de données : le service ne fait que comprendre
et valider des propositions. Les tests vérifient aussi qu'aucun dictionnaire
reçu n'est modifié et qu'aucun chiffre n'est inventé dans les changements.
"""

from __future__ import annotations

import copy
import re
import unittest

from logic.pert import compute
from services.actions_ia import preparer_action, valider_action

TACHES = {
    "A": {"description": "Analyse des besoins", "duree": 2, "depend_de": []},
    "B": {"description": "Conception", "duree": 4, "depend_de": ["A"]},
    "C": {"description": "Développement", "duree": 3, "depend_de": ["B"]},
    "D": {"description": "Documentation", "duree": 1, "depend_de": ["B"]},
}
RESULTATS = compute(TACHES).resultats

CLES_ATTENDUES = {
    "type", "cible", "parametres", "changements", "description",
    "destructrice", "confirmation_requise", "confiance", "avertissements",
}


class ActionsIATestCase(unittest.TestCase):
    """Compréhension, validation et garde-fous des actions en langage naturel."""

    def setUp(self):
        self.taches = copy.deepcopy(TACHES)
        self.resultats = RESULTATS

    def preparer(self, texte):
        return preparer_action(texte, self.taches, self.resultats)

    def valider(self, proposition):
        return valider_action(proposition, self.taches)

    def test_structure_de_reponse_toujours_complete(self):
        for proposition in (
            self.preparer("augmente la durée de la tâche B de 2 jours"),
            self.preparer("je voudrais un croissant"),
        ):
            self.assertEqual(CLES_ATTENDUES, set(proposition))
            self.assertIsInstance(proposition["changements"], list)
            self.assertIsInstance(proposition["avertissements"], list)
            self.assertIsInstance(proposition["description"], str)
            self.assertTrue(proposition["description"])
            self.assertIn(proposition["confirmation_requise"], ("CONFIRMER", "SUPPRIMER"))
            self.assertIn(proposition["confiance"], ("elevee", "faible"))

    def test_augmentation_relative_avec_changements_lisibles(self):
        proposition = self.preparer("Augmente la durée de la tâche B de 2 jours")

        self.assertEqual("modifier_duree", proposition["type"])
        self.assertEqual("B", proposition["cible"])
        self.assertEqual(6, proposition["parametres"]["nouvelle_duree"])
        self.assertEqual("elevee", proposition["confiance"])
        self.assertEqual("CONFIRMER", proposition["confirmation_requise"])
        self.assertFalse(proposition["destructrice"])
        self.assertTrue(proposition["changements"])
        self.assertTrue(any("B" in c and "4" in c and "6" in c
                            for c in proposition["changements"]))

    def test_augmentation_avec_signe_plus(self):
        proposition = self.preparer("durée B +2")

        self.assertEqual("modifier_duree", proposition["type"])
        self.assertEqual("B", proposition["cible"])
        self.assertEqual(6, proposition["parametres"]["nouvelle_duree"])

    def test_diminution_relative(self):
        proposition = self.preparer("réduis la durée de la tâche B de 2 jours")

        self.assertEqual("modifier_duree", proposition["type"])
        self.assertEqual("B", proposition["cible"])
        self.assertEqual(2, proposition["parametres"]["nouvelle_duree"])
        self.assertTrue(any("4" in c and "2" in c
                            for c in proposition["changements"]))

    def test_valeur_absolue(self):
        proposition = self.preparer("fixe la durée de la tâche B à 5 jours")

        self.assertEqual("modifier_duree", proposition["type"])
        self.assertEqual(5, proposition["parametres"]["nouvelle_duree"])
        self.assertTrue(any("5" in c for c in proposition["changements"]))

    def test_nombres_en_mots_francais(self):
        proposition = self.preparer("augmente la durée de la tâche B de deux jours")

        self.assertEqual(6, proposition["parametres"]["nouvelle_duree"])

    def test_ajout_de_tache_avec_code_et_predecesseur(self):
        proposition = self.preparer("ajoute une tâche E après B")

        self.assertEqual("ajouter_tache", proposition["type"])
        self.assertEqual("E", proposition["cible"])
        self.assertEqual(["B"], proposition["parametres"]["predecesseurs"])
        self.assertEqual(1, proposition["parametres"]["duree"])
        self.assertEqual("Nouvelle tâche", proposition["parametres"]["description"])
        self.assertTrue(any("durée par défaut de 1 jour" in a
                            and "Hypothèse" in a
                            for a in proposition["avertissements"]))
        self.assertFalse(proposition["destructrice"])
        ok, _ = self.valider(proposition)
        self.assertTrue(ok)

    def test_ajout_de_phase_sans_code_genere_un_code_stable(self):
        proposition = self.preparer("ajoute une phase de tests après B")

        self.assertEqual("ajouter_tache", proposition["type"])
        self.assertEqual("NOUVEAU_1", proposition["cible"])
        self.assertEqual(["B"], proposition["parametres"]["predecesseurs"])
        self.assertIn("tests", proposition["parametres"]["description"].lower())
        self.assertTrue(any("Hypothèse" in a and "1 jour" in a
                            for a in proposition["avertissements"]))
        ok, _ = self.valider(proposition)
        self.assertTrue(ok)
        # Le code généré reste stable pour un même planning.
        self.assertEqual(
            proposition["cible"],
            self.preparer("ajoute une phase de tests après B")["cible"],
        )

    def test_suppression_est_destructrice_avec_confirmation_renforcee(self):
        proposition = self.preparer("supprime la tâche B")

        self.assertEqual("supprimer_tache", proposition["type"])
        self.assertEqual("B", proposition["cible"])
        self.assertTrue(proposition["destructrice"])
        self.assertEqual("SUPPRIMER", proposition["confirmation_requise"])
        ok, messages = self.valider(proposition)
        self.assertTrue(ok)
        self.assertTrue(any("dépendent" in m for m in messages))

    def test_ajout_de_predecesseur(self):
        proposition = self.preparer("ajoute D comme prédécesseur de C")

        self.assertEqual("ajouter_predecesseur", proposition["type"])
        self.assertEqual("C", proposition["cible"])
        self.assertEqual("D", proposition["parametres"]["predecesseur"])
        self.assertFalse(proposition["destructrice"])
        ok, erreurs = self.valider(proposition)
        self.assertTrue(ok, erreurs)

    def test_ajout_de_predecesseur_formulation_alternative(self):
        proposition = self.preparer("ajoute un prédécesseur D à C")

        self.assertEqual("ajouter_predecesseur", proposition["type"])
        self.assertEqual("C", proposition["cible"])
        self.assertEqual("D", proposition["parametres"]["predecesseur"])

    def test_demande_incomprehensible_sans_action(self):
        proposition = self.preparer("Peux-tu m'expliquer la météo de demain ?")

        self.assertEqual("inconnu", proposition["type"])
        self.assertIsNone(proposition["cible"])
        self.assertEqual({}, proposition["parametres"])
        self.assertEqual("faible", proposition["confiance"])
        self.assertFalse(proposition["destructrice"])
        self.assertTrue(proposition["avertissements"])
        self.assertTrue(any("augmente" in a for a in proposition["avertissements"]))
        ok, erreurs = self.valider(proposition)
        self.assertFalse(ok)
        self.assertTrue(any("non comprise" in e for e in erreurs))

    def test_validation_cible_inexistante(self):
        proposition = self.preparer("augmente la durée de la tâche Z de 2 jours")

        self.assertEqual("modifier_duree", proposition["type"])
        self.assertTrue(any("Z" in a for a in proposition["avertissements"]))
        ok, erreurs = self.valider(proposition)
        self.assertFalse(ok)
        self.assertTrue(any("Z" in e for e in erreurs))

    def test_validation_duree_negative(self):
        ok, erreurs = self.valider({
            "type": "modifier_duree",
            "cible": "B",
            "parametres": {"nouvelle_duree": -3},
        })
        self.assertFalse(ok)
        self.assertTrue(erreurs)

        proposition = self.preparer("diminue la durée de la tâche B de 10 jours")
        self.assertLess(proposition["parametres"]["nouvelle_duree"], 0)
        ok, _ = self.valider(proposition)
        self.assertFalse(ok)

    def test_validation_predecesseur_inexistant(self):
        proposition = self.preparer("ajoute une tâche E après Z")
        self.assertEqual(["Z"], proposition["parametres"]["predecesseurs"])
        ok, erreurs = self.valider(proposition)
        self.assertFalse(ok)
        self.assertTrue(any("Z" in e for e in erreurs))

        ok, erreurs = self.valider({
            "type": "ajouter_predecesseur",
            "cible": "C",
            "parametres": {"predecesseur": "Z"},
        })
        self.assertFalse(ok)
        self.assertTrue(any("Z" in e for e in erreurs))

    def test_validation_cycle_par_le_moteur(self):
        # C dépend déjà de B : ajouter C comme prédécesseur de B crée un cycle.
        proposition = self.preparer("ajoute C comme prédécesseur de B")

        self.assertEqual("B", proposition["cible"])
        self.assertEqual("C", proposition["parametres"]["predecesseur"])
        ok, erreurs = self.valider(proposition)
        self.assertFalse(ok)
        self.assertTrue(any("cycle" in e.lower() for e in erreurs))

    def test_validation_suppression_avec_dependants(self):
        ok, messages = self.valider({
            "type": "supprimer_tache",
            "cible": "B",
            "parametres": {},
        })

        self.assertTrue(ok)
        self.assertTrue(any("dépendent" in m for m in messages))
        self.assertTrue(any("D" in m and "C" in m for m in messages))

    def test_code_deja_utilise_rejete(self):
        proposition = self.preparer("ajoute une tâche C après A")

        self.assertEqual("C", proposition["cible"])
        ok, erreurs = self.valider(proposition)
        self.assertFalse(ok)
        self.assertTrue(any("déjà utilisé" in e for e in erreurs))

    def test_suppression_massive_non_executable(self):
        proposition = self.preparer("supprime toutes les tâches")

        self.assertEqual("supprimer_tache", proposition["type"])
        self.assertIsNone(proposition["cible"])
        self.assertTrue(proposition["destructrice"])
        self.assertEqual("SUPPRIMER", proposition["confirmation_requise"])
        ok, _ = self.valider(proposition)
        self.assertFalse(ok)

    def test_aucun_chiffre_invente_dans_les_changements(self):
        proposition = self.preparer("augmente la durée de la tâche B de 2 jours")

        chiffres = set()
        for changement in proposition["changements"]:
            chiffres.update(re.findall(r"\d+", changement))
        self.assertEqual({"4", "6"}, chiffres)
        self.assertNotIn("999", " ".join(proposition["changements"]))

    def test_non_mutation_des_dictionnaires(self):
        taches_avant = copy.deepcopy(self.taches)
        resultats_avant = copy.deepcopy(self.resultats)
        propositions = [
            self.preparer("augmente la durée de la tâche B de 2 jours"),
            self.preparer("ajoute une tâche E après B"),
            self.preparer("supprime la tâche B"),
            self.preparer("ajoute C comme prédécesseur de A"),
        ]
        for proposition in propositions:
            self.valider(proposition)

        self.assertEqual(taches_avant, self.taches)
        self.assertEqual(resultats_avant, self.resultats)
        self.assertEqual(TACHES, self.taches)

    def test_entrees_inattendues_sans_exception(self):
        proposition = preparer_action(None, None, None)
        self.assertEqual("inconnu", proposition["type"])
        self.assertEqual("faible", proposition["confiance"])
        ok, erreurs = valider_action(None, None)
        self.assertFalse(ok)
        self.assertTrue(erreurs)


if __name__ == "__main__":
    unittest.main()
