"""Route HTTP de l'optimisation assistée (lot IA-3, cahier §19.4).

Cette page est strictement contemplative : elle affiche des scénarios
recalculés par le moteur PERT/CPM et n'écrit jamais dans le planning de
référence. Le contrôle d'accès est effectué avant tout calcul, et le service
``services.optimisation_assistee`` est importé de façon tolérante afin que
l'application reste disponible en mode dégradé s'il n'est pas livré.
"""

from __future__ import annotations

import logging

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    url_for,
)
from flask_login import current_user, login_required

from extensions import db
from logic.pert import ValidationError, compute
from models import AuditLog, Project, project_to_taches, user_can_access

try:  # Werkzeug >= 2.1
    from werkzeug.routing.exceptions import BuildError
except ImportError:  # Werkzeug < 2.1
    from werkzeug.routing import BuildError

logger = logging.getLogger(__name__)

try:
    from services.optimisation_assistee import suggerer_scenarios
except Exception:  # service en cours de livraison ou dépendance manquante
    logger.warning(
        "services.optimisation_assistee indisponible : mode dégradé activé.",
        exc_info=True,
    )
    suggerer_scenarios = None


optimisation_ia_bp = Blueprint('optimisation_ia', __name__, url_prefix='/projects')


def _url_ou_defaut(libelles, defaut='#'):
    """Première URL construisible parmi *libelles*, sinon *defaut*."""
    for libelle in libelles:
        try:
            return url_for(libelle)
        except BuildError:
            continue
    return defaut


@optimisation_ia_bp.context_processor
def urls_navigation():
    """Liens de navigation robustes pour le template autonome de la page."""
    return {
        'accueil_url': _url_ou_defaut(('accueil', 'main.accueil'), defaut='/'),
        'deconnexion_url': _url_ou_defaut(
            ('auth.logout', 'logout', 'users.logout'), defaut='/logout'
        ),
        'mes_projets_url': _url_ou_defaut(('projects.list_projects',), defaut='/projects/'),
    }


def _journaliser(project_id, nombre_scenarios):
    """Écrit un audit sans donnée sensible (identifiant et count uniquement)."""
    details = {'project_id': project_id, 'nb_scenarios': nombre_scenarios}
    try:
        db.session.add(AuditLog(
            user_id=current_user.id,
            action='IA_OPTIMISATION',
            details=str(details),  # le modèle stocke un texte, pas un JSON
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Impossible d'écrire l'audit d'optimisation assistée.")


@optimisation_ia_bp.route('/<int:project_id>/optimisation/assistee', methods=['GET'])
@login_required
def assister(project_id):
    """Affiche des scénarios d'optimisation recalculés, sans rien appliquer."""
    projet = Project.query.get_or_404(project_id)
    if not user_can_access(projet, current_user):
        abort(403)

    try:
        taches = project_to_taches(projet)
        resultat = compute(taches)
    except ValidationError as exc:
        for ligne in str(exc).split('\n'):
            if ligne.strip():
                flash(ligne.strip(), 'error')
        return redirect(url_for('projects.detail', project_id=project_id))

    scenarios = []
    if suggerer_scenarios is None:
        flash(
            "Le service d'optimisation assistée est indisponible : la synthèse "
            "PERT/CPM reste affichée, aucun scénario n'a pu être proposé.",
            'error',
        )
    else:
        try:
            proposition = suggerer_scenarios(
                taches,
                resultat.resultats,
                resultat.duree_totale,
                resultat.chemins_critiques,
                resultat.successeurs,
            )
            if isinstance(proposition, (list, tuple)):
                scenarios = [item for item in proposition if isinstance(item, dict)]
            else:
                flash(
                    "Le service d'optimisation assistée a renvoyé un résultat "
                    "inattendu : aucun scénario n'est affiché.",
                    'error',
                )
        except Exception:
            logger.exception("Le service d'optimisation assistée a échoué.")
            flash(
                "Le service d'optimisation assistée n'a pas pu calculer de "
                "scénarios : la synthèse PERT/CPM reste disponible.",
                'error',
            )

    _journaliser(projet.id, len(scenarios))

    return render_template(
        'projects/optimisation_assistee.html',
        projet=projet,
        scenarios=scenarios,
        duree_totale=resultat.duree_totale,
        chemins_critiques=resultat.chemins_critiques,
        taches=taches,
    )
