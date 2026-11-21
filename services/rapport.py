"""Génération serveur du rapport PDF PERT/CPM (EXP-02, EXP-03 et EXP-04).

Le service ne recalcule aucun chiffre : les résultats transmis proviennent du
moteur canonique :mod:`logic.pert`. Il se contente de les mettre en forme (tableau
détaillé, réseau PERT, diagramme de Gantt) dans un PDF dont le texte reste
sélectionnable, contrairement au rendu raster historique côté navigateur.

Les valeurs injectées dans les ``Paragraph`` sont systématiquement échappées
(``xml.sax.saxutils.escape``) : un nom de projet ou une description contenant
``<`` ou ``&`` ne peut donc pas être interprété comme du balisage reportlab.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")  # backend sans affichage, obligatoire avant pyplot

import matplotlib.pyplot as plt  # noqa: E402  (import après le choix du backend)
from matplotlib.patches import FancyArrowPatch  # noqa: E402

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4, landscape  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

COLONNES = (
    "Tâche", "Description", "Durée", "ES", "EF", "LS", "LF",
    "Marge totale", "Marge libre", "Critique",
)

_LARGEURS = (16, 86, 14, 13, 13, 13, 13, 22, 22, 18)  # mm, total 230 mm
_ROUGE = colors.HexColor("#B00020")
_ROUGE_CLAIR = colors.HexColor("#FDECEC")
_GRIS_CLAIR = colors.HexColor("#F2F4F7")
_BLEU_ENTETE = colors.HexColor("#1F3A5F")
_BORDURE = colors.HexColor("#B8C2CC")
_MPL_ROUGE = "#C62828"
_MPL_BLEU = "#DCE7F2"
_MPL_TEXTE = "#12212F"
_MPL_FLECHE = "#52677D"


def _texte(valeur) -> str:
    """Échappe une valeur pour qu'elle soit sûre dans un ``Paragraph``."""
    if valeur is None:
        return ""
    chaine = str(valeur)
    # Les caractères de contrôle autres que tabulation/retour à la ligne
    # perturbent le parseur XML interne de reportlab : on les retire.
    chaine = "".join(
        caractere for caractere in chaine
        if caractere in "\n\t" or ord(caractere) >= 32
    )
    return escape(chaine).replace("\n", "<br/>")


def _nombre(valeur) -> str:
    """Formate un nombre issu du moteur sans virgule superflue (``9.0`` -> 9)."""
    if valeur is None:
        return "—"
    if isinstance(valeur, float):
        if not math.isfinite(valeur):
            return str(valeur)
        if valeur.is_integer():
            return str(int(valeur))
    return str(valeur)


def _fluide(valeur) -> float:
    """Convertit une valeur numérique en flottant fini, 0 en cas de doute."""
    try:
        nombre = float(valeur)
    except (TypeError, ValueError):
        return 0.0
    return nombre if math.isfinite(nombre) else 0.0


def _styles() -> dict[str, ParagraphStyle]:
    """Styles reportlab utilisés par les deux pages du rapport."""
    sample = getSampleStyleSheet()
    return {
        "titre": ParagraphStyle(
            "RapportTitre", parent=sample["Title"],
            fontSize=18, leading=22, spaceAfter=8,
        ),
        "meta": ParagraphStyle(
            "RapportMeta", parent=sample["Normal"],
            fontSize=9, leading=12, spaceAfter=2,
        ),
        "legende": ParagraphStyle(
            "RapportLegende", parent=sample["Normal"],
            fontSize=8.5, leading=11, spaceAfter=3,
        ),
        "entete": ParagraphStyle(
            "RapportEntete", parent=sample["Normal"],
            fontName="Helvetica-Bold", fontSize=7.4, leading=9,
            textColor=colors.white,
        ),
        "cellule": ParagraphStyle(
            "RapportCellule", parent=sample["Normal"],
            fontSize=7.4, leading=9,
        ),
        "critique": ParagraphStyle(
            "RapportCelluleCritique", parent=sample["Normal"],
            fontSize=7.4, leading=9, textColor=_ROUGE,
            fontName="Helvetica-Bold",
        ),
    }


def _valider(taches, resultats) -> list:
    """Vérifie les données reçues et renvoie les codes de tâches ordonnés.

    Aucun chiffre n'est recalculé ici : on exige seulement que chaque tâche
    dispose d'un résultat exploitable produit par le moteur.
    """
    if not isinstance(taches, Mapping):
        raise ValueError(
            "Le graphe de tâches est invalide : un dictionnaire est attendu."
        )
    if not isinstance(resultats, Mapping):
        raise ValueError(
            "Les résultats PERT sont invalides : un dictionnaire est attendu."
        )

    codes = list(taches)
    for code in codes:
        if code not in resultats:
            raise ValueError(
                f"Résultat manquant pour la tâche « {code} » : le moteur PERT/CPM "
                f"doit fournir un résultat pour chaque tâche avant génération "
                f"du rapport."
            )
        if not isinstance(resultats[code], Mapping):
            raise ValueError(
                f"Résultat invalide pour la tâche « {code} » : un dictionnaire "
                f"de valeurs ES/EF/LS/LF est attendu."
            )
    return codes


def _informations(taches, code) -> Mapping:
    """Retourne les informations d'une tâche, même si le format est minimal."""
    infos = taches[code]
    return infos if isinstance(infos, Mapping) else {}


def _est_critique(resultats, code) -> bool:
    return bool(resultats[code].get("critique", False))


def _tableau_detaille(codes, taches, resultats, styles) -> Table:
    """Tableau PERT/CPM complet, avec les lignes critiques en rouge."""
    donnees = [
        [Paragraph(_texte(colonne), styles["entete"]) for colonne in COLONNES]
    ]
    commandes = [
        ("BACKGROUND", (0, 0), (-1, 0), _BLEU_ENTETE),
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDURE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, _GRIS_CLAIR]),
    ]

    for numero, code in enumerate(codes, start=1):
        resultat = resultats[code]
        infos = _informations(taches, code)
        critique = _est_critique(resultats, code)
        style_ligne = styles["critique"] if critique else styles["cellule"]
        valeur = lambda cle: Paragraph(_texte(_nombre(resultat.get(cle))), style_ligne)
        donnees.append([
            Paragraph(_texte(code), style_ligne),
            Paragraph(_texte(infos.get("description", "")), style_ligne),
            Paragraph(_texte(_nombre(infos.get("duree"))), style_ligne),
            valeur("ES"),
            valeur("EF"),
            valeur("LS"),
            valeur("LF"),
            Paragraph(
                _texte(_nombre(resultat.get(
                    "marge_totale", resultat.get("marge")
                ))),
                style_ligne,
            ),
            valeur("marge_libre"),
            Paragraph(_texte("oui" if critique else "non"), style_ligne),
        ])
        if critique:
            commandes.append(("BACKGROUND", (0, numero), (-1, numero), _ROUGE_CLAIR))
            commandes.append(("TEXTCOLOR", (0, numero), (-1, numero), _ROUGE))

    tableau = Table(donnees, colWidths=[largeur * mm for largeur in _LARGEURS],
                    repeatRows=1, hAlign="LEFT")
    tableau.setStyle(TableStyle(commandes))
    return tableau


def _texte_chemins_critiques(chemins_critiques) -> str:
    """Présente tous les chemins critiques sur une seule ligne lisible."""
    chemins = []
    for chemin in chemins_critiques or ():
        if isinstance(chemin, (str, bytes)):
            suite = [chemin]
        else:
            suite = list(chemin)
        chemins.append(" -> ".join(_texte(str(code)) for code in suite))
    return ", ".join(chemins) if chemins else "aucun"


def _graphique_pert(codes, taches, resultats):
    """Construit le réseau PERT : x = ES, flèches des prédécesseurs."""
    figure, ax = plt.subplots(figsize=(11, 5.2))
    try:
        positions = {}
        for indice, code in enumerate(codes):
            x = _fluide(resultats[code].get("ES"))
            y = float(len(codes) - 1 - indice)
            positions[code] = (x, y)

        # Les flèches sont dessinées en premier pour passer sous les nœuds.
        for code in codes:
            for pred in _informations(taches, code).get("depend_de", ()) or ():
                if pred not in positions:
                    continue
                x1, y1 = positions[pred]
                x2, y2 = positions[code]
                critique = (_est_critique(resultats, code)
                            and pred in resultats
                            and _est_critique(resultats, pred))
                ax.add_patch(FancyArrowPatch(
                    (x1, y1), (x2, y2),
                    arrowstyle="-|>", mutation_scale=12,
                    linewidth=1.5 if critique else 0.9,
                    color=_MPL_ROUGE if critique else _MPL_FLECHE,
                    connectionstyle="arc3,rad=0.08",
                    shrinkA=15, shrinkB=15, zorder=1,
                ))

        for code in codes:
            x, y = positions[code]
            critique = _est_critique(resultats, code)
            duree = _nombre(_informations(taches, code).get("duree"))
            ax.scatter(
                [x], [y], s=760,
                c=_MPL_ROUGE if critique else _MPL_BLEU,
                edgecolors=_MPL_ROUGE if critique else _MPL_FLECHE,
                linewidths=1.1, zorder=2,
            )
            ax.text(
                x, y, f"{code}\n({duree})",
                ha="center", va="center",
                fontsize=7.2,
                color="white" if critique else _MPL_TEXTE,
                fontweight="bold" if critique else "normal",
                zorder=3,
            )

        if positions:
            xs = [pos[0] for pos in positions.values()]
            ys = [pos[1] for pos in positions.values()]
            ax.set_xlim(min(xs) - 1.5, max(xs) + 1.5)
            ax.set_ylim(min(ys) - 1, max(ys) + 1)
        else:
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.text(0.5, 0.5, "Aucune tâche", ha="center", va="center")

        ax.set_title("Réseau PERT - nœuds positionnés selon ES", fontsize=11)
        ax.set_xlabel("Début au plus tôt (ES)", fontsize=8)
        ax.set_yticks([])
        ax.grid(axis="x", linestyle=":", alpha=0.4)
        for spine in ax.spines.values():
            spine.set_visible(False)
        figure.tight_layout()
        return _en_png(figure)
    finally:
        plt.close(figure)


def _graphique_gantt(codes, taches, resultats):
    """Construit le diagramme de Gantt : barres de ES à EF."""
    figure, ax = plt.subplots(figsize=(11, 5.2))
    try:
        ordre = list(reversed(codes))  # première tâche en haut du graphique
        for indice, code in enumerate(ordre):
            resultat = resultats[code]
            es = _fluide(resultat.get("ES"))
            ef = _fluide(resultat.get("EF"))
            ecart = ef - es
            duree = max(ecart, 0.01)  # jalon de durée 0 reste visible
            critique = _est_critique(resultats, code)
            ax.barh(
                indice, duree, left=es, height=0.55,
                color=_MPL_ROUGE if critique else "#8FB8DE",
                edgecolor=_MPL_ROUGE if critique else _MPL_FLECHE,
                linewidth=0.8,
            )
            ax.text(
                es + duree / 2, indice,
                f"{code} ({_nombre(ecart)})",
                ha="center", va="center", fontsize=7.2,
                color="white" if critique else _MPL_TEXTE,
                fontweight="bold" if critique else "normal",
            )

        ax.set_yticks(range(len(ordre)), labels=ordre)
        ax.set_title("Diagramme de Gantt - barres de ES à EF", fontsize=11)
        ax.set_xlabel("Temps (jours calendaires)", fontsize=8)
        ax.tick_params(axis="y", labelsize=8)
        ax.grid(axis="x", linestyle=":", alpha=0.4)
        for spine in ax.spines.values():
            spine.set_visible(False)
        figure.tight_layout()
        return _en_png(figure)
    finally:
        plt.close(figure)


def _en_png(figure) -> tuple[BytesIO, int, int]:
    """Sérialise une figure en PNG en mémoire et renvoie ses dimensions."""
    tampon = BytesIO()
    figure.savefig(
        tampon, format="png", dpi=150,
        bbox_inches="tight", facecolor="white",
    )
    tampon.seek(0)
    largeur_px, hauteur_px = ImageReader(tampon).getSize()
    tampon.seek(0)
    return tampon, largeur_px, hauteur_px


def _image_pdf(tampon: BytesIO, largeur_px: int, hauteur_px: int,
               largeur_cible: float) -> Image:
    """Adapte une image PNG à la largeur disponible sans la déformer."""
    hauteur_cible = largeur_cible * hauteur_px / largeur_px
    tampon.seek(0)
    return Image(tampon, width=largeur_cible, height=hauteur_cible)


def generer_rapport_pdf(nom_projet: str, taches: dict, resultats: dict,
                        duree_totale, chemins_critiques: list[list[str]],
                        date_debut: str | None = None) -> bytes:
    """Génère un rapport PDF de deux pages et le renvoie sous forme de bytes.

    Page 1 : titre, date de génération, paramètres, tableau détaillé et
    chemins critiques. Page 2 : réseau PERT et diagramme de Gantt en PNG.

    Lève :class:`ValueError` si une tâche n'a pas de résultat calculé par le
    moteur : le rapport ne doit jamais inventer de chiffre.
    """
    codes = _valider(taches, resultats)
    styles = _styles()

    projet = "" if nom_projet is None else str(nom_projet)
    titre = f"Rapport PERT/CPM — {_texte(projet)}"
    date_generation = datetime.now().strftime("%d/%m/%Y %H:%M")
    debut = ("non renseignée" if date_debut is None or not str(date_debut).strip()
             else _texte(str(date_debut).strip()))

    histoire = [
        Paragraph(titre, styles["titre"]),
        Paragraph(f"<b>Date de génération :</b> {date_generation}", styles["meta"]),
        Paragraph(
            f"<b>Durée totale :</b> {_texte(_nombre(duree_totale))} jours calendaires",
            styles["meta"],
        ),
        Paragraph(f"<b>Nombre de tâches :</b> {len(codes)}", styles["meta"]),
        Paragraph(f"<b>Date de début :</b> {debut}", styles["meta"]),
        Paragraph("<b>Unité :</b> jours calendaires", styles["meta"]),
        Paragraph("<b>Relations :</b> Finish-to-Start (FS)", styles["meta"]),
        Spacer(1, 5 * mm),
        _tableau_detaille(codes, taches, resultats, styles),
        Spacer(1, 4 * mm),
        Paragraph(
            "<b>Chemins critiques :</b> "
            + _texte_chemins_critiques(chemins_critiques),
            styles["legende"],
        ),
        PageBreak(),
    ]

    graphique_pert, largeur_pert, hauteur_pert = _graphique_pert(
        codes, taches, resultats
    )
    graphique_gantt, largeur_gantt, hauteur_gantt = _graphique_gantt(
        codes, taches, resultats
    )
    image_pert = _image_pdf(graphique_pert, largeur_pert, hauteur_pert, 130 * mm)
    image_gantt = _image_pdf(graphique_gantt, largeur_gantt, hauteur_gantt, 130 * mm)

    panneaux = Table(
        [[
            [Paragraph("<b>Réseau PERT</b>", styles["legende"]),
             Spacer(1, 2 * mm), image_pert],
            [Paragraph("<b>Diagramme de Gantt</b>", styles["legende"]),
             Spacer(1, 2 * mm), image_gantt],
        ]],
        colWidths=[132 * mm, 132 * mm],
        hAlign="LEFT",
    )
    panneaux.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    histoire.append(panneaux)

    tampon_pdf = BytesIO()
    document = SimpleDocTemplate(
        tampon_pdf,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Rapport PERT/CPM — {projet}",
        author="Application PERT/CPM",
        subject="Rapport PERT/CPM",
        pageCompression=0,  # flux lisibles : le texte reste extractible/sélectionnable
    )
    document.build(histoire)

    contenu = tampon_pdf.getvalue()
    if not contenu.startswith(b"%PDF"):
        raise RuntimeError("Le générateur n'a pas produit un document PDF valide.")
    return contenu
