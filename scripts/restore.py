#!/usr/bin/env python3
"""Restauration contrôlée d'une sauvegarde SQLite de PERT/CPM.

Aucune écriture n'est effectuée sans l'option ``--force`` : par défaut, le
script vérifie la sauvegarde et affiche le plan de restauration (mode
simulation). Avec ``--force``, une confirmation interactive est demandée, sauf
si ``--yes`` est fourni pour les automatismes. Dans tous les cas, une copie de
sécurité de l'état courant est créée avant remplacement, puis la sauvegarde est
copiée dans un fichier temporaire vérifié avant déplacement atomique.

Le chemin de la base restaurée est déduit de ``--db``, de ``DATABASE_URL`` ou
du chemin par défaut ``instance/pert_cpm.db`` (repli ``pert_cpm.db``).
SQLite n'étant pas la base de production, consulter ``docs/sauvegarde.md`` pour
la procédure PostgreSQL (``pg_dump`` / ``pg_restore``).

Usage typique ::

    python scripts/restore.py --backup backups/pert_cpm_20261121_020000.sqlite3
    python scripts/restore.py --backup <fichier> --force --yes
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

NOM_BASE_DEFAUT = "pert_cpm.db"
DOSSIER_INSTANCE = "instance"
DOSSIER_SECURITE_DEFAUT = "backups"
PREFIXE_SQLITE = "sqlite:///"
EN_TETE_SQLITE = b"SQLite format 3\x00"


def chemin_depuis_source(source: str | None) -> Path:
    """Convertit ``--db`` ou ``DATABASE_URL`` en chemin de fichier SQLite."""
    brut = source if source is not None else os.environ.get("DATABASE_URL")
    if not brut or not brut.strip():
        return Path(DOSSIER_INSTANCE) / NOM_BASE_DEFAUT

    texte = brut.strip()
    if texte.startswith(PREFIXE_SQLITE):
        reste = texte[len(PREFIXE_SQLITE):]
        if reste.startswith(":memory:"):
            raise ValueError("Impossible de restaurer une base SQLite en mémoire.")
        if reste.startswith("/"):
            if re.match(r"^/[A-Za-z]:[\\/]", reste):
                return Path(reste[1:]).expanduser()
            return Path(reste).expanduser()
        return Path(DOSSIER_INSTANCE) / reste

    if "://" in texte:
        raise ValueError(
            "La cible ne désigne pas une base SQLite "
            f"(URI reçue : {texte.split('://', 1)[0]}://…)."
        )
    return Path(texte).expanduser()


def resoudre_chemin_base(source: str | None = None) -> Path:
    """Retourne le chemin absolu de la base à restaurer, avec repli racine."""
    chemin = chemin_depuis_source(source)
    if not chemin.exists() and chemin.parent.name == DOSSIER_INSTANCE:
        repli = chemin.parent.parent / chemin.name
        if repli.exists():
            return repli.resolve()
    return chemin.resolve()


def uri_lecture_seule(chemin: Path) -> str:
    """Construit une URI SQLite en lecture seule, sûre sur tous les systèmes."""
    return chemin.resolve().as_uri() + "?mode=ro"


def verifier_sauvegarde(chemin: Path) -> None:
    """Vérifie l'en-tête et l'intégrité de la sauvegarde avant toute copie."""
    if not chemin.is_file():
        raise FileNotFoundError(f"Sauvegarde introuvable : {chemin}")
    with chemin.open("rb") as flux:
        entete = flux.read(len(EN_TETE_SQLITE))
    if entete != EN_TETE_SQLITE:
        raise ValueError(f"Fichier non reconnu comme une base SQLite : {chemin}")
    with closing(sqlite3.connect(uri_lecture_seule(chemin), uri=True)) as connexion:
        etat = connexion.execute("PRAGMA quick_check").fetchone()
    if not etat or etat[0] != "ok":
        raise sqlite3.DatabaseError(
            f"Sauvegarde corrompue ({chemin}) : {etat[0] if etat else 'aucun résultat'}"
        )


def horodatage() -> str:
    """Horodatage utilisé dans les noms de fichiers de sécurité."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sauvegarde_securite(cible: Path, dossier: Path) -> Path | None:
    """Copie l'état courant avant écrasement ; None si la cible n'existe pas."""
    if not cible.exists():
        return None
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"{cible.stem}_avant_restauration_{horodatage()}.sqlite3"
    compteur = 2
    while chemin.exists():
        chemin = (
            dossier
            / f"{cible.stem}_avant_restauration_{horodatage()}_{compteur}.sqlite3"
        )
        compteur += 1
    with closing(sqlite3.connect(uri_lecture_seule(cible), uri=True)) as lecture:
        with closing(sqlite3.connect(str(chemin))) as ecriture:
            lecture.backup(ecriture)
    with closing(sqlite3.connect(uri_lecture_seule(chemin), uri=True)) as controle:
        etat = controle.execute("PRAGMA quick_check").fetchone()
    if not etat or etat[0] != "ok":
        raise sqlite3.DatabaseError("La sauvegarde de sécurité a échoué (quick_check).")
    return chemin


def copier_sauvegarde(source: Path, temporaire: Path) -> None:
    """Copie la sauvegarde dans un fichier temporaire via l'API SQLite."""
    with closing(sqlite3.connect(uri_lecture_seule(source), uri=True)) as lecture:
        with closing(sqlite3.connect(str(temporaire))) as ecriture:
            lecture.backup(ecriture)
    with closing(sqlite3.connect(uri_lecture_seule(temporaire), uri=True)) as controle:
        etat = controle.execute("PRAGMA quick_check").fetchone()
    if not etat or etat[0] != "ok":
        raise sqlite3.DatabaseError("La copie temporaire est invalide (quick_check).")


def confirmer() -> bool:
    """Demande une confirmation explicite en mode interactif."""
    try:
        reponse = input("Saisissez OUI pour confirmer la restauration : ").strip().upper()
    except EOFError:
        return False
    return reponse == "OUI"


def analyser_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Définit l'interface en ligne de commande du script de restauration."""
    analyse = argparse.ArgumentParser(
        description="Restauration vérifiée et confirmée d'une base SQLite PERT/CPM.",
        epilog=(
            "Sans --force, aucun fichier n'est modifié. Avec --force, une "
            "confirmation est demandée (--yes la remplace en automatique)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyse.add_argument(
        "--backup", metavar="FICHIER", required=True,
        help="sauvegarde SQLite à restaurer (obligatoire).",
    )
    analyse.add_argument(
        "--db", metavar="CHEMIN",
        help="base à remplacer (prioritaire sur DATABASE_URL).",
    )
    analyse.add_argument(
        "--safety-dir", metavar="DOSSIER", default=DOSSIER_SECURITE_DEFAUT,
        help="dossier de la copie de sécurité (défaut : backups).",
    )
    analyse.add_argument(
        "--force", action="store_true",
        help="autorise l'écrasement de la base cible.",
    )
    analyse.add_argument(
        "--yes", action="store_true",
        help="remplace la confirmation interactive (nécessite --force).",
    )
    return analyse.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : validation, confirmation, sauvegarde puis remplacement."""
    arguments = analyser_arguments(argv)

    try:
        source = Path(arguments.backup).expanduser().resolve()
        verifier_sauvegarde(source)
        cible = resoudre_chemin_base(arguments.db)
        dossier_securite = Path(arguments.safety_dir).expanduser()

        print(f"Sauvegarde : {source} (vérifiée)")
        print(f"Cible      : {cible}")
        print(f"Sécurité   : {dossier_securite}")
        if not arguments.force:
            print("Simulation : aucun fichier modifié. Relancer avec --force pour restaurer.")
            return 2
        if not arguments.yes and not confirmer():
            print("Restauration annulée : confirmation refusée.")
            return 2

        copie = sauvegarde_securite(cible, dossier_securite)
        if copie is not None:
            print(f"Copie de sécurité créée : {copie}")
        else:
            print("Aucune base existante : aucune copie de sécurité nécessaire.")

        dossier_securite.mkdir(parents=True, exist_ok=True)
        temporaire = cible.with_name(f"{cible.name}.restauration_{horodatage()}.tmp")
        if temporaire.exists():
            raise FileExistsError(f"Fichier temporaire déjà présent : {temporaire}")
        try:
            copier_sauvegarde(source, temporaire)
            os.replace(temporaire, cible)
        finally:
            if temporaire.exists():
                temporaire.unlink()

        for suffixe in ("-wal", "-shm"):
            residu = Path(str(cible) + suffixe)
            if residu.exists():
                residu.unlink()
                print(f"Résidu supprimé : {residu}")

        print(f"Restauration terminée : {cible}")
        print("Étapes suivantes : relancer l'application, vérifier /health et les données,")
        print("puis exécuter 'python -m unittest discover -s tests -v'.")
        return 0
    except (OSError, ValueError, sqlite3.Error) as erreur:
        print(f"ERREUR : {erreur}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
