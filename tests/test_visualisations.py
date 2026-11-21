"""Tests de non-régression des visualisations du lot 4 (graphe, Gantt, fiches).

Ces tests vérifient le contrat du template ``resultats.html`` : présence des
zones de la fiche tâche, des contrôles Gantt, des deux constantes JSON injectées
(``FICHES_TACHES`` / ``ARCS_CRITIQUES``), des styles des arcs critiques et des
scripts d'export historiques.
"""

import json
import re
import unittest
from urllib.parse import urlparse

from app import app

app.config.update(TESTING=True)

DOMAINES_SCRIPTS_AUTORISES = {
    "unpkg.com",
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
}

ZONES_FICHE = (
    "fiche-code",
    "fiche-description",
    "fiche-duree",
    "fiche-dates",
    "fiche-marges",
    "fiche-criticite",
    "fiche-predecesseurs",
)

VUES_GANTT = ("Day", "Week", "Month", "Quarter")

IDS_EXPORT = ("contenu-export", "graphe", "gantt", "gantt-container")

ARCS_DEMO = ["A→B", "B→G", "G→H", "H→K", "K→L"]

CHEMINS_CUSTOM = (
    "A;Analyse;2;\n"
    "B;Branche B;3;A\n"
    "C;Branche C;3;A\n"
    "D;Final;2;B,C"
)

TACHES_CUSTOM_SIMPLE = "A;Test;2;\nB;Suite;3;A"


def html_de(response):
    """Décode le HTML d'une réponse Flask pour les assertions de contenu."""
    return response.data.decode("utf-8")


def constante_json(html, nom):
    """Extrait et désérialise ``const NOM = ...;`` depuis un HTML rendu.

    Jinja émet la valeur ``tojson`` sur une seule ligne : on accepte des
    espaces libre autour de ``=`` et on s'ancre sur la fin de ligne pour ne pas
    couper le JSON au premier ``;`` rencontré dans une chaîne.
    """
    motif = rf"const\s+{re.escape(nom)}\s*=\s*(.*?)\s*;\s*$"
    resultat = re.search(motif, html, flags=re.MULTILINE | re.DOTALL)
    if resultat is None:
        raise AssertionError(f"const {nom} introuvable dans le HTML rendu")
    try:
        return json.loads(resultat.group(1))
    except json.JSONDecodeError as exc:
        raise AssertionError(f"JSON invalide pour const {nom} : {exc}") from exc


class VisualisationsTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_elements_present_demo(self):
        """La démo expose toute la structure HTML attendue par le lot 4."""
        reponse = self.client.get("/demo")
        self.assertEqual(200, reponse.status_code)
        html = html_de(reponse)

        attendus = [
            'id="fiche-tache"',
            'id="fiche-fermer"',
            'class="controles-gantt"',
            'id="btn-actualiser"',
            "const ARCS_CRITIQUES",
            "const FICHES_TACHES",
            "const reseau",
            "const gantt",
            "exporterPDF",
            "exporterExcel",
        ]
        attendus.extend(f'id="{zone}"' for zone in ZONES_FICHE)
        attendus.extend(f'data-vue="{vue}"' for vue in VUES_GANTT)
        attendus.extend(f'id="{ident}"' for ident in IDS_EXPORT)
        attendus.extend(("30 jours", "A → B → G → H → K → L"))

        for valeur in attendus:
            with self.subTest(valeur=valeur):
                self.assertIn(valeur, html)

    def test_arcs_critiques_demo(self):
        """Les arcs des chemins critiques de la démo sont tous exportés."""
        html = html_de(self.client.get("/demo"))
        arcs = constante_json(html, "ARCS_CRITIQUES")

        self.assertIsInstance(arcs, list)
        self.assertEqual(ARCS_DEMO, arcs)

    def test_fiches_taches_demo(self):
        """Les 12 fiches de la démo contiennent bien les données PERT/CPM."""
        html = html_de(self.client.get("/demo"))
        fiches = constante_json(html, "FICHES_TACHES")

        self.assertIsInstance(fiches, dict)
        self.assertEqual(set("ABCDEFGHIJKL"), set(fiches))
        self.assertEqual(12, len(fiches))

        champs = {
            "description", "duree", "ES", "EF", "LS", "LF",
            "marge", "marge_libre", "critique", "predecesseurs",
        }
        for code, fiche in fiches.items():
            with self.subTest(code=code):
                self.assertEqual(champs, set(fiche))

        a = fiches["A"]
        # Écart de données signalé dans le rapport : le jeu de démo fournit
        # A avec une durée de 3 j (donc EF=3), pas 2 j comme annoncé dans
        # l'énoncé du contrat. On vérifie la cohérence interne réelle.
        self.assertEqual(3, a["duree"])
        self.assertEqual(0, a["ES"])
        self.assertEqual(3, a["EF"])
        self.assertEqual(0, a["marge_libre"])
        self.assertTrue(a["critique"])
        self.assertTrue(a["description"].strip())
        self.assertEqual([], a["predecesseurs"])

        self.assertEqual(["A"], fiches["B"]["predecesseurs"])

        non_critiques = [code for code, f in fiches.items() if not f["critique"]]
        self.assertTrue(non_critiques, "la démo doit comporter des tâches non critiques")
        for code in non_critiques:
            with self.subTest(code=code):
                self.assertIs(fiches[code]["critique"], False)

    def test_styles_arcs_et_securite_fiche(self):
        """Arcs critiques stylés, clic câblé, remplissage sans innerHTML."""
        html = html_de(self.client.get("/demo"))

        for valeur in (
            "'#ef4444'",
            "'#9ca3af'",
            "ARCS_CRITIQUES.includes(",
            "reseau.on('click'",
            "textContent",
        ):
            with self.subTest(valeur=valeur):
                self.assertIn(valeur, html)

        self.assertRegex(
            html,
            r"width\s*:\s*[\w$.]+\s*\?\s*3\s*:\s*1",
            "la largeur doit valoir 3 pour un arc critique, 1 sinon",
        )

        # Aucune affectation innerHTML sur la fiche ou l'une de ses zones.
        affectation_dangereuse = re.compile(
            r"(?:fiche\w*"
            r"|document\s*\.\s*getElementById\(\s*['\"]fiche-[^'\"]+['\"]\s*\)"
            r"|querySelector\(\s*['\"]#fiche-[^'\"]+['\"]\s*\))"
            r"\s*\.\s*innerHTML\s*=",
            re.IGNORECASE,
        )
        self.assertIsNone(
            affectation_dangereuse.search(html),
            "les données de la fiche doivent être injectées via textContent",
        )

    def test_multi_chemins_critiques(self):
        """Deux chemins critiques produisent quatre arcs et l'indication '(2)'."""
        reponse = self.client.post("/resultats_custom", data={"taches": CHEMINS_CUSTOM})
        self.assertEqual(200, reponse.status_code)
        html = html_de(reponse)

        arcs = constante_json(html, "ARCS_CRITIQUES")
        for arc in ("A→B", "A→C", "B→D", "C→D"):
            with self.subTest(arc=arc):
                self.assertIn(arc, arcs)

        self.assertIn("Chemins critiques (2)", html)

    def test_pas_de_nouvelle_dependance_externe(self):
        """Les scripts externes restent limités aux CDN déjà autorisés."""
        html = html_de(self.client.get("/demo"))
        urls = re.findall(
            r"<script\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"]",
            html,
            flags=re.IGNORECASE,
        )
        self.assertTrue(urls, "au moins un script externe est attendu")

        for url in urls:
            with self.subTest(url=url):
                hote = (urlparse(url).hostname or "").lower()
                if hote:  # une URL relative reste locale
                    self.assertIn(hote, DOMAINES_SCRIPTS_AUTORISES)

    def test_saisie_custom_fiches_coherentes(self):
        """Une saisie simple alimente correctement fiches et arcs critiques."""
        reponse = self.client.post(
            "/resultats_custom", data={"taches": TACHES_CUSTOM_SIMPLE}
        )
        self.assertEqual(200, reponse.status_code)
        html = html_de(reponse)

        fiches = constante_json(html, "FICHES_TACHES")
        self.assertEqual({"A", "B"}, set(fiches))
        self.assertEqual(0, fiches["A"]["ES"])
        self.assertEqual(2, fiches["A"]["EF"])
        self.assertEqual(["A"], fiches["B"]["predecesseurs"])
        self.assertEqual(["A→B"], constante_json(html, "ARCS_CRITIQUES"))


if __name__ == "__main__":
    unittest.main()
