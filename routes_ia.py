"""Blueprint de l'assistant IA conversationnel (lot IA-1, cahier §19).

Le service ``services.ia`` est fourni par un module parallèle : son import est
tolérant afin que l'application (et les tests existants) démarrent même s'il
n'est pas encore livré. Un fallback déterministe, calculé uniquement à partir
des résultats du moteur PERT/CPM, prend le relais dans ce cas ; il n'invente
aucun chiffre et n'effectue aucun appel réseau.

Règles de sécurité appliquées ici :
  * ``user_can_access()`` est vérifié AVANT toute lecture des données du projet
    (§19.9) : un utilisateur non autorisé reçoit 403 et aucun contexte n'est
    construit ;
  * la clé API n'est jamais lue depuis la requête ni renvoyée au navigateur ;
  * l'historique conservé en session est la seule source de vérité (les
    ``historique`` éventuellement envoyés par le client sont ignorés) ;
  * le journal d'audit n'enregistre ni la question ni la réponse complète.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from extensions import db
from logic.pert import ValidationError, compute
from models import (
    AuditLog,
    Dependency,
    Project,
    Task,
    project_to_taches,
    user_can_access,
)

try:  # Werkzeug >= 2.1
    from werkzeug.routing.exceptions import BuildError
except ImportError:  # Werkzeug < 2.1
    from werkzeug.routing import BuildError

logger = logging.getLogger(__name__)

try:
    from services.ia import configuration, est_disponible, repondre
except Exception:  # service en cours de livraison ou dépendance manquante
    logger.warning("services.ia indisponible : mode sans IA activé.", exc_info=True)
    configuration = None
    est_disponible = None
    repondre = None

try:
    from services.wbs import generer_wbs, valider_proposition
except Exception:  # service livré en parallèle : l'app doit toujours démarrer
    logger.warning("services.wbs indisponible : génération WBS désactivée.", exc_info=True)
    generer_wbs = None
    valider_proposition = None


ia_bp = Blueprint('ia', __name__, url_prefix='/ia')

HISTORIQUE_MAX = 20
TEXTE_MAX = 500
FENETRE_SECONDES = 3600
LIMITE_DEFAUT = 60

# Génération WBS (lot IA-2) : bornes des champs libres et du nom du projet.
WBS_OBJECTIF_MAX = 500
WBS_CONTRAINTES_MAX = 1000
WBS_TEXTE_MAX = 2000
WBS_NOM_PROJET_MAX = 120

_compteurs_lock = threading.Lock()
_appels_par_utilisateur = {}  # {identifiant utilisateur: [horodatages time.time()]}


# ---------------------------------------------------------------------------
# Fallback local (uniquement si services.ia n'est pas importable)
# ---------------------------------------------------------------------------

def _horodatage():
    return datetime.now(timezone.utc).isoformat()


def _configuration_fallback():
    config = current_app.config
    return {
        'active': bool(config.get('IA_ENABLED', True)),
        'url': config.get('IA_API_URL'),
        'modele': config.get('IA_MODEL', 'gpt-4o-mini'),
        'timeout': float(config.get('IA_TIMEOUT', 10.0)),
        'max_appels_par_heure': int(
            config.get('IA_MAX_APPELS_PAR_HEURE', LIMITE_DEFAUT)
        ),
    }


def _disponible_fallback():
    configuration_ia = _configuration_fallback()
    return bool(configuration_ia['active'] and current_app.config.get('IA_API_KEY'))


def _repondre_fallback(question, taches, resultats, duree_totale,
                       chemins_critiques, historique=None, successeurs=None):
    """Réponse déterministe : tous les chiffres proviennent du moteur."""
    q = (question or '').casefold()
    if any(m in q for m in ('durée totale', 'duree totale', 'combien de temps',
                            'durée du projet', 'duree du projet')):
        texte = (
            "La durée totale calculée par le moteur PERT/CPM est de "
            f"{duree_totale} jours."
        )
    elif 'critique' in q:
        if chemins_critiques:
            chemins = ' ; '.join(' → '.join(chemin) for chemin in chemins_critiques)
            texte = f"Le chemin critique calculé est : {chemins}."
        else:
            texte = "Aucun chemin critique n'a été calculé pour ce projet."
    elif any(m in q for m in ('simultan', 'parallèle', 'parallele',
                              'en même temps')):
        demarrages = {}
        for code, resultat in (resultats or {}).items():
            demarrages.setdefault(resultat.get('ES', 0), []).append(code)
        groupes = [
            f"{es} j : {', '.join(sorted(codes))}"
            for es, codes in sorted(demarrages.items()) if len(codes) > 1
        ]
        texte = (
            "Tâches partageant la même date de début au plus tôt (ES) : "
            + ' ; '.join(groupes) + "."
        ) if groupes else (
            "Aucune paire de tâches ne partage la même date de début au plus tôt "
            "(ES) dans ce projet."
        )
    elif 'rapport' in q:
        critiques = sum(
            1 for resultat in (resultats or {}).values()
            if resultat.get('critique')
        )
        texte = (
            f"Le projet comporte {len(taches or {})} tâche(s), dont {critiques} "
            f"tâche(s) critique(s), pour une durée totale calculée de "
            f"{duree_totale} jours."
        )
    else:
        texte = (
            "Je peux vous expliquer la durée totale, les tâches critiques, les "
            "tâches simultanées ou le rapport du planning. Durée totale calculée "
            f"par le moteur : {duree_totale} jours."
        )

    return {
        'texte': texte,
        'source': 'moteur',
        'donnees_utilisees': [
            'codes, durées et descriptions des tâches',
            'résultats calculés (ES, EF, LS, LF, marges, criticité)',
        ],
        'donnees_manquantes': [],
        'avertissements': [
            "Mode sans IA actif : réponse déterministe élaborée uniquement à "
            "partir des résultats calculés par le moteur PERT/CPM."
        ],
        'horodatage': _horodatage(),
        'appel_ia': False,
        'tokens': 0,
    }


def _configuration():
    """Configuration publique du service (jamais la clé API)."""
    fonction = configuration or _configuration_fallback
    try:
        donnees = fonction() or {}
    except Exception:
        logger.exception("Impossible de lire la configuration IA.")
        donnees = _configuration_fallback()
    # Le service peut nommer ces clés ``api_url`` / ``model`` ou reprendre le
    # contrat ``url`` / ``modele`` : accepter les deux sans jamais relayer la
    # clé API (``api_key`` n'est pas dans la liste blanche ci-dessous).
    public = {
        'active': donnees.get('active', donnees.get('enabled', False)),
        'url': donnees.get('url') or donnees.get('api_url'),
        'modele': donnees.get('modele') or donnees.get('model'),
        'timeout': donnees.get('timeout'),
        'max_appels_par_heure': donnees.get('max_appels_par_heure'),
    }
    # La configuration du service fait foi ; la configuration Flask ne sert
    # que de repli si la clé est absente.
    config_flask = current_app.config
    if public.get('max_appels_par_heure') is None:
        public['max_appels_par_heure'] = config_flask.get(
            'IA_MAX_APPELS_PAR_HEURE', LIMITE_DEFAUT
        )
    if not public.get('modele'):
        public['modele'] = config_flask.get('IA_MODEL', 'gpt-4o-mini')
    if not public.get('url'):
        public['url'] = config_flask.get('IA_API_URL')
    try:
        public['max_appels_par_heure'] = max(
            1, int(public.get('max_appels_par_heure') or LIMITE_DEFAUT)
        )
    except (TypeError, ValueError):
        public['max_appels_par_heure'] = LIMITE_DEFAUT
    return public


def _est_disponible():
    fonction = est_disponible or _disponible_fallback
    try:
        return bool(fonction())
    except Exception:
        logger.exception("Impossible de déterminer la disponibilité IA.")
        return False


def _liste_public(valeur):
    if valeur is None:
        return []
    if isinstance(valeur, (list, tuple, set)):
        return [str(element) for element in valeur]
    return [str(valeur)]


def _reponse_publique(donnees):
    """Ne renvoie que les clés contractuelles : aucun secret ne peut fuir."""
    if not isinstance(donnees, dict):
        donnees = {}
    source = donnees.get('source')
    if source not in ('moteur', 'ia'):
        source = 'moteur'
    texte = donnees.get('texte')
    texte = texte.strip() if isinstance(texte, str) else ''
    if not texte:
        texte = "Aucune réponse n'a pu être générée à partir du planning calculé."
    horodatage = donnees.get('horodatage')
    if not isinstance(horodatage, str) or not horodatage:
        horodatage = _horodatage()
    tokens = donnees.get('tokens', 0)
    try:
        tokens = int(tokens)
    except (TypeError, ValueError):
        tokens = 0
    return {
        'texte': texte,
        'source': source,
        'donnees_utilisees': _liste_public(donnees.get('donnees_utilisees')),
        'donnees_manquantes': _liste_public(donnees.get('donnees_manquantes')),
        'avertissements': _liste_public(donnees.get('avertissements')),
        'horodatage': horodatage,
        'appel_ia': bool(donnees.get('appel_ia')),
        'tokens': tokens,
    }


# ---------------------------------------------------------------------------
# Données du projet et historique
# ---------------------------------------------------------------------------

def _projet_autorise(project_id):
    """Charge le projet APRES contrôle de droits effectif (§19.9)."""
    projet = Project.query.filter_by(id=project_id).first()
    if projet is None:
        abort(404)
    if not user_can_access(projet, current_user):
        abort(403)
    return projet


def _donnees_calculees(projet):
    taches = project_to_taches(projet)
    resultat = compute(taches)  # lève ValidationError si le graphe est invalide
    return {
        'taches': taches,
        'resultats': resultat.resultats,
        'duree_totale': resultat.duree_totale,
        'chemins_critiques': resultat.chemins_critiques,
        'successeurs': resultat.successeurs,
    }


def _historique(project_id):
    """Retourne les 20 derniers messages de session pour ce projet."""
    cle = str(project_id)
    historiques = session.setdefault('ia_historique', {})
    if not isinstance(historiques, dict):
        historiques = {}
        session['ia_historique'] = historiques
    messages = historiques.get(cle)
    if not isinstance(messages, list):
        messages = []
    messages = [
        message for message in messages[-HISTORIQUE_MAX:]
        if isinstance(message, dict)
    ]
    historiques[cle] = messages
    session.modified = True
    return messages


def _ajouter_historique(project_id, role, texte):
    messages = list(_historique(project_id))
    messages.append({'role': role, 'texte': str(texte)[:TEXTE_MAX]})
    session['ia_historique'][str(project_id)] = messages[-HISTORIQUE_MAX:]
    session.modified = True


def _consommer_appel(identifiant, limite):
    """Limitation en mémoire (par processus) : au plus *limite* appels/heure."""
    maintenant = time.time()
    with _compteurs_lock:
        dates = [
            date for date in _appels_par_utilisateur.get(identifiant, ())
            if maintenant - date < FENETRE_SECONDES
        ]
        if len(dates) >= limite:
            _appels_par_utilisateur[identifiant] = dates
            return False
        dates.append(maintenant)
        _appels_par_utilisateur[identifiant] = dates
        return True


# ---------------------------------------------------------------------------
# Génération WBS (lot IA-2) : helpers de formulaire et d'aperçu
# ---------------------------------------------------------------------------

def _texte_wbs(valeur, limite):
    """Normalise un champ libre non fiable (une ligne, longueur bornée)."""
    chaine = '' if valeur is None else str(valeur)
    chaine = ''.join(c for c in chaine if c in '\t' or ord(c) >= 32)
    return chaine.strip()[:limite]


def _liste_predecesseurs(brut):
    """Convertit « A, B » en liste ordonnée de codes non vides."""
    if brut is None:
        return []
    resultat = []
    for morceau in str(brut).split(','):
        propre = morceau.strip()
        if propre and propre not in resultat:
            resultat.append(propre)
    return resultat


def _indices_taches(form):
    """Indices globaux des tâches soumises, déduits des seuls champs code_*."""
    indices = set()
    for cle in form.keys():
        correspondance = re.fullmatch(r'code_(\d+)', str(cle))
        if correspondance:
            indices.add(int(correspondance.group(1)))
    return sorted(indices)


def _avertissements_formulaire(form):
    """Relit les avertissements affichés dans l'aperçu (affichage uniquement)."""
    avertissements = []
    for cle in sorted(form.keys(), key=str):
        correspondance = re.fullmatch(r'avertissement_(\d+)', str(cle))
        if correspondance:
            texte = _texte_wbs(form.get(cle), WBS_TEXTE_MAX)
            if texte:
                avertissements.append(texte)
    return avertissements


def _reconstruire_wbs(form, objectif, contraintes):
    """Reconstruit la WBS soumise en relisant TOUS les champs de chaque tâche.

    Les champs cachés ne servent qu'aux métadonnées d'affichage (phases,
    livrables, hypothèses). La validation porte exclusivement sur les quatre
    champs éditables relus ici : code, description, durée, prédécesseurs.
    """
    phases = {}
    ordre_phases = []

    for index in _indices_taches(form):
        phase_brute = (form.get(f'phase_index_{index}') or '0').strip()
        try:
            phase_index = int(phase_brute)
        except ValueError:
            phase_index = 0
        nom_phase = _texte_wbs(form.get(f'phase_{index}'), 80) or 'Tâches proposées'
        cle = (phase_index, nom_phase)
        if cle not in phases:
            phases[cle] = {'nom': nom_phase, 'livrables': [], 'taches': []}
            ordre_phases.append(cle)

        duree_brute = (form.get(f'duree_{index}') or '').strip()
        if re.fullmatch(r'[+-]?\d+', duree_brute):
            duree = int(duree_brute)
        else:
            duree = None  # ``valider_proposition`` signalera la durée invalide

        phases[cle]['taches'].append({
            'code': _texte_wbs(form.get(f'code_{index}'), 40),
            'description': _texte_wbs(
                form.get(f'description_{index}'), WBS_TEXTE_MAX
            ),
            'duree': duree,
            'predecesseurs': _liste_predecesseurs(
                form.get(f'predecesseurs_{index}')
            ),
            'jalon': duree == 0,
        })

    for cle in ordre_phases:
        brut_livrables = form.get(f'livrables_{cle[0]}') or ''
        phases[cle]['livrables'] = [
            morceau.strip()[:240]
            for morceau in str(brut_livrables).split(' ;; ')
            if morceau.strip()
        ]

    source = _texte_wbs(form.get('source'), 20)
    if source not in ('regles', 'ia'):
        source = 'regles'

    return {
        'objectif': objectif,
        'contraintes': contraintes,
        'hypotheses': _texte_wbs(form.get('hypotheses'), WBS_TEXTE_MAX),
        'limites': _texte_wbs(form.get('limites'), WBS_TEXTE_MAX),
        'source': source,
        'avertissements': _avertissements_formulaire(form),
        'phases': [phases[cle] for cle in ordre_phases],
    }


def _apercu_wbs(wbs):
    """Aplatit une proposition pour le gabarit (indices de champs globaux)."""
    donnees = wbs if isinstance(wbs, dict) else {}
    phases = []
    jalons = []
    livrables = []
    index = 0

    brut_phases = donnees.get('phases') or []
    if not isinstance(brut_phases, (list, tuple)):
        brut_phases = []

    for phase_index, phase in enumerate(brut_phases):
        if not isinstance(phase, dict):
            continue
        nom_phase = _texte_wbs(phase.get('nom'), 80) or 'Tâches proposées'
        livrables_phase = [
            _texte_wbs(element, 240) for element in _liste_public(phase.get('livrables'))
        ]
        livrables_phase = [element for element in livrables_phase if element]
        livrables.extend(livrables_phase)
        lignes = []

        brut_taches = phase.get('taches') or []
        if not isinstance(brut_taches, (list, tuple)):
            brut_taches = []
        for tache in brut_taches:
            if not isinstance(tache, dict):
                continue
            duree_brute = tache.get('duree', 0)
            try:
                duree = int(duree_brute)
            except (TypeError, ValueError):
                duree = 0
            code = _texte_wbs(tache.get('code'), 40)
            description = _texte_wbs(tache.get('description'), WBS_TEXTE_MAX)
            est_jalon = bool(tache.get('jalon')) or duree == 0
            lignes.append({
                'index': index,
                'code': code,
                'description': description,
                'duree': duree,
                'predecesseurs': ', '.join(
                    _liste_public(tache.get('predecesseurs'))
                ),
                'jalon': est_jalon,
            })
            if est_jalon:
                jalons.append({'code': code, 'description': description})
            index += 1

        phases.append({
            'index': phase_index,
            'nom': nom_phase,
            'livrables': livrables_phase,
            'lignes': lignes,
        })

    source = _texte_wbs(donnees.get('source'), 20)
    return {
        'objectif': _texte_wbs(donnees.get('objectif'), WBS_OBJECTIF_MAX),
        'contraintes': _texte_wbs(donnees.get('contraintes'), WBS_CONTRAINTES_MAX),
        'hypotheses': _texte_wbs(donnees.get('hypotheses'), WBS_TEXTE_MAX),
        'limites': _texte_wbs(donnees.get('limites'), WBS_TEXTE_MAX),
        'source': source or 'regles',
        'avertissements': [
            _texte_wbs(element, WBS_TEXTE_MAX)
            for element in _liste_public(donnees.get('avertissements'))
        ],
        'phases': phases,
        'livrables': livrables,
        'jalons': jalons,
        'nb_taches': index,
    }


def _creer_projet_depuis_wbs(wbs, taches):
    """Crée le projet, ses tâches et ses dépendances (validation déjà faite).

    La fonction ajoute les objets à la session mais ne commit pas : l'appelant
    ajoute l'audit dans la même transaction avant de valider.
    """
    objectif = _texte_wbs(wbs.get('objectif'), WBS_OBJECTIF_MAX)
    contraintes = _texte_wbs(wbs.get('contraintes'), WBS_CONTRAINTES_MAX)
    hypotheses = _texte_wbs(wbs.get('hypotheses'), WBS_TEXTE_MAX)
    projet = Project(
        user_id=current_user.id,
        name=objectif[:WBS_NOM_PROJET_MAX] or 'Projet IA',
        description=contraintes or hypotheses or None,
        status='actif',
    )
    db.session.add(projet)
    db.session.flush()

    par_code = {}
    for code in taches:
        infos = taches[code]
        tache = Task(
            project_id=projet.id,
            code=code,
            description=_texte_wbs(infos.get('description'), WBS_TEXTE_MAX),
            duree=int(infos.get('duree', 0)),
        )
        db.session.add(tache)
        par_code[code] = tache
    db.session.flush()

    nb_dependances = 0
    for code, infos in taches.items():
        for predecesseur in (infos.get('depend_de') or []):
            cible = par_code.get(predecesseur)
            if cible is None:
                raise ValueError(
                    f"Le prédécesseur « {predecesseur} » n'a pas pu être résolu."
                )
            db.session.add(Dependency(
                successor_id=par_code[code].id,
                predecessor_id=cible.id,
                type_relation='FS',
            ))
            nb_dependances += 1

    return projet, nb_dependances


@ia_bp.app_context_processor
def lien_assistant_navigation():
    """Fournit un lien sûr vers l'assistant du projet le plus récent.

    Sans projet (ou sur la page publique), le lien pointe vers la liste des
    projets : la page d'accueil reste fonctionnelle pour tous les visiteurs.
    """
    projet = None
    if current_user.is_authenticated:
        try:
            for candidat in Project.query.order_by(
                Project.created_at.desc(), Project.id.desc()
            ):
                if user_can_access(candidat, current_user):
                    projet = candidat
                    break
        except Exception:
            db.session.rollback()
            projet = None
    try:
        url = (
            url_for('ia.assistant', project_id=projet.id)
            if projet is not None else url_for('projects.list_projects')
        )
    except BuildError:
        url = '/projects/'
    return {'assistant_projet_recent': projet, 'assistant_url': url}


@ia_bp.app_context_processor
def lien_optimisation_assistee():
    """Lien sûr vers l'optimisation assistée (blueprint livré en parallèle).

    Tant que ``routes_optimisation_ia`` n'est pas enregistré, le lien retombe
    sur la simulation classique : la page de détail reste rendable et le lien
    IA est déjà présent. Dès que le blueprint existe, ``url_for()`` fournit
    l'URL canonique ``/projects/<id>/optimisation/assistee``.
    """
    def url_optimisation_assistee(project_id):
        try:
            return url_for('optimisation_ia.assister', project_id=project_id)
        except BuildError:
            try:
                return url_for('projects.optimisation', project_id=project_id)
            except BuildError:
                return '#'

    return {'url_optimisation_assistee': url_optimisation_assistee}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@ia_bp.route('/statut')
def statut():
    configuration_ia = _configuration()
    return jsonify({
        'disponible': _est_disponible(),
        'modele': configuration_ia.get('modele'),
        'limite_appels_heure': configuration_ia.get('max_appels_par_heure'),
    })


@ia_bp.route('/projets/nouveau', methods=['GET', 'POST'])
@login_required
def nouveau_projet_wbs():
    """Proposition WBS modifiable, enregistrée seulement après confirmation.

    ``etape=generer`` ne fait aucun écrit en base : elle produit un aperçu
    éditable. ``etape=enregistrer`` relit tous les champs soumis, revalide le
    graphe côté serveur et exige ``confirmer=oui`` avant toute création.
    """
    valeurs = {
        'objectif': _texte_wbs(request.form.get('objectif'), WBS_OBJECTIF_MAX),
        'contraintes': _texte_wbs(
            request.form.get('contraintes'), WBS_CONTRAINTES_MAX
        ),
    }
    apercu = None
    erreurs = []

    if request.method == 'POST':
        etape = (request.form.get('etape') or '').strip()

        if etape == 'generer':
            if not valeurs['objectif']:
                flash("L'objectif doit être renseigné pour générer une WBS.", 'error')
            elif generer_wbs is None or valider_proposition is None:
                flash("Le service de génération WBS est indisponible.", 'error')
            else:
                limite = _configuration()['max_appels_par_heure']
                identifiant = current_user.get_id() or str(current_user.id)
                if not _consommer_appel(identifiant, limite):
                    pluriel = '' if limite == 1 else 's'
                    flash(
                        f"Limite d'usage atteinte : {limite} génération{pluriel} "
                        "par heure. Veuillez réessayer plus tard.",
                        'error',
                    )
                else:
                    try:
                        wbs = generer_wbs(
                            valeurs['objectif'], valeurs['contraintes']
                        )
                    except ValueError as exc:
                        flash(str(exc), 'error')
                    except Exception:
                        logger.exception("Le service de génération WBS a échoué.")
                        flash("La génération a échoué. Veuillez réessayer.", 'error')
                    else:
                        _, erreurs = valider_proposition(wbs)
                        apercu = _apercu_wbs(wbs)
                        valeurs = {
                            'objectif': apercu['objectif'],
                            'contraintes': apercu['contraintes'],
                        }

        elif etape == 'enregistrer':
            wbs = _reconstruire_wbs(
                request.form, valeurs['objectif'], valeurs['contraintes']
            )
            apercu = _apercu_wbs(wbs)

            if not valeurs['objectif']:
                erreurs.append("L'objectif est obligatoire pour créer un projet.")

            if request.form.get('confirmer') != 'oui':
                # Aucune écriture, aucune validation destructive : l'aperçu est
                # simplement restitué avec son formulaire éditable.
                flash(
                    "Confirmation explicite requise : aucun projet n'a été "
                    "enregistré.",
                    'error',
                )
            elif valider_proposition is None:
                flash("Le service de validation WBS est indisponible.", 'error')
            else:
                taches, erreurs_validation = valider_proposition(wbs)
                erreurs.extend(erreurs_validation)
                if not erreurs:
                    try:
                        projet, nb_dependances = _creer_projet_depuis_wbs(
                            wbs, taches
                        )
                        source = _texte_wbs(wbs.get('source'), 20) or 'regles'
                        db.session.add(AuditLog(
                            user_id=current_user.id,
                            action='IA_WBS_CREATION',
                            details=str({
                                'project_id': projet.id,
                                'nb_taches': len(taches),
                                'source': source,
                            }),
                        ))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                        logger.exception(
                            "Impossible d'enregistrer le projet généré par l'IA."
                        )
                        flash(
                            "Le projet n'a pas pu être enregistré. Vérifiez les "
                            "données et réessayez.",
                            'error',
                        )
                    else:
                        flash(
                            f"Projet créé avec succès : {len(taches)} tâche(s), "
                            f"{nb_dependances} dépendance(s).",
                            'success',
                        )
                        return redirect(
                            url_for('projects.detail', project_id=projet.id)
                        )
                else:
                    for erreur in erreurs:
                        flash(erreur, 'error')

        else:
            flash("Étape inconnue : utilisez le formulaire proposé.", 'error')

    return render_template(
        'projects/wbs_preview.html',
        valeurs=valeurs,
        apercu=apercu,
        erreurs=erreurs,
        limite_appels=_configuration()['max_appels_par_heure'],
    )


@ia_bp.route('/projets/<int:project_id>/assistant', methods=['GET'])
@login_required
def assistant(project_id):
    projet = _projet_autorise(project_id)
    try:
        donnees = _donnees_calculees(projet)
    except ValidationError as exc:
        for ligne in str(exc).split('\n'):
            if ligne.strip():
                flash(ligne.strip(), 'error')
        return redirect(url_for('projects.detail', project_id=project_id))

    return render_template(
        'projects/assistant.html',
        projet=projet,
        historique=_historique(project_id),
        disponible=_est_disponible(),
        configuration=_configuration(),
        taches=donnees['taches'],
        resultats=donnees['resultats'],
        duree_totale=donnees['duree_totale'],
        chemins_critiques=donnees['chemins_critiques'],
        question_url=url_for('ia.question', project_id=project_id),
    )


@ia_bp.route('/projets/<int:project_id>/assistant', methods=['POST'])
@login_required
def question(project_id):
    projet = _projet_autorise(project_id)
    payload = request.get_json(silent=True)
    est_json = isinstance(payload, dict)
    if est_json:
        question_brute = payload.get('question')
        # ``historique`` fourni par le client est volontairement ignoré :
        # seul l'historique de session fait foi (IA-ASS-04).
    else:
        question_brute = request.form.get('question')

    question_text = (
        question_brute.strip() if isinstance(question_brute, str) else ''
    )
    if not question_text:
        if est_json:
            return jsonify({'erreur': 'La question ne peut pas être vide.'}), 400
        flash('La question ne peut pas être vide.', 'error')
        return redirect(url_for('ia.assistant', project_id=project_id))

    configuration_ia = _configuration()
    limite = configuration_ia['max_appels_par_heure']
    identifiant = current_user.get_id() or str(current_user.id)
    if not _consommer_appel(identifiant, limite):
        pluriel = '' if limite == 1 else 's'
        return jsonify({
            'erreur': (
                f"Limite d'usage atteinte : {limite} question{pluriel} par heure. "
                "Veuillez réessayer plus tard."
            ),
            'code': 'LIMITE_ATTEINTE',
        }), 429

    try:
        donnees = _donnees_calculees(projet)
    except ValidationError as exc:
        details = [ligne.strip() for ligne in str(exc).split('\n') if ligne.strip()]
        return jsonify({
            'erreur': "Le planning de ce projet n'est pas calculable.",
            'details': details,
        }), 400

    historique = _historique(project_id)
    fonction = repondre or _repondre_fallback
    try:
        brut = fonction(
            question_text,
            donnees['taches'],
            donnees['resultats'],
            donnees['duree_totale'],
            donnees['chemins_critiques'],
            historique=historique,
            successeurs=donnees['successeurs'],
        )
    except Exception:
        # Le contrat interdit à services.ia de lever pour une panne IA, mais
        # une erreur de programmation ne doit jamais bloquer l'assistant.
        logger.exception("Le service IA a échoué : bascule sur le mode sans IA.")
        brut = _repondre_fallback(
            question_text,
            donnees['taches'],
            donnees['resultats'],
            donnees['duree_totale'],
            donnees['chemins_critiques'],
            historique=historique,
            successeurs=donnees['successeurs'],
        )
    reponse = _reponse_publique(brut)

    if not donnees['taches']:
        reponse['avertissements'].append(
            "Ce projet ne contient aucune tâche : les résultats calculés sont vides."
        )

    # Audit sans question ni texte de réponse : aucun contenu sensible n'est
    # persisté en base (§19.8).
    try:
        db.session.add(AuditLog(
            user_id=current_user.id,
            action='IA_ASSISTANT',
            details=str({
                'project_id': projet.id,
                'source': reponse['source'],
                'appel_ia': reponse['appel_ia'],
                'tokens': reponse['tokens'],
            }),
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Impossible d'écrire l'audit IA.")

    _ajouter_historique(project_id, 'user', question_text)
    _ajouter_historique(project_id, 'assistant', reponse['texte'])

    return jsonify(reponse)
