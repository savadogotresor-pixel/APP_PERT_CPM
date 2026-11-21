"""Tests unitaires du service IA explicatif (IA-ASS-01 à IA-ASS-06).

Aucun appel réseau : le fournisseur est remplacé par des doubles, et
``urllib.request.urlopen`` est patché pour inspecter le payload envoyé.
"""

from __future__ import annotations

import copy
import json
import os
import re
import unittest
from unittest.mock import patch

from logic.data import taches as TACHES_DEMO
from logic.pert import compute
from services import ia


class FausseReponse:
    """Contexte minimal imitant la réponse HTTP du fournisseur."""

    def __init__(self, corps: bytes):
        self.corps = corps

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.corps


class ServiceIATestCase(unittest.TestCase):
    """Mode sans IA, intentions déterministes et garde-fous de sécurité."""

    def setUp(self):
        environ = patch.dict(os.environ, {}, clear=False)
        environ.start()
        self.addCleanup(environ.stop)
        for nom in (
            "IA_API_KEY", "IA_API_URL", "IA_MODEL", "IA_TIMEOUT",
            "IA_MAX_APPELS_PAR_HEURE", "IA_ENABLED",
        ):
            os.environ.pop(nom, None)
        self.addCleanup(ia._APPELS.clear)
        ia._APPELS.clear()

        resultat = compute(TACHES_DEMO)
        self.taches = copy.deepcopy(TACHES_DEMO)
        self.resultats = resultat.resultats
        self.duree = resultat.duree_totale
        self.chemins = resultat.chemins_critiques
        self.successeurs = resultat.successeurs

    def deterministe(self, question, taches=None):
        return ia.repondre_deterministe(
            question,
            self.taches if taches is None else taches,
            self.resultats,
            self.duree,
            self.chemins,
            self.successeurs,
        )

    def repondre(self, question):
        return ia.repondre(
            question, self.taches, self.resultats, self.duree,
            self.chemins, None, self.successeurs,
        )

    def test_mode_sans_ia(self):
        self.assertFalse(ia.est_disponible())
        reponse = self.repondre("Combien de temps ?")

        self.assertEqual("moteur", reponse["source"])
        self.assertFalse(reponse["appel_ia"])
        self.assertIsNone(reponse["tokens"])
        self.assertIn(ia.AVERTISSEMENT_DESACTIVE, reponse["avertissements"])
        self.assertIn(ia.AVERTISSEMENT_MOTEUR, reponse["avertissements"])
        self.assertEqual(
            {
                "texte", "source", "donnees_utilisees", "donnees_manquantes",
                "avertissements", "horodatage", "appel_ia", "tokens",
            },
            set(reponse),
        )

    def test_question_duree_demo_12_taches(self):
        self.assertEqual(12, len(self.taches))
        self.assertEqual(30, self.duree)
        reponse = self.deterministe("Quelle est la durée totale du projet ?")

        self.assertIn("30 jours", reponse["texte"])
        self.assertIn("A → B → G → H → K → L", reponse["texte"])
        self.assertTrue(reponse["donnees_utilisees"])
        self.assertEqual("moteur", reponse["source"])

    def test_criticite_tache_b(self):
        reponse = self.deterministe("Pourquoi la tâche B est-elle critique ?")

        self.assertIn("B", reponse["texte"])
        self.assertIn("marge", reponse["texte"])
        self.assertIn("0", reponse["texte"])
        self.assertIn("ES = 3", reponse["texte"])

    def test_simultanite_sans_dependance(self):
        reponse = self.deterministe(
            "Quelles tâches peuvent être réalisées simultanément en parallèle ?"
        )
        paires = re.findall(r"\b([A-Z])\s+et\s+([A-Z])\b", reponse["texte"])

        self.assertTrue(paires)
        for premier, deuxieme in paires:
            self.assertNotIn(premier, ia._ancetres(deuxieme, self.taches))
            self.assertNotIn(deuxieme, ia._ancetres(premier, self.taches))
        self.assertIn("pas une affectation de ressources", reponse["texte"])

    def test_retard_hypothetique_recalcule_par_le_moteur(self):
        reponse = self.deterministe(
            "quel serait l'effet d'un retard de 3 jours sur la tâche C ?"
        )
        modifiees = copy.deepcopy(self.taches)
        modifiees["C"]["duree"] += 3
        attendu = compute(modifiees).duree_totale
        self.assertEqual(31, attendu)

        trouve = re.search(
            r"à\s+(\d+(?:[.,]\d+)?)\s+jours calendaires", reponse["texte"]
        )
        self.assertIsNotNone(trouve)
        self.assertEqual(
            float(attendu), float(trouve.group(1).replace(",", "."))
        )
        self.assertEqual(TACHES_DEMO, self.taches)  # planning non modifié

    def test_code_tache_inexistant(self):
        reponse = self.deterministe("Pourquoi la tâche Z est-elle critique ?")

        self.assertTrue(reponse["donnees_manquantes"])
        self.assertIn("Z", reponse["texte"])
        self.assertIsNone(re.search(r"\d", reponse["texte"]))

    def test_contexte_sans_secret_et_avec_regles_securite(self):
        contexte = ia.construire_contexte(
            self.taches, self.resultats, self.duree,
            self.chemins, self.successeurs,
        )
        dump = json.dumps(contexte, ensure_ascii=False).lower()
        for cle_interdite in ("SECRET_KEY", "password", "token"):
            self.assertNotIn(cle_interdite, contexte)
            self.assertNotIn(cle_interdite.lower(), dump)
        self.assertIn("instructions_securite", contexte)
        self.assertIn("NON FIABLES", contexte["instructions_securite"])

        os.environ["IA_API_KEY"] = "valeur-tres-secrete"
        contexte2 = ia.construire_contexte(
            self.taches, self.resultats, self.duree,
            self.chemins, self.successeurs,
        )
        self.assertNotIn("valeur-tres-secrete", json.dumps(contexte2))

        longues = copy.deepcopy(self.taches)
        longues["A"]["description"] = "x" * 250
        tronque = ia.construire_contexte(
            longues, self.resultats, self.duree, self.chemins, self.successeurs
        )
        self.assertEqual(200, len(tronque["taches"][0]["description"]))

    def test_verification_des_nombres(self):
        contexte = ia.construire_contexte(
            self.taches, self.resultats, self.duree,
            self.chemins, self.successeurs,
        )
        self.assertIn("30", ia.nombres_autorises(contexte))
        self.assertFalse(ia.verifier_nombres("La durée est 999 jours", contexte))
        self.assertTrue(
            ia.verifier_nombres("La durée est de 30 jours calendaires.", contexte)
        )
        self.assertTrue(ia.verifier_nombres("La marge vaut 0 jour.", contexte))

    def test_fournisseur_avec_chiffre_non_verifiable(self):
        os.environ["IA_API_KEY"] = "cle-de-test"
        with patch.object(
            ia, "_appeler_fournisseur",
            return_value=("La durée est 999 jours.", None),
        ):
            reponse = self.repondre("Combien de temps ?")

        self.assertEqual("moteur", reponse["source"])
        self.assertIn(ia.AVERTISSEMENT_CHIFFRES, reponse["avertissements"])
        self.assertFalse(reponse["appel_ia"])

    def test_fournisseur_valide(self):
        os.environ["IA_API_KEY"] = "cle-de-test"
        reponse_ia = "La durée totale calculée est de 30 jours calendaires."
        with patch.object(
            ia, "_appeler_fournisseur", return_value=(reponse_ia, 42)
        ) as fournisseur:
            reponse = self.repondre("Combien de temps ?")

        self.assertEqual("ia", reponse["source"])
        self.assertEqual(reponse_ia, reponse["texte"])
        self.assertEqual(42, reponse["tokens"])
        self.assertTrue(reponse["appel_ia"])
        fournisseur.assert_called_once()

    def test_fournisseur_indisponible_sans_exception(self):
        os.environ["IA_API_KEY"] = "cle-de-test"
        with patch.object(
            ia, "_appeler_fournisseur",
            side_effect=ia.IADisponibiliteError("panne réseau"),
        ):
            reponse = self.repondre("Combien de temps ?")

        self.assertEqual("moteur", reponse["source"])
        self.assertIn(ia.AVERTISSEMENT_INDISPONIBLE, reponse["avertissements"])
        self.assertIn("30 jours", reponse["texte"])

    def test_historique_limite_a_six_tours(self):
        os.environ["IA_API_KEY"] = "cle-de-test"
        historique = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"Tour {i}"}
            for i in range(20)
        ]
        capture = {}
        corps = json.dumps({
            "choices": [{"message": {"content": "Réponse courte."}}],
            "usage": {"total_tokens": 5},
        }).encode("utf-8")

        def faux_urlopen(requete, timeout=None):
            capture["requete"] = requete
            capture["timeout"] = timeout
            return FausseReponse(corps)

        contexte = ia.construire_contexte(
            self.taches, self.resultats, self.duree,
            self.chemins, self.successeurs,
        )
        with patch.object(ia.urllib_request, "urlopen", side_effect=faux_urlopen):
            texte, tokens = ia._appeler_fournisseur(
                contexte, "Combien de temps ?", historique, ia.configuration()
            )

        self.assertEqual("Réponse courte.", texte)
        self.assertEqual(5, tokens)
        payload = json.loads(capture["requete"].data.decode("utf-8"))
        messages = payload["messages"]
        self.assertEqual(8, len(messages))  # système + 6 tours + question
        self.assertEqual("system", messages[0]["role"])
        self.assertEqual(
            [f"Tour {i}" for i in range(14, 20)],
            [message["content"] for message in messages[1:7]],
        )
        self.assertEqual("user", messages[-1]["role"])
        self.assertEqual(10, capture["timeout"])
        self.assertNotIn(
            "cle-de-test", json.dumps(payload, ensure_ascii=False)
        )

    def test_limite_d_usage(self):
        os.environ["IA_API_KEY"] = "cle-de-test"
        os.environ["IA_MAX_APPELS_PAR_HEURE"] = "1"
        ia._APPELS.clear()
        with patch.object(
            ia, "_appeler_fournisseur", return_value=("La durée vaut 30 jours.", 1)
        ):
            premier = self.repondre("Combien de temps ?")
            deuxieme = self.repondre("Combien de temps ?")

        self.assertEqual("ia", premier["source"])
        self.assertEqual("moteur", deuxieme["source"])
        self.assertTrue(
            any("Limite d'usage" in a for a in deuxieme["avertissements"])
        )

    def test_projet_vide_reponse_honnete(self):
        reponse = ia.repondre_deterministe(
            "Combien de temps ?", {}, {}, 0, [], {}
        )

        self.assertEqual("moteur", reponse["source"])
        self.assertTrue(reponse["donnees_manquantes"])
        self.assertIsNone(re.search(r"\d", reponse["texte"]))
        self.assertIn("aucune tâche", reponse["texte"])
        self.assertIn(ia.AVERTISSEMENT_MOTEUR, reponse["avertissements"])


if __name__ == "__main__":
    unittest.main()
