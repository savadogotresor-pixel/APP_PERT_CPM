"""Tests des visualisations avancées générées dans le navigateur.

Vérifient le contrat du lot « graphiques » :
- les 8 blocs <script> historiques de resultats.html restent OCTET POUR OCTET
  identiques (hash SHA-256 calculés sur la version avant ajout) ;
- la section « Representations avancées » expose 5 cases à cocher et un bouton
  de génération, sans modifier les IDs critiques ;
- le JSON injecté (taches / resultats / chemins) est celui du moteur canonique ;
- les deux fichiers statiques existent et n'introduisent aucune dépendance ;
- design.css reste la dernière feuille de style chargée.
"""

import hashlib
import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

from app import app
from logic.data import taches as taches_demo
from logic.pert import compute

app.config.update(TESTING=True)

RACINE = Path(__file__).resolve().parent.parent
TEMPLATE_RESULTATS = RACINE / 'templates' / 'resultats.html'
DOSSIER_TEMPLATES = RACINE / 'templates'
DOSSIER_STATIC = RACINE / 'static'

# Hash des 8 blocs <script> historiques, calculés sur templates/resultats.html
# AVANT l'ajout des visualisations (commit de référence 4d0592e).
HASH_SCRIPTS_HISTORIQUES = (
    'ca3eeb4f31f51a9a12740f7e326b16cbbce22c29647146d818d2da4934bf8d8b',
    '6d409ebca7e880111663212c6785f55fd4995b9c8804384f25e07e915bdddf1d',
    '390d901b880263eb64e9ebddd246cb1e50148252ec7305764ede3803817f559e',
    '6deac6210ef89847dbf00782050fd1ef279fb56a8d2b1000e48ac3979076e7f5',
    '11ecf9020cbdbd1187c126958578f068afb96302cee9c20ffd3edc32177c041b',
    'dd7c082b906f53e67131fb1f449c62049c281eddbb3be80ce01bab48629c2058',
    '8c23357debbb88d511a94d4b65928f501d1ee0f2ed32f885d3f1c6f80f43ad86',
    'd25ea5e0ea8edcde8536d4d5eebc9f103e1e352e47d8e87263b34fbf0562482b',
)

CLES_GRAPHIQUES = ('durees', 'marges', 'criticite', 'nuage', 'calendrier')

IDS_CRITIQUES = (
    'contenu-export', 'graphe', 'gantt', 'gantt-container',
    'fiche-tache', 'fiche-fermer', 'fiche-code', 'fiche-description',
    'fiche-duree', 'fiche-dates', 'fiche-marges', 'fiche-criticite',
    'fiche-predecesseurs',
)

DOMAINES_SCRIPTS_AUTORISES = {
    'unpkg.com', 'cdn.jsdelivr.net', 'cdnjs.cloudflare.com',
}

TACHES_CUSTOM_SIMPLE = 'A;Test;2;\nB;Suite;3;A'


def html_de(response):
    """Décode le HTML d'une réponse Flask."""
    return response.data.decode('utf-8')


def constante_json(html, nom):
    """Extrait et désérialise ``const NOM = ...;`` dans un HTML rendu."""
    motif = rf"const\s+{re.escape(nom)}\s*=\s*(.*?)\s*;\s*$"
    resultat = re.search(motif, html, flags=re.MULTILINE | re.DOTALL)
    if resultat is None:
        raise AssertionError(f"const {nom} introuvable dans le HTML rendu")
    try:
        return json.loads(resultat.group(1))
    except json.JSONDecodeError as exc:
        raise AssertionError(f"JSON invalide pour const {nom} : {exc}") from exc


def blocs_scripts(source):
    """Retourne tous les blocs <script ...>...</script> d'une source HTML."""
    return [
        correspondance.group(0)
        for correspondance in re.finditer(
            rb'<script\b[^>]*>.*?</script\s*>',
            source,
            flags=re.IGNORECASE | re.DOTALL,
        )
    ]


class GraphiquesTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    # ------------------------------------------------------------------
    # Non-régression des scripts existants
    # ------------------------------------------------------------------

    def test_scripts_historiques_octet_pour_octet(self):
        """Les 8 scripts historiques sont inchangés ; 2 blocs sont ajoutés."""
        source = TEMPLATE_RESULTATS.read_bytes()
        blocs = blocs_scripts(source)

        self.assertEqual(
            8 + 2, len(blocs),
            "8 scripts historiques + 1 injection + 1 chargement JS attendus"
        )
        for index, reference in enumerate(HASH_SCRIPTS_HISTORIQUES):
            with self.subTest(script=index + 1):
                empreinte = hashlib.sha256(blocs[index]).hexdigest()
                self.assertEqual(
                    reference, empreinte,
                    f"le bloc <script> historique n°{index + 1} a été modifié"
                )

        self.assertIn(b'DONNEES_GRAPHiques', blocs[-2])
        self.assertIn(b'graphiques.js', blocs[-1])

    def test_ids_critiques_toujours_presents(self):
        """Aucun ID historique de la page de résultats n'a disparu."""
        html = html_de(self.client.get('/demo'))
        for ident in IDS_CRITIQUES:
            with self.subTest(id=ident):
                self.assertIn(f'id="{ident}"', html)

        # Autres IDs critiques du projet, situés dans d'autres templates.
        source_templates = ''.join(
            chemin.read_text(encoding='utf-8')
            for chemin in DOSSIER_TEMPLATES.rglob('*.html')
        )
        for motif in (r'id="confirmer"', r'id="duree_[^"]+"'):
            with self.subTest(motif=motif):
                self.assertIsNotNone(re.search(motif, source_templates))

    # ------------------------------------------------------------------
    # Contrôles de la page
    # ------------------------------------------------------------------

    def test_section_et_controles_presents(self):
        """La section, le bouton et les 5 cases sont rendus sur /demo."""
        html = html_de(self.client.get('/demo'))
        attendus = [
            'id="visualisations-avancees"',
            '📈 Representations avancées',
            'id="btn-generer-graphiques"',
            'Générer les visualisations',
            'Graphiques.genererTous()',
            'id="graphiques-resultats"',
            'id="graphiques-invite"',
            'aria-live="polite"',
            'graphiques.css',
            '/static/graphiques.js',
        ]
        for valeur in attendus:
            with self.subTest(valeur=valeur):
                self.assertIn(valeur, html)

        for cle in CLES_GRAPHIQUES:
            with self.subTest(graphique=cle):
                self.assertIn(f'id="graphique-{cle}"', html)
                self.assertIn(f'data-graphique="{cle}"', html)
                self.assertIn(f'for="graphique-{cle}"', html)

        self.assertEqual(5, html.count('data-graphique='))
        self.assertEqual(5, html.count('checked>'))

    def test_design_css_reste_dernier(self):
        """graphiques.css est chargé avant design.css, qui reste le dernier."""
        html = html_de(self.client.get('/demo'))
        feuilles = re.findall(
            r'<link[^>]+rel="stylesheet"[^>]*>', html.split('</head>')[0]
        )
        self.assertTrue(feuilles)
        self.assertIn('design.css', feuilles[-1])
        index_graphiques = next(
            i for i, feuille in enumerate(feuilles) if 'graphiques.css' in feuille
        )
        self.assertLess(index_graphiques, len(feuilles) - 1)

    def test_aucune_nouvelle_dependance_externe(self):
        """Le seul script ajouté est local ; les CDN historiques sont inchangés."""
        html = html_de(self.client.get('/demo'))
        urls = re.findall(
            r'<script\b[^>]*\bsrc\s*=\s*[\'"]([^\'"]+)[\'"]',
            html,
            flags=re.IGNORECASE,
        )
        self.assertIn('/static/graphiques.js', urls)
        for url in urls:
            with self.subTest(url=url):
                hote = (urlparse(url).hostname or '').lower()
                if hote:
                    self.assertIn(hote, DOMAINES_SCRIPTS_AUTORISES)

    # ------------------------------------------------------------------
    # Données injectées
    # ------------------------------------------------------------------

    def test_json_injecte_coherent_avec_la_demo(self):
        """12 tâches, durée 30, et les valeurs du moteur canonique."""
        html = html_de(self.client.get('/demo'))
        donnees = constante_json(html, 'DONNEES_GRAPHiques')

        self.assertEqual({'taches', 'resultats', 'chemins'}, set(donnees))
        self.assertEqual(12, len(donnees['taches']))
        self.assertEqual(set('ABCDEFGHIJKL'), set(donnees['taches']))
        self.assertEqual(12, len(donnees['resultats']))

        duree_totale = max(r['EF'] for r in donnees['resultats'].values())
        self.assertEqual(30, duree_totale)
        self.assertEqual(
            ['A', 'B', 'G', 'H', 'K', 'L'], donnees['chemins'][0]
        )

        resultat = compute(taches_demo)
        self.assertEqual(resultat.chemins_critiques, donnees['chemins'])
        for nom, r in resultat.resultats.items():
            with self.subTest(tache=nom):
                injecte = donnees['resultats'][nom]
                for champ in ('ES', 'EF', 'LS', 'LF', 'marge', 'marge_libre'):
                    self.assertEqual(r[champ], injecte[champ])
                self.assertIs(r['critique'], injecte['critique'])
                self.assertEqual(taches_demo[nom]['duree'],
                                 donnees['taches'][nom]['duree'])

    def test_json_injecte_suit_une_saisie_personnalisee(self):
        """Les graphiques reflètent aussi les tâches saisies par l'utilisateur."""
        response = self.client.post(
            '/resultats_custom', data={'taches': TACHES_CUSTOM_SIMPLE}
        )
        self.assertEqual(200, response.status_code)
        donnees = constante_json(html_de(response), 'DONNEES_GRAPHiques')

        self.assertEqual({'A', 'B'}, set(donnees['taches']))
        self.assertEqual(0, donnees['resultats']['A']['ES'])
        self.assertEqual(2, donnees['resultats']['A']['EF'])
        self.assertEqual(5, max(r['EF'] for r in donnees['resultats'].values()))

    # ------------------------------------------------------------------
    # Fichiers statiques
    # ------------------------------------------------------------------

    def test_fichiers_statiques_presents_sans_dependance(self):
        """Le JS est natif (SVG) et expose les deux fonctions de l'API."""
        js = (DOSSIER_STATIC / 'graphiques.js').read_text(encoding='utf-8')
        css = (DOSSIER_STATIC / 'graphiques.css').read_text(encoding='utf-8')

        for attendu in (
            'window.Graphiques', 'genererTous', 'generer:',
            'createElementNS', "SVG_NS = 'http://www.w3.org/2000/svg'",
            'role', 'aria-labelledby', 'graphique-vide',
        ):
            with self.subTest(js=attendu):
                self.assertIn(attendu, js)
        for cle in CLES_GRAPHIQUES:
            with self.subTest(js=cle):
                self.assertIn(cle, js)
        for absent in ('https://', 'unpkg', 'cdn.jsdelivr', 'cdnjs', '<script'):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, js)
        self.assertIsNone(
            re.search(r'\.innerHTML\s*=', js),
            "les textes doivent être injectés via createTextNode/textContent"
        )

        for attendu in (
            '.graphiques-grid', '.graphique-carte', '.graphique-vide',
            'prefers-reduced-motion', '@media', 'grid-template-columns',
        ):
            with self.subTest(css=attendu):
                self.assertIn(attendu, css)
        for absent in ('#graphe', '#gantt', '#contenu-export'):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, css)


if __name__ == '__main__':
    unittest.main()
