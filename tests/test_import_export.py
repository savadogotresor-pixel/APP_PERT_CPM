"""Tests unitaires du service d'import/export tabulaire (unittest)."""

import io
import unittest

from openpyxl import Workbook, load_workbook

from logic.data import parser_taches
from logic.pert import compute
from services.import_export import (
    MAX_OCTETS,
    exporter_csv,
    exporter_xlsx,
    importer_fichier,
)


class ImportExportTests(unittest.TestCase):
    def assert_import_valide(self, donnees, nom_fichier):
        texte, erreurs = importer_fichier(donnees, nom_fichier)
        self.assertEqual(erreurs, [])
        taches, erreurs_parser = parser_taches(texte)
        self.assertEqual(erreurs_parser, [])
        compute(taches)  # le texte doit être directement calculable
        return taches

    @staticmethod
    def classeur_csv(lignes):
        return "\n".join(lignes).encode("utf-8")

    def test_import_csv_separateur_virgule(self):
        contenu = self.classeur_csv([
            "code,description,duree,predecesseurs",
            "A,Analyse,3,",
            'B,Conception,4,"A"',
            "C,Tests,2,\"A,B\"",
        ])
        taches = self.assert_import_valide(contenu, "plan.csv")
        self.assertEqual(set(taches), {"A", "B", "C"})
        self.assertEqual(taches["C"]["depend_de"], ["A", "B"])

    def test_import_csv_separateur_point_virgule(self):
        contenu = self.classeur_csv([
            "code;description;duree;predecesseurs",
            "A;Analyse;3;",
            "B;Conception;4;A",
            "C;Tests;2;A;B",
        ])
        taches = self.assert_import_valide(contenu + b"\n", "plan.csv")
        self.assertEqual(taches["B"]["depend_de"], ["A"])
        self.assertEqual(taches["C"]["depend_de"], ["A", "B"])

    def test_import_csv_sans_en_tetes_et_predecesseurs_espaces(self):
        contenu = self.classeur_csv([
            "A;Analyse;3",
            "B;Conception;4;A",
            "C;Tests;2;A B",
            "",
        ])
        taches = self.assert_import_valide(contenu, "plan.csv")
        self.assertEqual(taches["C"]["depend_de"], ["A", "B"])

    def test_import_xlsx_avec_en_tetes(self):
        classeur = Workbook()
        feuille = classeur.active
        feuille.append(["Code", "Description", "Durée", "Prédécesseurs"])
        feuille.append(["A", "Analyse", 3, ""])
        feuille.append(["B", "Conception", 4, "A"])
        feuille.append(["C", "Tests", 2, "A;B"])
        tampon = io.BytesIO()
        classeur.save(tampon)

        taches = self.assert_import_valide(tampon.getvalue(), "plan.xlsx")
        self.assertEqual(taches["A"]["duree"], 3)
        self.assertEqual(taches["C"]["depend_de"], ["A", "B"])

    def test_import_xlsx_sans_en_tetes(self):
        classeur = Workbook()
        feuille = classeur.active
        feuille.append(["A", "Analyse", 3])
        feuille.append(["B", "Conception", 4, "A"])
        tampon = io.BytesIO()
        classeur.save(tampon)

        taches = self.assert_import_valide(tampon.getvalue(), "plan.xlsx")
        self.assertEqual(set(taches), {"A", "B"})
        self.assertEqual(taches["B"]["depend_de"], ["A"])

    def test_import_xlsx_premiere_feuille_uniquement(self):
        classeur = Workbook()
        classeur.active.title = "Utile"
        classeur.active.append(["A", "Analyse", 2])
        classeur.create_sheet("Ignoree").append(["Z", "Ignorée", 9])
        tampon = io.BytesIO()
        classeur.save(tampon)

        taches = self.assert_import_valide(tampon.getvalue(), "plan.xlsx")
        self.assertEqual(set(taches), {"A"})

    def test_erreur_duree_invalide(self):
        texte, erreurs = importer_fichier(
            self.classeur_csv(["A;Analyse;1.5"]), "plan.csv"
        )
        self.assertEqual(texte, "")
        self.assertTrue(any("Ligne 1" in e and "durée" in e for e in erreurs))

    def test_erreur_duree_negative(self):
        texte, erreurs = importer_fichier(
            self.classeur_csv(["A;Analyse;-2"]), "plan.csv"
        )
        self.assertEqual(texte, "")
        self.assertTrue(
            any("Ligne 1" in e and "négative" in e for e in erreurs)
        )

    def test_erreur_doublon(self):
        texte, erreurs = importer_fichier(
            self.classeur_csv(["A;Analyse;3", "A;Autre;2"]), "plan.csv"
        )
        self.assertTrue(any("Ligne 2" in e and "double" in e for e in erreurs))
        self.assertEqual(texte, "A;Analyse;3;")

    def test_erreur_predecesseur_vide(self):
        texte, erreurs = importer_fichier(
            self.classeur_csv(["A;Analyse;3", "B;Conception;2;A;;C"]),
            "plan.csv",
        )
        self.assertTrue(
            any("Ligne 2" in e and "vide" in e for e in erreurs)
        )

    def test_erreur_fichier_inconnu(self):
        texte, erreurs = importer_fichier(b"contenu", "plan.txt")
        self.assertEqual(texte, "")
        self.assertEqual(len(erreurs), 1)
        self.assertIn("Ligne 1", erreurs[0])
        self.assertIn("format", erreurs[0])

    def test_erreur_fichier_trop_volumineux(self):
        texte, erreurs = importer_fichier(
            b"x" * (MAX_OCTETS + 1), "plan.csv"
        )
        self.assertEqual(texte, "")
        self.assertEqual(len(erreurs), 1)
        self.assertIn("Ligne 1", erreurs[0])
        self.assertIn("volumineux", erreurs[0])

    def test_erreur_xlsx_invalide_sans_exception(self):
        texte, erreurs = importer_fichier(b"ceci n'est pas un xlsx", "plan.xlsx")
        self.assertEqual(texte, "")
        self.assertTrue(erreurs)
        self.assertIn("Ligne 1", erreurs[0])

    def test_export_csv_bom_colonnes_et_formule(self):
        taches = {"A": {"description": "=1+1", "duree": 2, "depend_de": []}}
        resultats = {
            "A": {
                "ES": 0, "EF": 2, "LS": 0, "LF": 2,
                "marge_totale": 0, "marge_libre": 0, "critique": True,
            }
        }
        donnees = exporter_csv(taches, resultats)

        self.assertTrue(donnees.startswith(b"\xef\xbb\xbf"))
        lignes = donnees.decode("utf-8-sig").splitlines()
        self.assertEqual(
            lignes[0],
            "Tache;Description;Duree;ES;EF;LS;LF;Marge totale;Marge libre;Critique",
        )
        self.assertIn("'=1+1;2;0;2;0;2;0;0;OUI", lignes[1])

    def test_export_xlsx_onglet_duree_et_cellule_critique_texte(self):
        taches = {"A": {"description": "=1+1", "duree": 2, "depend_de": []}}
        resultats = {
            "A": {
                "ES": 0, "EF": 2, "LS": 0, "LF": 2,
                "marge_totale": 0, "marge_libre": 0, "critique": True,
            }
        }
        donnees = exporter_xlsx(taches, resultats, 2, [["A"]])

        classeur = load_workbook(io.BytesIO(donnees))
        self.assertIn("Résultats", classeur.sheetnames)
        feuille = classeur["Résultats"]
        self.assertTrue(any(
            cellule.value is not None
            and "Durée totale du projet : 2" in str(cellule.value)
            for ligne in feuille.iter_rows() for cellule in ligne
        ))
        cellule_critique = feuille.cell(row=2, column=10)
        self.assertEqual(cellule_critique.value, "OUI")
        self.assertEqual(cellule_critique.data_type, "s")
        cellule_formule = feuille.cell(row=2, column=2)
        self.assertEqual(cellule_formule.value, "=1+1")
        self.assertEqual(cellule_formule.data_type, "s")


if __name__ == "__main__":
    unittest.main()
