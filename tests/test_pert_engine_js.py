"""Parité entre le moteur JS offline et le moteur Python canonique (js2py).

js2py est une dépendance de test optionnelle : elle n'est pas ajoutée à
requirements.txt et les tests sont ignorés proprement si elle est absente.
"""

import contextlib
import io
import pathlib
import random
import unittest

try:
    # js2py 0.74 imprime des octets de debug avant d'échouer sur un Python
    # dont le bytecode a changé (ex. 3.13) : on garde la sortie de test propre.
    with contextlib.redirect_stdout(io.StringIO()):
        import js2py
except Exception:  # absent, ou bytecode incompatible avec la version de Python
    js2py = None

from logic.data import taches as taches_demo
from logic.pert import ValidationError, compute as compute_python, validate as validate_python


SOURCE_JS = pathlib.Path(__file__).resolve().parents[1] / "static" / "pert-engine.js"


def t(duree, *depend_de):
    """Raccourci pour construire une tâche de test."""
    return {"duree": duree, "depend_de": list(depend_de)}


def graphes_aleatoires(nombre=50, graine=20240621):
    """Graphes acycliques déterministes, générés sans dépendance externe."""
    hasard = random.Random(graine)
    graphes = []
    for _ in range(nombre):
        taille = hasard.randint(1, 10)
        noms = ["T%02d" % i for i in range(taille)]
        taches = {}
        for i, nom in enumerate(noms):
            precedents = [p for p in noms[:i] if hasard.random() < 0.35]
            if len(precedents) > 3:
                precedents = hasard.sample(precedents, 3)
            duree = hasard.choice([0, 1, 2, 3, 5, 8, 0.5])
            taches[nom] = {"duree": duree, "depend_de": precedents}
        graphes.append(taches)
    return graphes


CAS_LIMITES = [
    ("une tâche", {"A": t(3)}),
    ("tâches indépendantes", {"A": t(2), "B": t(5), "C": t(1)}),
    ("durée nulle", {"A": t(0), "B": t(2, "A")}),
    ("jalons", {"A": t(0), "B": t(0, "A"), "C": t(4, "B"), "D": t(0, "C")}),
    ("plusieurs chemins critiques", {
        "A": t(2), "B": t(3, "A"), "C": t(3, "A"), "D": t(2, "B", "C"),
    }),
    ("graphe diamant", {
        "A": t(2), "B": t(5, "A"), "C": t(3, "A"), "D": t(1, "B", "C"),
    }),
    ("multi sources et multi puits", {
        "A": t(2), "X": t(2), "B": t(3, "A"), "C": t(3, "X"),
        "D": t(1, "B"), "E": t(1, "C"),
    }),
    ("dépendances dupliquées", {"A": t(1), "B": t(2, "A", "A"), "C": t(1, "A")}),
    ("durées décimales et epsilon", {
        "A": t(0.1), "B": t(0.2, "A"), "C": t(0.3, "A"), "D": t(0.1, "B", "C"),
    }),
    ("graphe vide", {}),
]

CAS_INVALIDES = [
    ("cycle", {"A": t(1, "B"), "B": t(1, "A")}),
    ("cycle avec tâche en aval", {
        "A": t(1, "C"), "B": t(1, "A"), "C": t(1, "B"), "D": t(1, "A"),
    }),
    ("auto-dépendance", {"A": t(1, "A")}),
    ("durée négative", {"A": t(-1), "B": t(2, "A")}),
    ("prédécesseur inexistant", {"A": t(1, "Z"), "B": t(2, "A")}),
    ("identifiant vide", {"": t(1)}),
    ("identifiants en double", {"A": t(1), "A ": t(2)}),
    ("durée booléenne", {"A": {"duree": True, "depend_de": []}}),
    ("durée chaîne", {"A": {"duree": "3", "depend_de": []}}),
    ("durée absente", {"A": {"depend_de": []}}),
    ("durée NaN", {"A": {"duree": float("nan"), "depend_de": []}}),
    ("durée infinie", {"A": {"duree": float("inf"), "depend_de": []}}),
    ("prédécesseurs chaîne", {"A": {"duree": 1, "depend_de": "B"}}),
    ("prédécesseur vide", {"A": t(1, "")}),
    ("informations invalides", {"A": 3}),
    ("graphe non dictionnaire", [1, 2]),
]


def _messages_js(valeur):
    """Convertit un tableau JS en liste Python de messages."""
    if hasattr(valeur, "to_list"):
        return valeur.to_list()
    return list(valeur)


def _resultat_js(valeur):
    """Convertit l'objet JS de résultat en dictionnaire Python natif."""
    if hasattr(valeur, "to_dict"):
        return valeur.to_dict()
    return valeur


def _message_exception_js(exception):
    texte = str(exception)
    return texte[len("Error: "):] if texte.startswith("Error: ") else texte


@unittest.skipUnless(js2py is not None,
                     "js2py non disponible (dépendance de test optionnelle)")
class PertEngineJsParityTestCase(unittest.TestCase):
    """Le moteur JS doit produire exactement les mêmes chiffres que logic/pert."""

    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_JS.read_text(encoding="utf-8")
        contexte = js2py.EvalJs()
        contexte.execute("var window = {};")
        contexte.execute(cls.source)
        # js2py.to_dict() trie les clés : la lecture via Object.keys()
        # permet de vérifier l'ordre déterministe réel du moteur JS.
        contexte.execute(
            "var _clesResultats = function (r) {"
            " return Object.keys(r.resultats); };"
        )
        cls.contexte = contexte
        cls.moteur = contexte.window.PertEngine
        cls.aleatoires = graphes_aleatoires()

    def verifier_parite(self, taches):
        """Compare compute() JS et Python champ par champ."""
        attendu = compute_python(taches)
        brut = self.moteur.compute(taches)
        obtenu = _resultat_js(brut)

        self.assertEqual(attendu.duree_totale, obtenu["duree_totale"])
        self.assertEqual(list(attendu.resultats),
                         _messages_js(self.contexte._clesResultats(brut)))
        for nom, valeurs in attendu.resultats.items():
            self.assertEqual(valeurs, obtenu["resultats"][nom], nom)
        self.assertEqual(attendu.chemins_critiques, obtenu["chemins_critiques"])
        self.assertEqual(attendu.successeurs, obtenu["successeurs"])
        self.assertEqual(attendu.ordre, obtenu["ordre"])
        self.assertEqual([], validate_python(taches))
        self.assertEqual([], _messages_js(self.moteur.validate(taches)))
        return obtenu

    def test_demonstration_valeurs_certifiees(self):
        obtenu = self.verifier_parite(taches_demo)
        self.assertEqual(30, obtenu["duree_totale"])
        self.assertEqual([["A", "B", "G", "H", "K", "L"]],
                         obtenu["chemins_critiques"])

    def test_cas_limites(self):
        for intitule, taches in CAS_LIMITES:
            with self.subTest(cas=intitule):
                self.verifier_parite(taches)

    def test_graphes_aleatoires_deterministes(self):
        self.assertGreaterEqual(len(self.aleatoires), 50)
        for index, taches in enumerate(self.aleatoires):
            with self.subTest(graphe=index):
                self.verifier_parite(taches)

    def test_validate_messages_invalides(self):
        for intitule, taches in CAS_INVALIDES:
            with self.subTest(cas=intitule):
                attendu = validate_python(taches)
                obtenu = _messages_js(self.moteur.validate(taches))
                self.assertTrue(attendu, intitule)
                self.assertEqual(attendu, obtenu)

    def test_compute_leve_erreur_sur_graphe_invalide(self):
        for intitule, taches in CAS_INVALIDES:
            with self.subTest(cas=intitule):
                with self.assertRaises(ValidationError) as py_erreur:
                    compute_python(taches)
                with self.assertRaises(Exception) as js_erreur:
                    self.moteur.compute(taches)
                self.assertEqual(str(py_erreur.exception),
                                 _message_exception_js(js_erreur.exception))

    def test_api_globale_et_commonjs(self):
        self.assertIn("validate", self.source)
        self.assertNotIn("=>", self.source)
        self.assertNotRegex(self.source, r"\b(?:const|let)\s")

        contexte = js2py.EvalJs()
        contexte.execute("var window = {}; var module = {exports: {}};")
        contexte.execute(self.source)
        self.assertTrue(contexte.eval(
            "typeof window.PertEngine.compute === 'function'"))
        self.assertTrue(contexte.eval(
            "typeof window.PertEngine.validate === 'function'"))
        self.assertTrue(contexte.eval(
            "module.exports === window.PertEngine"))


if __name__ == "__main__":
    unittest.main()
