"""Service d'import/export tabulaire pour l'application PERT/CPM.

Formats documentés (voir TASK-06 et EXP-01) :

* import CSV : UTF-8 (BOM ``utf-8-sig`` accepté), séparateur ``;`` ou ``,``
  détecté automatiquement ; les champs contenant le séparateur doivent être
  entre guillemets ;
* import XLSX : première feuille du classeur, avec ou sans ligne d'en-têtes ;
* export CSV : UTF-8 avec BOM, séparateur ``;`` ;
* export XLSX : classeur en mémoire, onglet « Résultats ».

Le texte renvoyé par :func:`importer_fichier` utilise exactement la syntaxe
attendue par ``logic.data.parser_taches`` (``nom;description;duree;pred1,pred2``)
et peut donc être passé directement à ``parser_taches()`` puis à ``compute()``.
Aucune fonction d'import ne lève d'exception : les problèmes sont renvoyés
sous forme de messages numérotés par ligne.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
import unicodedata

from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill

from logic.data import MAX_CARACTERES

MAX_OCTETS = 5 * 1024 * 1024
MAX_LIGNES = 2000
PREFIXES_FORMULE = ("=", "+", "-", "@")

COLONNES = (
    "Tache", "Description", "Duree", "ES", "EF", "LS", "LF",
    "Marge totale", "Marge libre", "Critique",
)

_DUREE_ASCII = re.compile(r"^[0-9]+$")
_DUREE_NEGATIVE = re.compile(r"^-[0-9]+$")
_SEPARATEURS = re.compile(r"[,;\s]+")
_SEPARATEURS_CONSECUTIFS = re.compile(r"[,;]\s*[,;]")


def _erreur_ligne(numero: int, message: str) -> str:
    """Préfixe un message d'erreur par son numéro de ligne (1-based)."""
    return f"Ligne {numero} : {message}"


def _normaliser(valeur) -> str:
    """Normalise un en-tête : minuscules, sans accents ni ponctuation."""
    if valeur is None:
        return ""
    texte = str(valeur).strip().lower()
    texte = "".join(
        caractere for caractere in unicodedata.normalize("NFKD", texte)
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"[^a-z0-9]", "", texte)


def _ligne_vide(ligne) -> bool:
    """Vrai si une ligne (CSV ou XLSX) ne contient aucune valeur utile."""
    return not any(cellule is not None and str(cellule).strip() for cellule in ligne)


def _detecter_separateur(lignes: list[str]) -> str:
    """Détecte ``;`` ou ``,`` en privilégiant le découpage le plus régulier."""
    echantillon = [ligne for ligne in lignes[:20] if ligne.strip()]
    if not echantillon:
        return ";"

    meilleur = None
    for separateur in (";", ","):
        try:
            rangees = list(csv.reader(echantillon, delimiter=separateur))
        except csv.Error:
            continue
        largeurs = [len(rangee) for rangee in rangees if rangee]
        if not largeurs:
            continue
        largeur_courante = max(set(largeurs), key=largeurs.count)
        if largeur_courante < 2:
            continue
        score = (sum(1 for largeur in largeurs if largeur == largeur_courante),
                 largeur_courante)
        if meilleur is None or score > meilleur[0]:
            meilleur = (score, separateur)
    if meilleur is not None:
        return meilleur[1]

    nb_points_virgule = sum(ligne.count(";") for ligne in echantillon)
    nb_virgules = sum(ligne.count(",") for ligne in echantillon)
    return ";" if nb_points_virgule >= nb_virgules else ","


def _indices_colonnes(ligne) -> dict[str, int] | None:
    """Repère les colonnes d'une ligne d'en-têtes, ou ``None`` si absente.

    Une ligne est considérée comme un en-tête lorsqu'elle contient à la fois
    un identifiant de tâche (``code``/``tache``) et une durée (``duree``).
    """
    normes = [_normaliser(cellule) for cellule in ligne]
    a_code = any("code" in norme or "tache" in norme for norme in normes)
    a_duree = any("duree" in norme for norme in normes)
    if not (a_code and a_duree):
        return None

    indices: dict[str, int] = {}
    for position, norme in enumerate(normes):
        if not norme:
            continue
        if "duree" in norme:
            indices.setdefault("duree", position)
        elif ("predecesseur" in norme
              or norme in ("pred", "preds", "dependance", "dependances",
                           "dependde", "de")):
            indices.setdefault("pred", position)
        elif "description" in norme or norme in ("desc", "libelle", "intitule"):
            indices.setdefault("description", position)
        elif ("code" in norme or "tache" in norme
              or norme in ("nom", "id", "identifiant")):
            indices.setdefault("code", position)
    return indices


def _valeur_colonne(ligne, index: int | None, defaut=""):
    """Lit une colonne éventuellement absente d'une ligne courte."""
    if index is None or index >= len(ligne):
        return defaut
    return ligne[index]


def _valeur_predecesseurs(ligne, index: int | None) -> str:
    """Rassemble la colonne de prédécesseurs et les éventuelles colonnes suivantes.

    Les exports naïve écrivent parfois ``A;B;C`` (ou ``A,B,C``) sans guillemets :
    les valeurs supplémentaires sont alors des colonnes distinctes. On les
    réassemble, en conservant les champs vides internes afin de pouvoir
    signaler un prédécesseur vide « bizarre ».
    """
    if index is None:
        return ""
    morceaux = [
        "" if valeur is None else str(valeur).strip()
        for valeur in ligne[index:]
    ]
    while morceaux and not morceaux[-1]:
        morceaux.pop()
    return ",".join(morceaux)


def _code_propre(valeur, numero: int, erreurs: list[str]) -> str | None:
    """Valide et nettoie le code d'une tâche."""
    code = "" if valeur is None else str(valeur).strip()
    if not code:
        erreurs.append(_erreur_ligne(numero, "code de tâche manquant"))
        return None
    if ";" in code or "\n" in code or "\r" in code:
        erreurs.append(_erreur_ligne(
            numero,
            f'code de tâche invalide ("{code}") : ";" et les sauts de ligne '
            "sont interdits",
        ))
        return None
    return code


def _description_propre(valeur) -> str:
    """Rend une description compatible avec la syntaxe `nom;description;...`."""
    if valeur is None:
        return ""
    return (str(valeur)
            .replace("\r", " ")
            .replace("\n", " ")
            .replace(";", ",")
            .strip())


def _valider_duree(valeur) -> tuple[int | None, str | None]:
    """Retourne ``(duree, message_erreur)`` pour une durée entière >= 0."""
    if isinstance(valeur, bool):
        return None, "durée invalide (un booléen n'est pas une durée)"
    if isinstance(valeur, int):
        duree = valeur
    elif isinstance(valeur, float):
        if not math.isfinite(valeur) or not valeur.is_integer():
            return None, f'durée invalide ("{valeur}") : un entier est attendu'
        duree = int(valeur)
    else:
        texte = "" if valeur is None else str(valeur).strip()
        if not texte:
            return None, "durée manquante"
        if _DUREE_NEGATIVE.fullmatch(texte):
            return None, f'durée négative interdite ("{texte}")'
        if not _DUREE_ASCII.fullmatch(texte):
            return None, (
                f'durée invalide ("{texte}") : un entier décimal nul ou '
                "positif est attendu"
            )
        duree = int(texte)

    if duree < 0:
        return None, f'durée négative interdite ("{valeur}")'
    return duree, None


def _predecesseurs_propres(valeur, numero: int, code: str,
                           erreurs: list[str]) -> list[str]:
    """Accepte ``A,B``, ``A;B``, ``A B`` et signale les champs vides internes."""
    if valeur is None:
        return []
    texte = str(valeur).strip()
    if not texte:
        return []

    if (_SEPARATEURS_CONSECUTIFS.search(texte)
            or texte.startswith((",", ";"))
            or texte.endswith((",", ";"))):
        erreurs.append(_erreur_ligne(
            numero,
            f'(tâche "{code}") : prédécesseur vide inattendu dans "{texte}"',
        ))

    predecesseurs = [mot.strip() for mot in _SEPARATEURS.split(texte) if mot.strip()]
    return list(dict.fromkeys(predecesseurs))


def _convertir_lignes(lignes_travail: list[tuple[int, list]],
                      erreurs: list[str]) -> tuple[str, list[str]]:
    """Convertit les lignes utiles (numéro, valeurs) en texte parser_taches."""
    indices = _indices_colonnes(lignes_travail[0][1])
    a_en_tetes = indices is not None
    if indices is None:
        indices = {"code": 0, "description": 1, "duree": 2, "pred": 3}
    elif "code" not in indices or "duree" not in indices:
        return "", [_erreur_ligne(
            lignes_travail[0][0],
            "en-têtes incomplets : les colonnes 'code' et 'duree' sont requises",
        )]

    lignes_sortie: list[str] = []
    codes_vus: dict[str, int] = {}
    for numero, ligne in (lignes_travail[1:] if a_en_tetes else lignes_travail):
        code = _code_propre(_valeur_colonne(ligne, indices.get("code")),
                            numero, erreurs)
        if code is None:
            continue

        duree, message = _valider_duree(
            _valeur_colonne(ligne, indices.get("duree"))
        )
        if message is not None:
            erreurs.append(_erreur_ligne(numero, f'(tâche "{code}") : {message}'))
            continue

        predecesseurs = _predecesseurs_propres(
            _valeur_predecesseurs(ligne, indices.get("pred")),
            numero, code, erreurs,
        )

        if code in codes_vus:
            erreurs.append(_erreur_ligne(
                numero,
                f'le code "{code}" est en double '
                f"(déjà utilisé ligne {codes_vus[code]})",
            ))
            continue
        codes_vus[code] = numero

        description = _description_propre(
            _valeur_colonne(ligne, indices.get("description"))
        )
        lignes_sortie.append(
            f"{code};{description};{duree};{','.join(predecesseurs)}"
        )

    texte = "\n".join(lignes_sortie)
    if len(texte) > MAX_CARACTERES:
        erreurs.append(_erreur_ligne(
            1,
            f"données trop volumineuses après conversion ({len(texte)} "
            f"caractères, maximum {MAX_CARACTERES})",
        ))
        return "", erreurs
    return texte, erreurs


def _importer_fichier(donnees: bytes, nom_fichier: str) -> tuple[str, list[str]]:
    """Implémentation de :func:`importer_fichier` (les erreurs sont numerotées)."""
    if not isinstance(donnees, (bytes, bytearray, memoryview)):
        return "", [_erreur_ligne(1, "les données du fichier doivent être binaires")]
    contenu = bytes(donnees)

    if len(contenu) > MAX_OCTETS:
        return "", [_erreur_ligne(
            1,
            f"fichier trop volumineux ({len(contenu)} octets, "
            f"maximum {MAX_OCTETS} octets)",
        )]

    if not isinstance(nom_fichier, str):
        return "", [_erreur_ligne(1, "nom de fichier invalide")]
    extension = os.path.splitext(nom_fichier.strip())[1].lower()
    if extension not in (".csv", ".xlsx"):
        return "", [_erreur_ligne(
            1,
            f'format de fichier non pris en charge ("{nom_fichier}") : '
            "utilisez .csv ou .xlsx",
        )]

    if extension == ".csv":
        try:
            texte_brut = contenu.decode("utf-8-sig")
        except UnicodeDecodeError:
            return "", [_erreur_ligne(
                1, "encodage CSV invalide : UTF-8 (BOM accepté) attendu",
            )]

        lignes = texte_brut.splitlines()
        nb_utiles = sum(1 for ligne in lignes if ligne.strip())
        if nb_utiles > MAX_LIGNES:
            return "", [_erreur_ligne(
                MAX_LIGNES + 1,
                f"trop de lignes ({nb_utiles}, maximum {MAX_LIGNES})",
            )]

        try:
            rangees = list(csv.reader(
                lignes, delimiter=_detecter_separateur(lignes)
            ))
        except csv.Error:
            return "", [_erreur_ligne(
                1, "CSV illisible (séparateur ou guillemets invalides)",
            )]
        lignes_travail = [
            (numero, rangee)
            for numero, rangee in enumerate(rangees, start=1)
            if rangee and not _ligne_vide(rangee)
        ]
    else:
        try:
            classeur = load_workbook(
                io.BytesIO(contenu), read_only=True, data_only=True
            )
        except Exception as exc:
            return "", [_erreur_ligne(
                1, f"classeur XLSX illisible ({type(exc).__name__})",
            )]

        try:
            if not classeur.worksheets:
                return "", [_erreur_ligne(
                    1, "le classeur ne contient aucune feuille",
                )]
            feuille = classeur.worksheets[0]
            lignes_travail = []
            for numero, rangee in enumerate(
                    feuille.iter_rows(values_only=True), start=1):
                if _ligne_vide(rangee):
                    continue
                if len(lignes_travail) >= MAX_LIGNES:
                    return "", [_erreur_ligne(
                        numero, f"trop de lignes (plus de {MAX_LIGNES})",
                    )]
                lignes_travail.append((numero, list(rangee)))
        finally:
            try:
                classeur.close()
            except Exception:
                pass

    if not lignes_travail:
        return "", [_erreur_ligne(1, "le fichier ne contient aucune tâche")]
    return _convertir_lignes(lignes_travail, [])


def importer_fichier(donnees: bytes, nom_fichier: str) -> tuple[str, list[str]]:
    """Retourne ``(texte_au_format_parser_taches, erreurs)``.

    Prend en charge ``.csv`` (UTF-8 avec BOM accepté, séparateur ``,`` ou ``;``
    détecté) et ``.xlsx`` (première feuille). La première ligne est traitée
    comme un en-tête si elle contient ``code``/``tache`` et ``duree``. Les
    colonnes attendues sont ``code``, ``description``, ``duree`` et
    ``predecesseurs`` (optionnel). Les prédécesseurs acceptent ``A,B``, ``A;B``
    et ``A B``. Chaque erreur contient son numéro de ligne.
    """
    try:
        return _importer_fichier(donnees, nom_fichier)
    except Exception as exc:  # dernier rempart : jamais d'exception à l'appelant
        return "", [_erreur_ligne(
            1, f"fichier illisible ({type(exc).__name__} : {exc})",
        )]


def _valeur_csv(valeur):
    """Convertit une valeur en champ CSV, en neutralisant les formules."""
    if valeur is None:
        return ""
    if isinstance(valeur, bool):
        return "OUI" if valeur else "Non"
    if isinstance(valeur, (int, float)):
        return valeur
    texte = str(valeur)
    if texte.startswith(PREFIXES_FORMULE):
        return "'" + texte
    return texte


def _ligne_export(taches: dict, resultats: dict, nom) -> list:
    """Construit la ligne de résultats d'une tâche (10 colonnes)."""
    infos = taches.get(nom) if isinstance(taches, dict) else {}
    if not isinstance(infos, dict):
        infos = {}
    resultat = resultats.get(nom, {}) if isinstance(resultats, dict) else {}
    if not isinstance(resultat, dict):
        resultat = {}

    return [
        nom,
        infos.get("description", ""),
        infos.get("duree", 0),
        resultat.get("ES", 0),
        resultat.get("EF", 0),
        resultat.get("LS", 0),
        resultat.get("LF", 0),
        resultat.get("marge_totale", resultat.get("marge", 0)),
        resultat.get("marge_libre", 0),
        "OUI" if resultat.get("critique", False) else "Non",
    ]


def exporter_csv(taches: dict, resultats: dict) -> bytes:
    """Exporte les résultats en CSV UTF-8 avec BOM et séparateur ``;``.

    Les colonnes sont : ``Tache;Description;Duree;ES;EF;LS;LF;Marge totale;
    Marge libre;Critique``. Tout texte commençant par ``=``, ``+``, ``-`` ou
    ``@`` est préfixé par une apostrophe pour neutraliser les formules.
    """
    if not isinstance(taches, dict):
        taches = {}
    if not isinstance(resultats, dict):
        resultats = {}

    tampon = io.StringIO(newline="")
    ecrivain = csv.writer(tampon, delimiter=";", lineterminator="\r\n")
    ecrivain.writerow(COLONNES)
    for nom in taches:
        ecrivain.writerow([_valeur_csv(valeur)
                           for valeur in _ligne_export(taches, resultats, nom)])
    return ("\ufeff" + tampon.getvalue()).encode("utf-8")


def _neutraliser_ligne(feuille, numero_ligne: int) -> None:
    """Force en texte (data_type 's') les cellules pouvant être des formules."""
    for cellule in feuille[numero_ligne]:
        valeur = cellule.value
        if isinstance(valeur, str) and valeur.startswith(PREFIXES_FORMULE):
            cellule.data_type = "s"
            cellule.quotePrefix = True


def exporter_xlsx(taches: dict, resultats: dict, duree_totale,
                  chemins_critiques: list[list[str]]) -> bytes:
    """Exporte les résultats dans un classeur en mémoire (onglet « Résultats »).

    Les colonnes sont identiques à celles de :func:`exporter_csv`. Les lignes
    critiques sont mises en rouge, la durée totale et les chemins critiques
    sont ajoutés en fin de feuille, et les formules sont neutralisées.
    """
    if not isinstance(taches, dict):
        taches = {}
    if not isinstance(resultats, dict):
        resultats = {}
    if not isinstance(chemins_critiques, (list, tuple)):
        chemins_critiques = []

    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Résultats"
    feuille.append(list(COLONNES))
    _neutraliser_ligne(feuille, feuille.max_row)

    rouge = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")
    for nom in taches:
        feuille.append(_ligne_export(taches, resultats, nom))
        ligne = feuille.max_row
        _neutraliser_ligne(feuille, ligne)
        resultat = resultats.get(nom, {})
        if isinstance(resultat, dict) and resultat.get("critique", False):
            for cellule in feuille[ligne]:
                cellule.fill = rouge

    feuille.append([])
    feuille.append([f"Durée totale du projet : {duree_totale} jours"])
    _neutraliser_ligne(feuille, feuille.max_row)
    chemins = " | ".join(
        " → ".join(str(tache) for tache in chemin)
        for chemin in chemins_critiques
    )
    feuille.append([f"Chemins critiques : {chemins}"])
    _neutraliser_ligne(feuille, feuille.max_row)

    sortie = io.BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()
