"""Tests de non-régression HTTP de l'application Flask PERT/CPM."""

import io
import time
import unittest
import uuid

from openpyxl import load_workbook

from app import app
from extensions import db
from models import User

app.config.update(TESTING=True)

PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42"
    "mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
PNG_DATA_URL = "data:image/png;base64," + PNG_BASE64

TACHES_VALIDES = "A;Analyse;2;\nB;Suite;3;A"
TACHES_CYCLE = "A;Analyse;1;B\nB;Suite;1;A"


def payload_export(taches_texte=TACHES_VALIDES, image_graphe=PNG_DATA_URL,
                   image_gantt=PNG_DATA_URL, **extra):
    """Construit un corps JSON valide pour ``/export_excel``."""
    payload = {
        "taches_texte": taches_texte,
        "date_debut": "2026-11-21",
        "image_graphe": image_graphe,
        "image_gantt": image_gantt,
    }
    payload.update(extra)
    return payload


def html_de(response):
    """Décode le HTML d'une réponse Flask pour les assertions de contenu."""
    return response.data.decode("utf-8")


class ApplicationHTTPTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def _creer_utilisateur_connecte(self):
        """Crée un compte éphémère et le connecte en POST clair.

        Les clients historiques envoient le mot de passe en clair : le champ
        chiffré est absent, le serveur accepte donc ce format. CSRF est neutralisé
        le temps du test (les tests HTTP d'auth utilisent ``TestConfig``).
        """
        identifiant = f"user_test_{uuid.uuid4().hex[:12]}"
        mot_de_passe = "Password123"

        csrf_initial = app.config["WTF_CSRF_ENABLED"]
        app.config["WTF_CSRF_ENABLED"] = False
        self.addCleanup(app.config.__setitem__, "WTF_CSRF_ENABLED", csrf_initial)

        # Le moteur SQLAlchemy peut avoir été remplacé par un autre module de
        # test : on garantit l'existence des tables sur le moteur courant.
        with app.app_context():
            db.create_all()

        reponse = self.client.post("/register", data={
            "username": identifiant,
            "email": f"{identifiant}@example.test",
            "password": mot_de_passe,
            "password_confirm": mot_de_passe,
        })
        self.assertIn(reponse.status_code, (200, 302))
        self.addCleanup(self._supprimer_utilisateur, identifiant)
        return identifiant

    @staticmethod
    def _supprimer_utilisateur(identifiant):
        with app.app_context():
            utilisateur = User.query.filter_by(username=identifiant).first()
            if utilisateur is not None:
                db.session.delete(utilisateur)
                db.session.commit()

    def test_pages_publiques(self):
        # L'accueil exige désormais une session : seul un anonyme est redirigé.
        for chemin in ("/demo", "/saisie", "/health"):
            with self.subTest(chemin=chemin):
                self.assertEqual(200, self.client.get(chemin).status_code)

        accueil = self.client.get("/")
        self.assertEqual(302, accueil.status_code)
        self.assertEqual("/login?next=%2F", accueil.headers.get("Location"))

        sante = self.client.get("/health")
        self.assertEqual({"status": "ok"}, sante.get_json())

    def test_accueil_anonyme_redirige_vers_login(self):
        reponse = self.client.get("/")
        self.assertEqual(302, reponse.status_code)
        self.assertEqual("/login?next=%2F", reponse.headers.get("Location"))

    def test_cle_publique_auth(self):
        reponse = self.client.get("/auth/public-key")
        self.assertEqual(200, reponse.status_code)

        donnees = reponse.get_json()
        self.assertIsInstance(donnees, dict)
        for cle in ("cle_publique", "algorithme", "champ", "format"):
            with self.subTest(cle=cle):
                self.assertIn(cle, donnees)

        self.assertIn("BEGIN PUBLIC KEY", donnees["cle_publique"])
        self.assertEqual("RSA-OAEP-SHA256", donnees["algorithme"])
        self.assertEqual("password", donnees["champ"])
        self.assertEqual("RSA-OAEP-SHA256:<base64url>", donnees["format"])
        self.assertEqual("public, max-age=3600", reponse.headers.get("Cache-Control"))

    def test_accueil_utilisateur_connecte(self):
        self._creer_utilisateur_connecte()
        self.assertEqual(200, self.client.get("/").status_code)

    def test_demo_reference(self):
        html = html_de(self.client.get("/demo"))
        for attendu in ("30 jours", "A → B → G → H → K → L",
                        "Marge libre", "Chemins critiques"):
            with self.subTest(attendu=attendu):
                self.assertIn(attendu, html)

    def test_saisie_valide(self):
        response = self.client.post("/resultats_custom", data={
            "taches": TACHES_VALIDES,
            "date_debut": "2026-11-21",
        })
        self.assertEqual(200, response.status_code)
        html = html_de(response)
        self.assertIn("5 jours", html)
        self.assertIn("A → B", html)

    def test_cycle_sans_blocage(self):
        debut = time.monotonic()
        response = self.client.post("/resultats_custom", data={
            "taches": TACHES_CYCLE,
            "date_debut": "2026-11-21",
        })
        duree = time.monotonic() - debut
        self.assertLess(duree, 2.0, "la détection de cycle ne doit pas bloquer")
        self.assertEqual(200, response.status_code)
        self.assertIn("circulaire", html_de(response))

    def test_duree_negative_et_predecesseur_inconnu(self):
        response = self.client.post("/resultats_custom", data={
            "taches": "A;Analyse;-2;",
            "date_debut": "2026-11-21",
        })
        self.assertEqual(200, response.status_code)
        self.assertIn("négat", html_de(response))

        response = self.client.post("/resultats_custom", data={
            "taches": "A;Analyse;2;\nB;Suite;3;Z",
            "date_debut": "2026-11-21",
        })
        self.assertEqual(200, response.status_code)
        html = html_de(response)
        # L'apostrophe de "n'existe pas" est échappée en HTML par Jinja.
        self.assertIn("Z", html)
        self.assertIn("existe pas", html)

    def test_export_excel_recalcule_cote_serveur(self):
        farfelu = 12345
        response = self.client.post("/export_excel", json=payload_export(
            resultats={
                "duree_totale": farfelu,
                "A": {"ES": farfelu, "EF": farfelu},
                "B": {"ES": farfelu, "EF": farfelu},
            },
        ))
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.data.startswith(b"PK"))

        wb = load_workbook(io.BytesIO(response.data))
        self.assertEqual(["Résultats", "Graphe PERT", "Diagramme Gantt"], wb.sheetnames)

        ws = wb["Résultats"]
        self.assertEqual(
            ["Tâche", "Description", "Durée", "ES", "EF", "LS", "LF",
             "Marge totale", "Marge libre", "Critique"],
            [cellule.value for cellule in ws[1]],
        )
        self.assertEqual("A", ws.cell(row=2, column=1).value)
        self.assertEqual(0, ws.cell(row=2, column=4).value)   # A : ES
        self.assertEqual(2, ws.cell(row=2, column=5).value)   # A : EF
        self.assertEqual("B", ws.cell(row=3, column=1).value)
        self.assertEqual(2, ws.cell(row=3, column=4).value)   # B : ES
        self.assertEqual(5, ws.cell(row=3, column=5).value)   # B : EF

        totales = [cellule.value for ligne in ws
                   for cellule in ligne
                   if isinstance(cellule.value, str)
                   and cellule.value.startswith("Durée totale du projet :")]
        self.assertEqual(["Durée totale du projet : 5 jours"], totales)
        self.assertFalse(any(str(farfelu) in str(cellule.value)
                             for ligne in ws for cellule in ligne))

    def test_export_injections_et_erreurs(self):
        # (a) Une description ressemblant à une formule reste du texte.
        response = self.client.post("/export_excel", json=payload_export(
            taches_texte="A;=1+1;2;",
        ))
        self.assertEqual(200, response.status_code)
        wb = load_workbook(io.BytesIO(response.data))
        cellule = wb["Résultats"].cell(row=2, column=2)
        self.assertEqual("s", cellule.data_type)
        self.assertIn("=1+1", str(cellule.value))

        # (b) Base64 corrompu.
        response = self.client.post("/export_excel", json=payload_export(
            image_graphe="data:image/png;base64,@@@!!!pas-du-base64@@@",
        ))
        self.assertEqual(400, response.status_code)
        self.assertIn("erreur", response.get_json())

        # (c) Cycle dans le graphe.
        response = self.client.post("/export_excel", json=payload_export(
            taches_texte=TACHES_CYCLE,
        ))
        self.assertEqual(400, response.status_code)
        self.assertIn("graphe de tâches est invalide", str(response.get_json()))

        # (d) Corps vide.
        response = self.client.post("/export_excel", json={})
        self.assertEqual(400, response.status_code)
        self.assertIn("erreur", response.get_json())

    def test_limite_taille(self):
        self.assertEqual(8 * 1024 * 1024, app.config["MAX_CONTENT_LENGTH"])


if __name__ == "__main__":
    unittest.main()
