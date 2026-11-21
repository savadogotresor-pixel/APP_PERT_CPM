"""Traitement groupé et borné des projets PERT/CPM (100 à 1000 projets).

Les trois fonctions de ce module sont indépendantes de Flask et ne lisent
jamais ``request`` ou ``current_user`` : le contrôle de propriété est passé
explicitement par l'appelant (``user_id``). Les identifiants sont traités par
lots de :data:`TAILLE_LOT`, avec **une seule requête de chargement par lot**
(les relations nécessaires sont chargées avec ``joinedload`` afin d'éviter le
redoutable « N+1 » sur les tâches et les dépendances).

Aucune file d'attente, aucun cache et aucune dépendance externe : le service
tourne dans le processus Flask, ce qui suffit pour quelques centaines de
projets. Les chiffres PERT ne sont jamais recalculés « à la main » ici :
:func:`logic.pert.compute` reste l'unique source de vérité.

Chaque fonction renvoie le résumé ``{traites, reussis, echoues, erreurs}``.
``erreurs`` est une liste de dictionnaires ``{'id': ..., 'message': ...}``.
"""

from __future__ import annotations

import csv
import io

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from extensions import db
from logic.pert import ValidationError, compute
from models import (
    AlerteRisque,
    Dependency,
    Project,
    ProjectAccess,
    Scenario,
    Task,
    project_to_taches,
)

TAILLE_LOT = 100
LIMITE_IDS = 1000


def _identifiants(ids):
    """Normalise une collection d'identifiants (doublons et valeurs sales exclus)."""
    if ids is None:
        return []
    if isinstance(ids, (str, bytes)):
        valeurs = [ids]
    else:
        try:
            valeurs = list(ids)
        except TypeError:
            valeurs = [ids]

    resultat = []
    vus = set()
    for valeur in valeurs:
        try:
            identifiant = int(valeur)
        except (TypeError, ValueError):
            continue
        if identifiant <= 0 or identifiant in vus:
            continue
        vus.add(identifiant)
        resultat.append(identifiant)
        if len(resultat) >= LIMITE_IDS:
            break
    return resultat


def _lots(identifiants, taille=TAILLE_LOT):
    """Découpe les identifiants en lots de 100 au plus."""
    for debut in range(0, len(identifiants), taille):
        yield identifiants[debut:debut + taille]


def _proprietaire(user_id, aliases):
    """Résout l'identifiant du propriétaire (objet utilisateur ou entier accepté)."""
    if user_id is None:
        for cle in ('utilisateur_id', 'utilisateur', 'user', 'owner_id'):
            if cle in aliases:
                user_id = aliases[cle]
                break
    if user_id is None:
        return None
    return getattr(user_id, 'id', user_id)


def _resume():
    return {'traites': 0, 'reussis': 0, 'echoues': 0, 'erreurs': []}


def _echec(resume, identifiant, message):
    resume['echoues'] += 1
    resume['erreurs'].append({'id': identifiant, 'message': str(message)})


def _message_erreur(exception):
    lignes = [ligne.strip() for ligne in str(exception).splitlines() if ligne.strip()]
    return ' ; '.join(lignes)[:300] or 'Erreur inconnue.'


def _charger_lot(lot, proprietaire_id):
    """Une seule requête SQL pour charger un lot et toutes ses relations utiles."""
    return (
        Project.query.options(
            joinedload(Project.tasks)
            .joinedload(Task.dependances)
            .joinedload(Dependency.predecessor),
            joinedload(Project.tasks).joinedload(Task.dependances_sortantes),
        )
        .filter(Project.id.in_(lot), Project.user_id == proprietaire_id)
        .all()
    )


def _controle_refuse(resume, identifiants):
    """Sans propriétaire explicite, aucun projet n'est touché."""
    for identifiant in identifiants:
        resume['traites'] += 1
        _echec(
            resume, identifiant,
            "Propriétaire non spécifié : traitement refusé.",
        )
    return resume


def recalculer_projets(ids, user_id=None, **kwargs):
    """Recalcule ((valide) les plannings des projets appartenant à ``user_id``.

    Le moteur canonique est appelé pour chaque projet : aucun résultat chiffré
    n'est écrit en base, conformément au modèle de données de l'application.
    Les identifiants inconnus ou appartenant à un autre utilisateur sont
    comptabilisés comme des échecs, jamais modifiés.
    """
    proprietaire_id = _proprietaire(user_id, kwargs)
    identifiants = _identifiants(ids)
    resume = _resume()
    if proprietaire_id is None:
        return _controle_refuse(resume, identifiants)

    for lot in _lots(identifiants):
        projets = _charger_lot(lot, proprietaire_id)
        par_id = {projet.id: projet for projet in projets}
        for identifiant in lot:
            resume['traites'] += 1
            projet = par_id.get(identifiant)
            if projet is None:
                _echec(
                    resume, identifiant,
                    "Projet introuvable ou accès refusé.",
                )
                continue
            try:
                compute(project_to_taches(projet))
            except ValidationError as exc:
                _echec(resume, identifiant, _message_erreur(exc))
            except Exception as exc:  # un projet corrompu n'arrête pas le lot
                _echec(resume, identifiant, _message_erreur(exc))
            else:
                resume['reussis'] += 1
    return resume


def supprimer_projets(ids, user_id=None, **kwargs):
    """Supprime les projets (tâches et dépendances comprises) d'un seul propriétaire.

    Un seul ``SELECT`` par lot récupère les identifiants autorisés ; les
    suppressions sont ensuite groupées en instructions ``DELETE`` (aucune
    requête par projet, aucun chargement de relations en cascade). Les tables
    secondaires sont nettoyées explicitement car SQLite n'applique pas les
    ``ON DELETE CASCADE`` sans PRAGMA dédié.
    """
    proprietaire_id = _proprietaire(user_id, kwargs)
    identifiants = _identifiants(ids)
    resume = _resume()
    if proprietaire_id is None:
        return _controle_refuse(resume, identifiants)

    reussis = []
    for lot in _lots(identifiants):
        autorises = [
            ligne.id for ligne in db.session.query(Project.id)
            .filter(Project.id.in_(lot), Project.user_id == proprietaire_id)
            .all()
        ]
        ensembles = set(autorises)
        for identifiant in lot:
            resume['traites'] += 1
            if identifiant in ensembles:
                reussis.append(identifiant)
            else:
                _echec(
                    resume, identifiant,
                    "Projet introuvable ou accès refusé.",
                )
        if not autorises:
            continue

        try:
            ids_taches = db.session.query(Task.id).filter(
                Task.project_id.in_(autorises)
            )
            Dependency.query.filter(
                or_(
                    Dependency.successor_id.in_(ids_taches),
                    Dependency.predecessor_id.in_(ids_taches),
                )
            ).delete(synchronize_session=False)
            for modele in (Task, ProjectAccess, Scenario, AlerteRisque):
                modele.query.filter(modele.project_id.in_(autorises)).delete(
                    synchronize_session=False
                )
            Project.query.filter(
                Project.id.in_(autorises), Project.user_id == proprietaire_id
            ).delete(synchronize_session=False)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            for identifiant in autorises:
                _echec(
                    resume, identifiant,
                    f"Suppression impossible : {_message_erreur(exc)}",
                )
            reussis = [i for i in reussis if i not in ensembles]

    resume['reussis'] = len(reussis)
    return resume


def exporter_projets(ids, user_id=None, **kwargs):
    """Exporte en CSV (UTF-8 avec BOM) les projets appartenant à ``user_id``.

    En plus du résumé standard, le dictionnaire contient ``donnees`` (``bytes``)
    et ``nom_fichier``. Les projets invalides sont signalés dans ``erreurs`` et
    n'apparaissent pas dans le fichier.
    """
    proprietaire_id = _proprietaire(user_id, kwargs)
    identifiants = _identifiants(ids)
    resume = _resume()
    resume['donnees'] = b'\xef\xbb\xbf'
    resume['nom_fichier'] = 'projets_export.csv'

    colonnes = (
        'Projet', 'Description', 'Statut', 'Date de référence',
        'Tâches', 'Durée totale (j)', 'Chemins critiques',
    )
    lignes = [list(colonnes)]

    if proprietaire_id is None:
        _controle_refuse(resume, identifiants)
    else:
        for lot in _lots(identifiants):
            projets = _charger_lot(lot, proprietaire_id)
            par_id = {projet.id: projet for projet in projets}
            for identifiant in lot:
                resume['traites'] += 1
                projet = par_id.get(identifiant)
                if projet is None:
                    _echec(
                        resume, identifiant,
                        "Projet introuvable ou accès refusé.",
                    )
                    continue
                try:
                    taches = project_to_taches(projet)
                    resultat = compute(taches)
                    lignes.append([
                        projet.name or '',
                        projet.description or '',
                        projet.status or '',
                        str(projet.date_reference or ''),
                        len(taches),
                        resultat.duree_totale,
                        ' | '.join(
                            ' → '.join(chemin)
                            for chemin in resultat.chemins_critiques
                        ),
                    ])
                except ValidationError as exc:
                    _echec(resume, identifiant, _message_erreur(exc))
                except Exception as exc:
                    _echec(resume, identifiant, _message_erreur(exc))
                else:
                    resume['reussis'] += 1

    tampon = io.StringIO()
    ecrivain = csv.writer(tampon, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    ecrivain.writerows(lignes)
    resume['donnees'] += tampon.getvalue().encode('utf-8')
    return resume
