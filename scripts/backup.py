#!/usr/bin/env python3
"""Sauvegarde cohérente de la base SQLite de l'application PERT/CPM.

La copie est réalisée avec ``sqlite3.Connection.backup`` : contrairement à une
copie du fichier, cette API produit un instantané transactionnellement sain,
même si l'application est en cours d'exécution. Le fichier source est ouvert en
lecture seule et n'est jamais modifié.

Le chemin de la base est déterminé, par ordre de priorité, par l'option
``--db``, la variable d'environnement ``DATABASE_URL`` (URI SQLAlchemy
``sqlite:///...`` acceptée) ou le chemin par défaut ``instance/pert_cpm.db``
(avec repli sur ``pert_cpm.db`` à la racine du dépôt). Le nom de la sauvegarde
comporte un horodatage ``AAAAmmdd_HHMMSS`` et une rétention parameterizable est
appliquée aux anciennes copies.

Usage typique ::

    python scripts/backup.py
    python scripts/backup.py --db instance/pert_cpm.db --output backups --keep 30
    python scripts/backup.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

NOM_BASE_DEFAUT = "pert_cpm.db"
DOSSIER_INSTANCE = "instance"
DOSSIER_SORTIE_DEFAUT = "backups"
PREFIXE_SQLITE = "sqlite:///"
MOTIF_COPIE = re.compile(
    r"^(?P<base>.+)_(?P<jour>\d{8})_(?P<heure>\d{6})(?:_\d+)?\.sqlite3$"
)


def chemin_depuis_source(source: str | None) -> Path:
    """Convertit ``--db`` ou ``DATABASE_URL`` en chemin de fichier SQLite."""
    brut = source if source is not None else os.environ.get("DATABASE_URL")
    if not brut or not brut.strip():
        return Path(DOSSIER_INSTANCE) / NOM_BASE_DEFAUT

    texte = brut.strip()
    if texte.startswith(PREFIXE_SQLITE):
        reste = texte[len(PREFIXE_SQLITE):]
        if reste.startswith(":memory:"):
            raise ValueError(
                "Impossible de sauvegarder une base SQLite en mémoire (:memory:)."
            )
        if reste.startswith("/"):
            # sqlite:////tmp/x.db -> /tmp/x.db ; sqlite:///C:/x.db -> C:/x.db
            if re.match(r"^/[A-Za-z]:[\\/]", reste):
                return Path(reste[1:]).expanduser()
            return Path(reste).expanduser()
        return Path(DOSSIER_INSTANCE) / reste

    if "://" in texte:
        raise ValueError(
            "La source ne désigne pas une base SQLite "
            f"(URI reçue : {texte.split('://', 1)[0]}://…)."
        )
    return Path(texte).expanduser()


def resoudre_chemin_base(source: str | None = None) -> Path:
    """Retourne le chemin absolu de la base, avec repli sur la racine du dépôt."""
    chemin = chemin_depuis_source(source)
    if not chemin.exists() and chemin.parent.name == DOSSIER_INSTANCE:
        repli = chemin.parent.parent / chemin.name
        if repli.exists():
            return repli.resolve()
    return chemin.resolve()


def uri_lecture_seule(chemin: Path) -> str:
    """Construit une URI SQLite en lecture seule, sûre sur tous les systèmes."""
    return chemin.resolve().as_uri() + "?mode=ro"


def verifier_integrite(chemin: Path) -> None:
    """Lève une erreur si le ``PRAGMA quick_check`` de la base n'est pas « ok »."""
    with closing(sqlite3.connect(uri_lecture_seule(chemin), uri=True)) as connexion:
        etat = connexion.execute("PRAGMA quick_check").fetchone()
    if not etat or etat[0] != "ok":
        raise sqlite3.DatabaseError(
            f"Intégrité invalide pour {chemin} : {etat[0] if etat else 'aucun résultat'}"
        )


def nom_cible(source: Path, dossier: Path) -> Path:
    """Retourne un nom horodaté unique dans ``dossier``."""
    horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
    cible = dossier / f"{source.stem}_{horodatage}.sqlite3"
    compteur = 2
    while cible.exists():
        cible = dossier / f"{source.stem}_{horodatage}_{compteur}.sqlite3"
        compteur += 1
    return cible


def creer_copie(source: Path, cible: Path) -> Path:
    """Copie ``source`` vers ``cible`` avec l'API de sauvegarde SQLite."""
    with closing(sqlite3.connect(uri_lecture_seule(source), uri=True)) as lecture:
        with closing(sqlite3.connect(str(cible))) as ecriture:
            lecture.backup(ecriture)
    verifier_integrite(cible)
    return cible


def anciennes_copies(dossier: Path, stem: str, exclure: Path) -> list[Path]:
    """Liste les copies horodatées du même projet, hors sauvegarde fraîche."""
    trouvees = []
    for chemin in dossier.glob(f"{stem}_*.sqlite3"):
        correspondance = MOTIF_COPIE.match(chemin.name)
        if correspondance and correspondance.group("base") == stem and chemin != exclure:
            trouvees.append(chemin)
    trouvees.sort(key=lambda chemin: chemin.stat().st_mtime, reverse=True)
    return trouvees


def purger(dossier: Path, stem: str, garder: int | None, jours: int | None,
           exclure: Path, simulation: bool = False) -> list[Path]:
    """Applique la rétention (nombre et/ou ancienneté) aux anciennes copies."""
    if garder is None and jours is None:
        return []

    candidats = anciennes_copies(dossier, stem, exclure)
    a_supprimer: set[Path] = set()

    if garder is not None and len(candidats) > garder:
        a_supprimer.update(candidats[garder:])

    if jours is not None:
        seuil = (datetime.now() - timedelta(days=jours)).timestamp()
        a_supprimer.update(
            chemin for chemin in candidats if chemin.stat().st_mtime < seuil
        )

    suppression = sorted(a_supprimer, key=lambda chemin: chemin.stat().st_mtime)
    for chemin in suppression:
        print(f"[rétention] {'Simulation - ' if simulation else ''}suppression : {chemin}")
        if not simulation:
            chemin.unlink()
    return suppression


def analyser_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Définit l'interface en ligne de commande du script de sauvegarde."""
    analyse = argparse.ArgumentParser(
        description="Sauvegarde cohérente de la base SQLite PERT/CPM.",
        epilog=(
            "Exemples : python scripts/backup.py ; "
            "python scripts/backup.py --db instance/pert_cpm.db --keep 30 --days 90"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyse.add_argument(
        "--db", metavar="CHEMIN",
        help="fichier SQLite source (prioritaire sur DATABASE_URL).",
    )
    analyse.add_argument(
        "--output", metavar="DOSSIER", default=DOSSIER_SORTIE_DEFAUT,
        help=f"dossier des sauvegardes (défaut : {DOSSIER_SORTIE_DEFAUT}).",
    )
    analyse.add_argument(
        "--keep", metavar="N", type=int, default=30,
        help="nombre maximum de copies conservées (0 = illimité, défaut : 30).",
    )
    analyse.add_argument(
        "--days", metavar="N", type=int, default=None,
        help="supprime aussi les copies plus anciennes que N jours.",
    )
    analyse.add_argument(
        "--no-prune", action="store_true",
        help="désactive la rétention automatique.",
    )
    analyse.add_argument(
        "--dry-run", action="store_true",
        help="affiche les actions prévues sans écrire ni supprimer.",
    )
    return analyse.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : prépare, crée puis purge la sauvegarde."""
    arguments = analyser_arguments(argv)
    garder = None if arguments.no_prune or arguments.keep <= 0 else arguments.keep
    jours = None if arguments.no_prune else arguments.days

    try:
        source = resoudre_chemin_base(arguments.db)
        if not source.is_file():
            raise FileNotFoundError(f"Base SQLite introuvable : {source}")
        verifier_integrite(source)

        dossier = Path(arguments.output).expanduser()
        cible = nom_cible(source, dossier)
        print(f"Source : {source}")
        print(f"Cible  : {cible}")

        if arguments.dry_run:
            purger(dossier, source.stem, garder, jours, cible, simulation=True)
            print("Simulation terminée : aucun fichier modifié.")
            return 0

        dossier.mkdir(parents=True, exist_ok=True)
        cible = creer_copie(source, cible)
        taille = cible.stat().st_size
        purger(dossier, source.stem, garder, jours, cible)
        print(f"Sauvegarde terminée : {cible} ({taille} octets)")
        return 0
    except (OSError, ValueError, sqlite3.Error) as erreur:
        print(f"ERREUR : {erreur}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
