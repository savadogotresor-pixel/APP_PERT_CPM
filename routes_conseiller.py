"""Route du conseiller IA maison (moteur de règles déterministe).

Le projet est chargé puis recalculé par le moteur canonique ``logic.pert``.
Aucun chiffre n'est accepté depuis le navigateur et aucun service externe
n'est consulté : la page affiche les conseils tracés produits par
:func:`services.conseiller.generer_conseils`.
"""

from __future__ import annotations

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required

from logic.pert import ValidationError, compute
from models import Project, project_to_taches
from services.conseiller import generer_conseils

conseiller_bp = Blueprint('conseiller', __name__, url_prefix='/projects')


def _projet_proprietaire_ou_404(project_id):
    """Charge un projet avec un contrôle de propriété strict.

    Contrairement aux vues partagées de ``projects``, le conseiller est limité
    au propriétaire : un accès explicite ou un compte administrateur n'ouvre
    pas cette page. Un projet inexistant ou appartenant à un autre utilisateur
    renvoie systématiquement 404, sans révéler son existence.
    """
    projet = Project.query.filter_by(id=project_id).first()
    if projet is None:
        abort(404)

    proprietaire = getattr(projet, 'user_id', None)
    visiteur = getattr(current_user, 'id', None)
    if proprietaire is None or visiteur is None or proprietaire != visiteur:
        abort(404)
    return projet


@conseiller_bp.route('/<int:project_id>/conseils')
@login_required
def conseils(project_id):
    """Affiche les conseils calculés pour le projet de l'utilisateur connecté."""
    projet = _projet_proprietaire_ou_404(project_id)
    taches = project_to_taches(projet)

    resultat = None
    erreur_calcul = None
    if not taches:
        erreur_calcul = (
            "Aucune tâche n'est enregistrée dans ce projet : ajoutez des tâches "
            "pour obtenir un planning et des conseils."
        )
    else:
        try:
            resultat = compute(taches)
        except ValidationError as exc:
            erreur_calcul = ' ; '.join(
                ligne.strip() for ligne in str(exc).split('\n') if ligne.strip()
            )

    liste_conseils = generer_conseils(resultat, taches) if resultat is not None else []

    return render_template(
        'projects/conseils.html',
        projet=projet,
        taches=taches,
        resultat=resultat,
        conseils=liste_conseils,
        erreur_calcul=erreur_calcul,
        duree_totale=resultat.duree_totale if resultat is not None else None,
        nb_taches=len(resultat.resultats) if resultat is not None else len(taches),
        nb_critiques=(
            sum(1 for r in resultat.resultats.values() if r.get('critique'))
            if resultat is not None else 0
        ),
        chemins_critiques=resultat.chemins_critiques if resultat is not None else [],
    )
