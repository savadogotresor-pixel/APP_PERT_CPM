"""Catalogue public de projets PERT/CPM.

Les fiches sont consultables sans compte. L'import dans l'espace personnel
passe par une route POST protégée par Flask-Login et par la protection CSRF
globale de l'application.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from logic import catalogue as catalogue_logic
from logic.pert import compute

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
def index():
    """Liste publique des modèles de projets, avec leur durée calculée."""
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


@catalogue_bp.route('/<projet_id>')
def detail(projet_id):
    """Fiche publique d'un projet : tableau PERT et chemin critique."""
    projet = catalogue_logic.get_projet(projet_id)
    if projet is None:
        abort(404)
    resultat = compute(projet['taches'])
    return render_template('catalogue/detail.html', projet=projet, resultat=resultat)


@catalogue_bp.route('/<projet_id>/optimiser', methods=['POST'])
@login_required
def optimiser(projet_id):
    """Copie un modèle dans le compte connecté puis ouvre l'optimisation."""
    try:
        projet = catalogue_logic.optimiser(projet_id, current_user.id)
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('catalogue.index'))

    flash(
        f"Projet « {projet.name} » créé avec {len(projet.tasks)} tâches. "
        "Ajustez maintenant les durées simulées.",
        'success',
    )
    return redirect(url_for('projects.optimisation', project_id=projet.id))
