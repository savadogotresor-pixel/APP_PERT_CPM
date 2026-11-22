"""Catalogue de projets PERT/CPM réservé aux utilisateurs connectés.

La consultation des modèles et l'import dans l'espace personnel sont protégés
par Flask-Login ; l'import est en plus couvert par la protection CSRF globale
de l'application.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from logic import catalogue as catalogue_logic
from logic.pert import compute
from models import project_to_taches

try:
    from werkzeug.routing.exceptions import BuildError
except ImportError:  # Werkzeug < 2.1
    from werkzeug.routing import BuildError


catalogue_bp = Blueprint('catalogue', __name__, url_prefix='/catalogue')


def _url_ou_defaut(*endpoints, defaut='#'):
    """Retourne la première URL d'endpoint disponible, sinon *defaut*."""
    for endpoint in endpoints:
        try:
            return url_for(endpoint)
        except BuildError:
            continue
    return defaut


@catalogue_bp.context_processor
def urls_navigation():
    """Liens de navigation stables, même si un blueprint est indisponible."""
    return {
        'accueil_url': _url_ou_defaut('accueil', 'main.accueil', defaut='/'),
        'mes_projets_url': _url_ou_defaut(
            'projects.list_projects', defaut='/projects'
        ),
        'profil_url': _url_ou_defaut('auth.profile', 'profile', defaut='/profile'),
        'deconnexion_url': _url_ou_defaut(
            'auth.logout', 'logout', defaut='/logout'
        ),
    }


@catalogue_bp.route('/')
@login_required
def index():
    """Liste des modèles de projets, avec leur durée calculée."""
    projets = catalogue_logic.CATALOGUE
    plannings = {projet['id']: compute(projet['taches']) for projet in projets}
    durees = [planning.duree_totale for planning in plannings.values()]
    stats = {
        'projets': len(projets),
        'categories': len({projet['categorie'] for projet in projets}),
        'duree_moyenne': round(sum(durees) / len(durees)) if durees else 0,
    }
    return render_template(
        'catalogue/index.html', projets=projets, plannings=plannings, stats=stats
    )


def _rendre_detail(projet, valeurs=None, erreurs=None):
    """Rend la fiche en tenant compte de l'exemplaire personnel de l'utilisateur."""
    copie = catalogue_logic.trouver_copie(projet['id'], current_user.id)
    taches_affichees = (
        project_to_taches(copie) if copie is not None else projet['taches']
    )
    resultat = compute(taches_affichees)
    if valeurs is None:
        valeurs = {
            'code': catalogue_logic.prochain_code(taches_affichees),
            'description': '',
            'duree': '1',
            'predecesseurs': '',
        }
    return render_template(
        'catalogue/detail.html',
        projet=projet,
        resultat=resultat,
        taches_affichees=taches_affichees,
        copie=copie,
        est_personnalise=copie is not None,
        valeurs=valeurs,
        erreurs=erreurs or [],
    )


@catalogue_bp.route('/<projet_id>')
@login_required
def detail(projet_id):
    """Fiche d'un projet : tableau PERT et chemin critique."""
    projet = catalogue_logic.get_projet(projet_id)
    if projet is None:
        abort(404)
    return _rendre_detail(projet)


@catalogue_bp.route('/<projet_id>/taches', methods=['POST'])
@login_required
def ajouter_tache(projet_id):
    """Ajoute une tâche à l'exemplaire personnel du modèle de catalogue."""
    projet = catalogue_logic.get_projet(projet_id)
    if projet is None:
        abort(404)

    valeurs = {
        'code': (request.form.get('code') or '').strip(),
        'description': (request.form.get('description') or '').strip(),
        'duree': (request.form.get('duree') or '').strip(),
        'predecesseurs': (request.form.get('predecesseurs') or '').strip(),
    }
    try:
        copie, resultat, tache = catalogue_logic.ajouter_tache(
            projet_id,
            current_user.id,
            valeurs['code'],
            valeurs['description'],
            valeurs['duree'],
            valeurs['predecesseurs'],
        )
    except ValueError as exc:
        erreurs = [ligne.strip() for ligne in str(exc).split('\n') if ligne.strip()]
        return _rendre_detail(projet, valeurs=valeurs, erreurs=erreurs)

    flash(
        f"Tâche « {tache.code} » ajoutée à votre exemplaire de "
        f"« {projet['titre']} » : durée PERT recalculée à "
        f"{resultat.duree_totale} jour(s).",
        'success',
    )
    return redirect(
        url_for('catalogue.detail', projet_id=projet_id, _anchor='ajout-tache')
    )


@catalogue_bp.route('/<projet_id>/optimiser', methods=['POST'])
@login_required
def optimiser(projet_id):
    """Copie un modèle dans le compte connecté puis ouvre l'optimisation.

    Si l'utilisateur a déjà enrichi ce modèle via l'ajout de tâches, c'est cet
    exemplaire personnel qui est réutilisé : les tâches ajoutées ne sont jamais
    perdues au profit d'une nouvelle copie du modèle d'origine.
    """
    projet = catalogue_logic.trouver_copie(projet_id, current_user.id)
    if projet is None:
        try:
            projet = catalogue_logic.optimiser(projet_id, current_user.id)
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('catalogue.index'))
        verbe = 'créé'
    else:
        verbe = 'repris'

    flash(
        f"Projet « {projet.name} » {verbe} avec {len(projet.tasks)} tâches. "
        "Ajustez maintenant les durées simulées.",
        'success',
    )
    return redirect(url_for('projects.optimisation', project_id=projet.id))
