"""Vérifications du design responsive PC / tablette / Android.

Aucun navigateur n'est nécessaire : on contrôle les invariants qui garantissent
le rendu multi-appareils (viewport, ordre des feuilles de style, breakpoints,
cibles tactiles, absence de dépendance Bootstrap distante).
"""

import os
import re
import unittest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(RACINE, 'templates')
DESIGN_CSS = os.path.join(RACINE, 'static', 'design.css')


def _fichiers_gabarit():
    """Gabarits complets (les partiels commencent par ``_`` sont ignorés)."""
    resultats = []
    for dossier, _, fichiers in os.walk(TEMPLATES):
        for nom in fichiers:
            if nom.endswith('.html') and not nom.startswith('_'):
                resultats.append(os.path.join(dossier, nom))
    return sorted(resultats)


class ResponsiveTestCase(unittest.TestCase):
    def test_tous_les_gabarits_ont_le_viewport_mobile(self):
        gabarits = _fichiers_gabarit()
        self.assertGreater(len(gabarits), 20)
        for chemin in gabarits:
            with self.subTest(gabarit=os.path.relpath(chemin, RACINE)):
                contenu = open(chemin, encoding='utf-8').read()
                self.assertRegex(
                    contenu,
                    r'<meta\s+name="viewport"\s+content="[^"]*width=device-width',
                )

    def test_design_css_est_toujours_la_derniere_feuille_de_style(self):
        for chemin in _fichiers_gabarit():
            with self.subTest(gabarit=os.path.relpath(chemin, RACINE)):
                contenu = open(chemin, encoding='utf-8').read()
                entete = contenu.split('</head>')[0]
                liens = re.findall(
                    r'<link[^>]+rel="stylesheet"[^>]*>', entete
                )
                self.assertTrue(liens, msg='Aucune feuille de style')
                self.assertIn('design.css', liens[-1])

    def test_aucun_bootstrap_dist_nest_pas_en_cache_par_le_service_worker(self):
        """Le responsive reste autonome : pas de CDN Bootstrap à charger."""
        for chemin in _fichiers_gabarit():
            with self.subTest(gabarit=os.path.relpath(chemin, RACINE)):
                contenu = open(chemin, encoding='utf-8').read()
                self.assertNotIn('bootstrap@', contenu.lower())
                self.assertNotIn('bootstrap.min.css', contenu.lower())

    def test_design_css_contient_les_breakpoints_et_le_mode_tactile(self):
        css = open(DESIGN_CSS, encoding='utf-8').read()
        for motif in [
            r'@media\s*\(max-width:\s*1024px\)',
            r'@media\s*\(max-width:\s*640px\)',
            r'@media\s*\(pointer:\s*coarse\)',
            r'\.col-md-6',
            r'\.col-lg-4',
            r'--touch-cible:\s*44px',
            r'env\(safe-area-inset-left\)',
            r'overflow-x:\s*auto',
        ]:
            with self.subTest(motif=motif):
                self.assertRegex(css, motif)

    def test_grille_adaptative_est_declarative_pour_tablette_et_mobile(self):
        css = open(DESIGN_CSS, encoding='utf-8').read()
        self.assertIn(
            '@media (min-width: 641px) and (max-width: 1024px)', css
        )
        self.assertIn('grid-template-columns: minmax(0, 1fr);', css)


if __name__ == '__main__':
    unittest.main()
