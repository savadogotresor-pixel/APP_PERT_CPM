"""Administration technique minimale de l'application PERT/CPM.

Ce blueprint est autonome : il n'écrit pas dans ``app.py`` et s'appuie sur
les modèles existants. Tous les contrôles de rôle sont appliqués côté serveur
via :func:`admin_required` ; le simple fait de masquer un lien dans un template
n'est jamais considéré comme une protection.
"""

from __future__ import annotations

from functools import wraps

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
from sqlalchemy import func

from extensions import db
from models import AuditLog, Project, Scenario, Task, User

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

STATUT_ACTIF = 'actif'
STATUT_ARCHIVE = 'archive'

LIMITE_TABLEAU_DE_BORD = 10
LIMITE_JOURNAL = 100

ACTION_ROLE_OCTROYE = 'admin_role_octroye'
ACTION_ROLE_RETIRE = 'admin_role_retire'
ACTION_RETRAIT_PROPRE_REFUSE = 'admin_retrait_propre_statut_refuse'


# ---------------------------------------------------------------------------
# Contrôle d'accès
# ---------------------------------------------------------------------------

def admin_required(vue):
    """Exige un utilisateur connecté **et** administrateur.

    L'ordre des décorateurs est important : ``login_required`` gère la
    redirection vers la connexion, puis le rôle est vérifié côté serveur.
    Un utilisateur authentifié mais non administrateur reçoit un 403.
    """

    @wraps(vue)
    @login_required
    def decorated(*args, **kwargs):
        if not getattr(current_user, 'is_admin', False):
            abort(403)
        return vue(*args, **kwargs)

    return decorated


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _compte(modele, *filtres):
    """Compte les lignes d'un modèle, éventuellement filtrées."""
    requete = db.session.query(func.count(modele.id))
    if filtres:
        requete = requete.filter(*filtres)
    return requete.scalar() or 0


def _journal(action, details):
    """Ajoute une entrée d'audit à la session courante (commit par l'appelant)."""
    db.session.add(
        AuditLog(user_id=current_user.id, action=action, details=str(details))
    )


def _journal_et_commit(action, details):
    """Journalise puis valide la transaction ; retourne ``True`` en cas de succès."""
    _journal(action, details)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return False
    return True


def _derniers_audits(limite):
    """Retourne les dernières entrées, l'id servant de départage en cas d'égalité."""
    return (
        AuditLog.query
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limite)
        .all()
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@admin_bp.route('/')
@admin_required
def index():
    """Tableau de bord : compteurs et dernières actions journalisées."""
    statistiques = {
        'utilisateurs': _compte(User),
        'projets_actifs': _compte(Project, Project.status == STATUT_ACTIF),
        'projets_archives': _compte(Project, Project.status == STATUT_ARCHIVE),
        'taches': _compte(Task),
        'scenarios': _compte(Scenario),
        'audits': _compte(AuditLog),
    }
    return render_template(
        'admin/index.html',
        statistiques=statistiques,
        audits=_derniers_audits(LIMITE_TABLEAU_DE_BORD),
        limite=LIMITE_TABLEAU_DE_BORD,
    )


@admin_bp.route('/utilisateurs')
@admin_required
def users():
    """Liste des comptes avec bascule du rôle administrateur par formulaire POST."""
    nb_projets = dict(
        db.session.query(Project.user_id, func.count(Project.id))
        .group_by(Project.user_id)
        .all()
    )
    comptes = User.query.order_by(User.username.asc(), User.id.asc()).all()
    return render_template(
        'admin/users.html',
        users=comptes,
        nb_projets=nb_projets,
    )


@admin_bp.route('/utilisateurs/<int:user_id>/admin', methods=['POST'])
@admin_required
def toggle_admin(user_id):
    """Accorde ou retire le rôle admin, en interdisant l'auto-rétrogradation."""
    cible = db.session.get(User, user_id)
    if cible is None:
        abort(404)

    auteur = current_user.username

    # Un administrateur ne peut pas se retirer lui-même ce rôle : cela
    # pourrait verrouiller l'instance sans aucun compte privilégié restant.
    if cible.id == current_user.id and cible.is_admin:
        succes = _journal_et_commit(
            ACTION_RETRAIT_PROPRE_REFUSE,
            f"Tentative de retrait du statut administrateur de {cible.username} "
            f"(id={cible.id}) par {auteur} (id={current_user.id}) : refusée.",
        )
        if succes:
            flash(
                "Vous ne pouvez pas retirer votre propre statut administrateur.",
                'error',
            )
        else:
            flash(
                "Retrait refusé, mais l'incident n'a pas pu être journalisé.",
                'error',
            )
        return redirect(url_for('admin.users'))

    ancien_statut = bool(cible.is_admin)
    nouveau_statut = not ancien_statut
    cible.is_admin = nouveau_statut

    action = ACTION_ROLE_OCTROYE if nouveau_statut else ACTION_ROLE_RETIRE
    details = (
        f"Utilisateur {cible.username} (id={cible.id}) : "
        f"administrateur {ancien_statut} -> {nouveau_statut}, "
        f"modifié par {auteur} (id={current_user.id})."
    )

    _journal(action, details)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Impossible de modifier le statut administrateur.", 'error')
    else:
        verbe = 'accordé à' if nouveau_statut else 'retiré à'
        flash(f"Statut administrateur {verbe} {cible.username}.", 'success')

    return redirect(url_for('admin.users'))


@admin_bp.route('/journal')
@admin_required
def journal():
    """Consulte les 100 dernières entrées d'audit, avec filtre optionnel."""
    action = (request.args.get('action') or '').strip()[:100]

    requete = AuditLog.query
    if action:
        requete = requete.filter(AuditLog.action == action)

    journaux = (
        requete
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(LIMITE_JOURNAL)
        .all()
    )

    actions = [
        ligne[0]
        for ligne in (
            db.session.query(AuditLog.action)
            .distinct()
            .order_by(AuditLog.action.asc())
            .all()
        )
        if ligne[0]
    ]

    return render_template(
        'admin/journal.html',
        journaux=journaux,
        actions=actions,
        action=action,
        limite=LIMITE_JOURNAL,
    )
