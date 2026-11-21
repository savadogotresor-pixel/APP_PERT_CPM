"""Gestion assistée IA-4 : risques, actions contrôlées, rapport intelligent.

Ce blueprint complète l'espace projets avec trois parcours strictement
encadrés :

* l'analyse des risques calculée par règles (appréciation IA optionnelle),
  dont les alertes sont persistées et commentables ;
* les actions en langage naturel, prévisualisées puis exécutées uniquement
  après une confirmation explicite (renforcée pour les suppressions) ;
* la synthèse intelligente, datée et sourcée, fondée sur les seuls chiffres
  du moteur ``logic.pert.compute``.

Le contrôle d'accès est toujours appliqué avant tout calcul, toute lecture
ou toute écriture. Aucune donnée chiffrée n'est acceptée depuis le
navigateur : les propositions sont régénérées côté serveur à partir du texte
et du planning enregistré.
"""

from __future__ import annotations

import logging

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

from extensions import db
from logic.pert import ValidationError, compute
from models import (
    AlerteRisque,
    AuditLog,
    Dependency,
    Project,
    Task,
    project_to_taches,
    user_can_access,
)
from services.actions_ia import preparer_action, valider_action
from services.ia import est_disponible
from services.rapport_intelligent import generer_synthese
from services.risques import analyser_risques

try:  # Werkzeug >= 2.1
    from werkzeug.routing.exceptions import BuildError
except ImportError:  # Werkzeug < 2.1
    from werkzeug.routing import BuildError

try:  # service livré par le lot IA-3, import tolérant
    from services.optimisation_assistee import suggerer_scenarios
except Exception:  # pragma: no cover - dépend du déploiement des lots
    suggerer_scenarios = None

logger = logging.getLogger(__name__)

ia_gestion_bp = Blueprint('ia_gestion', __name__, url_prefix='/projects')

STATUTS_RISQUE = ('confirme', 'rejete', 'en_attente')
TYPES_EXECUTABLES = (
    'modifier_duree',
    'ajouter_tache',
    'ajouter_predecesseur',
    'supprimer_tache',
)
LIBELLES_STATUT = {
    'confirme': 'confirmée',
    'rejete': 'rejetée',
    'en_attente': 'en attente',
}


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _url_ou_defaut(libelles, defaut='#'):
    """Première URL construisible parmi *libelles*, sinon *defaut*."""
    for libelle in libelles:
        try:
            return url_for(libelle)
        except BuildError:
            continue
    return defaut


@ia_gestion_bp.context_processor
def urls_navigation():
    """Liens de navigation robustes pour les trois gabarits autonomes."""
    return {
        'accueil_url': _url_ou_defaut(('accueil', 'main.accueil'), defaut='/'),
        'deconnexion_url': _url_ou_defaut(
            ('auth.logout', 'logout', 'users.logout'), defaut='/logout'
        ),
        'mes_projets_url': _url_ou_defaut(
            ('projects.list_projects',), defaut='/projects/'
        ),
    }


def _projet_ou_404(project_id):
    """Charge le projet puis vérifie les droits avant toute autre opération."""
    projet = Project.query.get_or_404(project_id)
    if not user_can_access(projet, current_user):
        abort(403)
    return projet


def _journal(action, details):
    """Ajoute une trace d'audit sans donnée libre (commit par l'appelant)."""
    db.session.add(AuditLog(
        user_id=current_user.id,
        action=action,
        details=str(details),  # le modèle stocke du texte, pas un JSON
    ))


def _messages_erreur(exception):
    """Découpe les messages agrégés de ``ValidationError``."""
    return [ligne.strip() for ligne in str(exception).split('\n') if ligne.strip()]


def _calculer(projet):
    """Retourne ``(taches, resultat)`` ou ``None`` si le planning est invalide."""
    taches = project_to_taches(projet)
    try:
        resultat = compute(taches)
    except ValidationError as exc:
        for message in _messages_erreur(exc):
            flash(message, 'error')
        return None
    return taches, resultat


def _avec_ia():
    """Disponibilité du fournisseur IA, en mode dégradé si le service échoue."""
    try:
        return bool(est_disponible())
    except Exception:
        return False


def _cle_alerte(code_tache, titre):
    return (code_tache or None, str(titre or ''))


# ---------------------------------------------------------------------------
# 1. Analyse des risques
# ---------------------------------------------------------------------------

def _afficher_risques(projet):
    """Recalcule les alertes, les synchronise en base puis affiche la page."""
    calcul = _calculer(projet)
    if calcul is None:
        return redirect(url_for('projects.detail', project_id=projet.id))
    taches, resultat = calcul

    alertes_calculees = analyser_risques(
        taches,
        resultat.resultats,
        resultat.duree_totale,
        resultat.chemins_critiques,
        successeurs=resultat.successeurs,
        avec_ia=_avec_ia(),
    )

    existantes = AlerteRisque.query.filter_by(project_id=projet.id).all()
    par_cle = {
        _cle_alerte(alerte.code_tache, alerte.titre): alerte
        for alerte in existantes
    }

    affichage = []
    pour_rappel = []
    for donnees in alertes_calculees:
        if not isinstance(donnees, dict):
            continue
        code = donnees.get('code_tache') or None
        titre = str(donnees.get('titre') or 'Alerte sans titre')
        cle = _cle_alerte(code, titre)
        alerte = par_cle.get(cle)
        if alerte is None:
            alerte = AlerteRisque(
                project_id=projet.id,
                code_tache=code,
                titre=titre,
                statut='en_attente',
            )
            db.session.add(alerte)
            par_cle[cle] = alerte

        alerte.niveau = str(donnees.get('niveau') or 'indicatif')
        alerte.justification = str(donnees.get('justification') or '')
        alerte.recommandation = str(donnees.get('recommandation') or '')
        alerte.origine = str(donnees.get('origine') or 'regle')
        appreciation = donnees.get('appreciation_ia')
        alerte.appreciation_ia = str(appreciation) if appreciation else None

        # Données de preuve non persistées (le modèle n'a pas ces colonnes) :
        # elles restent attachées à l'objet pour l'affichage du gabarit.
        alerte.preuve = donnees.get('preuve') or {}
        alerte.elements_justificatifs = (
            donnees.get('elements_justificatifs') or []
        )
        affichage.append(alerte)
        pour_rappel.append(cle)

    affichage.extend(
        alerte for alerte in existantes
        if _cle_alerte(alerte.code_tache, alerte.titre) not in pour_rappel
    )
    db.session.commit()

    return render_template(
        'projects/risques.html',
        projet=projet,
        alertes=affichage,
        duree_totale=resultat.duree_totale,
        chemins_critiques=resultat.chemins_critiques,
    )


def _enregistrer_statut_risque(projet):
    """Applique le statut et le commentaire d'une alerte appartenant au projet."""
    brut_id = str(request.form.get('alerte_id') or '').strip()
    statut = request.form.get('statut') or ''
    commentaire = request.form.get('commentaire') or ''

    if statut not in STATUTS_RISQUE:
        flash("Statut d'alerte invalide : aucune modification n'a été appliquée.", 'error')
        return redirect(url_for('ia_gestion.risques', project_id=projet.id))

    try:
        alerte_id = int(brut_id)
    except (TypeError, ValueError):
        abort(404)
    alerte = AlerteRisque.query.filter_by(
        id=alerte_id, project_id=projet.id
    ).first_or_404()

    alerte.statut = statut
    alerte.commentaire = commentaire
    _journal('IA_RISQUE_STATUT', {
        'project_id': projet.id,
        'alerte_id': alerte.id,
        'statut': statut,
    })
    db.session.commit()
    flash(
        f"Alerte « {alerte.titre} » : statut {LIBELLES_STATUT[statut]} enregistré.",
        'success',
    )
    return redirect(url_for('ia_gestion.risques', project_id=projet.id))


@ia_gestion_bp.route('/<int:project_id>/risques', methods=['GET', 'POST'])
@login_required
def risques(project_id):
    """Affiche et permet de traiter les alertes de risques du projet."""
    projet = _projet_ou_404(project_id)
    if request.method == 'POST':
        return _enregistrer_statut_risque(projet)
    return _afficher_risques(projet)


# ---------------------------------------------------------------------------
# 2. Actions contrôlées en langage naturel
# ---------------------------------------------------------------------------

def _rendre_action(projet, texte='', proposition=None, erreurs=None, valide=None):
    """Rendu unique de la page d'action (prévisualisation ou confirmation).

    ``valide`` distingue les erreurs bloquantes des simples avertissements
    renvoyés par ``valider_action`` : ces derniers ne doivent pas empêcher
    l'exécution d'une action confirmée.
    """
    return render_template(
        'projects/action_assistee.html',
        projet=projet,
        texte=texte or '',
        proposition=proposition,
        erreurs=list(erreurs or []),
        action_valide=valide,
        confirmation_requise=(
            proposition.get('confirmation_requise') if proposition else None
        ),
    )


def _proposition_depuis_texte(texte, taches, resultats):
    """Régénère et valide la proposition : jamais de confiance au client."""
    proposition = preparer_action(texte, taches, resultats)
    valide, erreurs = valider_action(proposition, taches)
    return proposition, bool(valide), list(erreurs or [])


def _dep_est_presente(successor_id, predecessor_id):
    return Dependency.query.filter_by(
        successor_id=successor_id, predecessor_id=predecessor_id
    ).first() is not None


def _executer_action(projet, proposition, valide, erreurs, texte):
    """Exécute la seule opération autorisée et confirmée, puis retrace."""
    if not isinstance(proposition, dict):
        flash("Aucune proposition exploitable : aucune modification appliquée.", 'error')
        return _rendre_action(projet, texte, proposition, erreurs, valide)

    type_action = proposition.get('type')
    cible = proposition.get('cible')
    if type_action not in TYPES_EXECUTABLES:
        flash("Action non autorisée : aucune modification n'a été appliquée.", 'error')
        return _rendre_action(projet, texte, proposition, erreurs, valide)

    if not valide:
        return _rendre_action(projet, texte, proposition, erreurs, valide)

    parametres = proposition.get('parametres') or {}
    par_code = {tache.code: tache for tache in projet.tasks}

    try:
        if type_action == 'modifier_duree':
            tache = par_code.get(cible)
            nouvelle = parametres.get('nouvelle_duree')
            if tache is None:
                raise ValueError("Tâche cible introuvable.")
            if isinstance(nouvelle, bool) or not isinstance(nouvelle, int):
                raise ValueError("Nouvelle durée invalide.")
            tache.duree = nouvelle

        elif type_action == 'ajouter_tache':
            code = str(cible or '').strip()
            duree = parametres.get('duree')
            if not code or code in par_code:
                raise ValueError("Code de tâche absent ou déjà utilisé.")
            if isinstance(duree, bool) or not isinstance(duree, int):
                raise ValueError("Durée invalide.")
            predecesseurs = parametres.get('predecesseurs') or []
            if isinstance(predecesseurs, (str, bytes)) or not isinstance(
                predecesseurs, (list, tuple, set)
            ):
                raise ValueError("Prédécesseurs invalides.")
            nouvelle_tache = Task(
                project_id=projet.id,
                code=code,
                description=str(parametres.get('description') or ''),
                duree=duree,
            )
            db.session.add(nouvelle_tache)
            db.session.flush()
            for code_pred in predecesseurs:
                precedent = par_code.get(code_pred)
                if precedent is None:
                    raise ValueError(
                        f"Le prédécesseur « {code_pred} » est introuvable."
                    )
                if not _dep_est_presente(nouvelle_tache.id, precedent.id):
                    db.session.add(Dependency(
                        successor_id=nouvelle_tache.id,
                        predecessor_id=precedent.id,
                        type_relation='FS',
                    ))

        elif type_action == 'ajouter_predecesseur':
            tache = par_code.get(cible)
            code_pred = parametres.get('predecesseur')
            precedent = par_code.get(code_pred)
            if tache is None or precedent is None:
                raise ValueError("Tâche ou prédécesseur introuvable.")
            if tache.id == precedent.id:
                raise ValueError("Une tâche ne peut pas se précéder elle-même.")
            if not _dep_est_presente(tache.id, precedent.id):
                db.session.add(Dependency(
                    successor_id=tache.id,
                    predecessor_id=precedent.id,
                    type_relation='FS',
                ))

        elif type_action == 'supprimer_tache':
            tache = par_code.get(cible)
            if tache is None:
                raise ValueError("Tâche cible introuvable.")
            # Les dépendances entrantes (successeur = tâche supprimée) sont
            # supprimées par la cascade 'all, delete-orphan' de Task.dependances.
            # Les dépendances sortantes n'ont pas de cascade ORM : on les
            # supprime explicitement avant la tâche (la FK SQL ne suffit pas
            # sur toutes les configurations SQLite).
            for dependance in list(tache.dependances_sortantes):
                db.session.delete(dependance)
            db.session.delete(tache)

        _journal('IA_ACTION_EXECUTEE', {
            'project_id': projet.id,
            'type': type_action,
            'cible': cible,
        })
        db.session.commit()
    except Exception as exc:  # rollback complet : aucune écriture partielle
        db.session.rollback()
        logger.exception("Échec de l'action assistée sur le projet %s", projet.id)
        flash(
            "L'action n'a pas pu être appliquée : aucune modification n'a été "
            "enregistrée (" + str(exc) + ").",
            'error',
        )
        return _rendre_action(projet, texte, proposition, erreurs, valide)

    # Étape 6 : recalcul systématique après modification.
    try:
        duree_finale = compute(project_to_taches(projet)).duree_totale
    except ValidationError as exc:
        logger.warning("Planning non calculable après action : %s", exc)
        flash(
            "Action appliquée et journalisée, mais le planning résultant "
            "n'est pas calculable : corrigez les dépendances.",
            'error',
        )
    else:
        flash(
            "Action appliquée et journalisée. Nouvelle durée calculée : "
            f"{duree_finale} jour{'s' if duree_finale != 1 else ''}.",
            'success',
        )
    return redirect(url_for('projects.detail', project_id=projet.id))


@ia_gestion_bp.route('/<int:project_id>/actions/assister', methods=['GET', 'POST'])
@login_required
def action_assistee(project_id):
    """Prévisualise une demande en langage naturel, puis l'exécute si confirmée."""
    projet = _projet_ou_404(project_id)
    calcul = _calculer(projet)
    if calcul is None:
        return redirect(url_for('projects.detail', project_id=projet.id))
    taches, resultat = calcul

    if request.method == 'GET':
        return _rendre_action(projet)

    texte = request.form.get('texte') or ''
    etape = request.form.get('etape') or ''
    proposition, valide, erreurs = _proposition_depuis_texte(
        texte, taches, resultat.resultats
    )

    if etape == 'preparer':
        # Aucune écriture : simple aperçu lisible et validation serveur.
        return _rendre_action(projet, texte, proposition, erreurs, valide)

    if etape != 'executer':
        flash("Étape inconnue : aucune modification n'a été appliquée.", 'error')
        return _rendre_action(projet, texte, proposition, erreurs, valide)

    confirmation_attendue = (
        proposition.get('confirmation_requise') if isinstance(proposition, dict) else None
    )
    if request.form.get('confirmer') != confirmation_attendue:
        flash(
            "Confirmation explicite absente ou incorrecte : aucun changement "
            "n'a été appliqué.",
            'error',
        )
        return _rendre_action(projet, texte, proposition, erreurs, valide)

    return _executer_action(projet, proposition, valide, erreurs, texte)


# ---------------------------------------------------------------------------
# 3. Rapport intelligent
# ---------------------------------------------------------------------------

@ia_gestion_bp.route('/<int:project_id>/rapport/intelligent', methods=['GET'])
@login_required
def rapport_intelligent(project_id):
    """Synthèse datée et sourcée, construite sur les seuls chiffres du moteur."""
    projet = _projet_ou_404(project_id)
    calcul = _calculer(projet)
    if calcul is None:
        return redirect(url_for('projects.detail', project_id=projet.id))
    taches, resultat = calcul

    risques = analyser_risques(
        taches,
        resultat.resultats,
        resultat.duree_totale,
        resultat.chemins_critiques,
        successeurs=resultat.successeurs,
        avec_ia=_avec_ia(),
    )

    scenarios = []
    if suggerer_scenarios is not None:
        try:
            proposition = suggerer_scenarios(
                taches,
                resultat.resultats,
                resultat.duree_totale,
                resultat.chemins_critiques,
                resultat.successeurs,
            )
            if isinstance(proposition, (list, tuple)):
                scenarios = list(proposition)
            elif isinstance(proposition, dict):
                scenarios = [proposition]
        except Exception:
            logger.exception("Scénarios indisponibles pour le rapport intelligent.")
            flash(
                "Les scénarios d'optimisation n'ont pas pu être générés : "
                "la synthèse reste fondée sur le planning enregistré.",
                'error',
            )
    else:
        flash(
            "Le service d'optimisation assistée est indisponible : aucun "
            "scénario n'est commenté dans cette synthèse.",
            'error',
        )

    donnees_manquantes = None
    if projet.date_reference is None:
        donnees_manquantes = ['date de début non renseignée']

    synthese = generer_synthese(
        projet.name,
        taches,
        resultat.resultats,
        resultat.duree_totale,
        resultat.chemins_critiques,
        risques=risques,
        scenarios=scenarios,
        donnees_manquantes=donnees_manquantes,
    )

    _journal('IA_RAPPORT_INTELLIGENT', {'project_id': projet.id})
    db.session.commit()

    return render_template(
        'projects/rapport_intelligent.html',
        projet=projet,
        synthese=synthese,
    )
