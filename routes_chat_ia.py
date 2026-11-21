"""Espace de discussion libre avec l'IA maison (sans API externe ni quota).

Toutes les données chiffrées sont recalculées côté serveur à chaque message :
``project_to_taches`` puis ``compute``. Le client n'envoie ni résultat PERT ni
historique ; seul son texte (1000 caractères maximum) est accepté. L'historique
de session est propre à ce blueprint et ne modifie pas celui de l'assistant
historique ``/ia/projets/<id>/assistant``.

Le journal d'audit ne contient ni le message ni la réponse : seuls l'identifiant
du projet et l'intention (identifiant technique fermé) sont conservés.
"""

from __future__ import annotations

import logging

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from extensions import db
from logic.pert import ValidationError, compute
from models import AuditLog, Project, project_to_taches, user_can_access
from services.chat_ia import HISTORIQUE_MAX, MESSAGE_MAX, repondre

logger = logging.getLogger(__name__)

chat_ia_bp = Blueprint('chat_ia', __name__, url_prefix='/ia')

CLE_HISTORIQUE = 'chat_ia_historique'


def _entier(valeur):
    """Convertit un identifiant de projet sans jamais faire confiance au client."""
    try:
        nombre = int(str(valeur).strip())
    except (TypeError, ValueError):
        return None
    return nombre if nombre > 0 else None


def _projet_autorise(project_id):
    """Charge le projet après contrôle de droits effectif."""
    projet = db.session.get(Project, project_id)
    if projet is None:
        abort(404)
    if not user_can_access(projet, current_user):
        abort(403)
    return projet


def _projets_accessibles():
    """Liste des projets que l'utilisateur connecté peut consulter."""
    projets = []
    try:
        for candidat in Project.query.order_by(
            Project.created_at.desc(), Project.id.desc()
        ):
            if user_can_access(candidat, current_user):
                projets.append(candidat)
    except Exception:
        db.session.rollback()
        logger.exception("Impossible de lister les projets du chat IA.")
        return []
    return projets


def _historique(project_id):
    """Retourne l'historique de session borné pour ce projet."""
    cle = str(project_id)
    historiques = session.get(CLE_HISTORIQUE)
    if not isinstance(historiques, dict):
        historiques = {}
    messages = historiques.get(cle)
    if not isinstance(messages, list):
        messages = []
    messages = [
        message for message in messages[-HISTORIQUE_MAX:]
        if isinstance(message, dict)
    ][-HISTORIQUE_MAX:]
    historiques[cle] = messages
    session[CLE_HISTORIQUE] = historiques
    session.modified = True
    return messages


def _ajouter_historique(project_id, role, texte):
    """Ajoute un tour de conversation, en bornant texte et nombre de tours."""
    messages = list(_historique(project_id))
    messages.append({'role': role, 'texte': str(texte or '').strip()[:MESSAGE_MAX]})
    historiques = dict(session.get(CLE_HISTORIQUE) or {})
    historiques[str(project_id)] = messages[-HISTORIQUE_MAX:]
    session[CLE_HISTORIQUE] = historiques
    session.modified = True


def _donnees_calculees(projet):
    """Recalcule le planning : aucune valeur calculée n'est acceptée du client."""
    taches = project_to_taches(projet)
    return taches, compute(taches)


def _projet_public(projet):
    """Données déclaratives transmises au moteur, sans identifiant chiffré."""
    return {'name': projet.name, 'description': projet.description}


@chat_ia_bp.route('/discussion', methods=['GET'])
@login_required
def discussion():
    """Page de discussion libre, avec sélecteur de projet."""
    projets = _projets_accessibles()
    brut = request.args.get('projet')
    projet = None
    if brut is not None and str(brut).strip():
        projet_id = _entier(brut)
        if projet_id is None:
            abort(400)
        projet = _projet_autorise(projet_id)
    elif projets:
        projet = projets[0]

    projet_id = projet.id if projet is not None else None
    return render_template(
        'ia/discussion.html',
        projets=projets,
        projet=projet,
        projet_id=projet_id,
        historique=_historique(projet_id) if projet_id is not None else [],
        message_url=url_for('chat_ia.message'),
        historique_url=url_for('chat_ia.historique'),
    )


@chat_ia_bp.route('/discussion/message', methods=['POST'])
@login_required
def message():
    """Répond à un message libre, à partir du seul planning recalculé."""
    payload = request.get_json(silent=True)
    est_json = isinstance(payload, dict)
    if est_json:
        message_brut = payload.get('message')
        projet_brut = payload.get('projet_id')
    else:
        message_brut = request.form.get('message')
        projet_brut = request.form.get('projet_id')

    texte = str(message_brut or '').strip()[:MESSAGE_MAX]
    if not texte:
        return jsonify({'erreur': 'Le message ne peut pas être vide.'}), 400

    projet_id = _entier(projet_brut)
    if projet_id is None:
        return jsonify({'erreur': 'Projet invalide.'}), 400
    projet = db.session.get(Project, projet_id)
    if projet is None:
        return jsonify({'erreur': 'Projet invalide.'}), 400
    if not user_can_access(projet, current_user):
        abort(403)

    try:
        taches, resultat = _donnees_calculees(projet)
    except ValidationError as exc:
        details = [ligne.strip() for ligne in str(exc).split('\n') if ligne.strip()]
        return jsonify({
            'erreur': "Le planning de ce projet n'est pas calculable.",
            'details': details,
        }), 400

    historique = _historique(projet_id)
    reponse = repondre(
        texte,
        taches,
        resultat,
        projet=_projet_public(projet),
        historique=historique,
    )

    # Audit sans contenu libre : ni question, ni réponse, ni donnée du planning.
    try:
        db.session.add(AuditLog(
            user_id=current_user.id,
            action='CHAT_IA',
            details=str({
                'project_id': projet.id,
                'intention': reponse.get('intention', 'hors_cadre'),
            }),
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Impossible d'écrire l'audit du chat IA.")

    _ajouter_historique(projet_id, 'user', texte)
    _ajouter_historique(projet_id, 'assistant', reponse.get('texte', ''))

    return jsonify({
        'texte': reponse.get('texte', ''),
        'intention': reponse.get('intention', 'hors_cadre'),
        'sources': reponse.get('sources', []),
        'projet_id': projet.id,
    })


@chat_ia_bp.route('/discussion/historique', methods=['GET'])
@login_required
def historique():
    """Historique de la session courante pour le projet demandé."""
    brut = request.args.get('projet', request.args.get('projet_id'))
    projet_id = _entier(brut)
    if projet_id is None:
        return jsonify({'erreur': 'Projet invalide.'}), 400
    _projet_autorise(projet_id)
    return jsonify({
        'projet_id': projet_id,
        'historique': _historique(projet_id),
    })
