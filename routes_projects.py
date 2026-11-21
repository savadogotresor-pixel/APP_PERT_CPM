"""Module de gestion des projets et des tâches PERT/CPM.

Ce blueprint est autonome : il consomme les modèles définis dans ``models.py``
et le journal d'audit, puis recalcule systématiquement le planning avec le
moteur canonique ``logic.pert.compute``. Aucune donnée chiffrée n'est acceptée
depuis le navigateur : seules les données sources (tâches, durées,
prédécesseurs) sont enregistrées.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from io import BytesIO

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import inspect as sa_inspect
from werkzeug.utils import secure_filename

from extensions import db
from logic.data import parser_taches
from logic.pert import ValidationError, compute
from models import (
    AuditLog,
    Dependency,
    Project,
    Task,
    project_to_taches,
    user_can_access,
)
from services.import_export import exporter_csv, exporter_xlsx, importer_fichier

try:
    from services.rapport import generer_rapport_pdf
except ImportError:  # pragma: no cover - dépend de l'avancement du chantier
    generer_rapport_pdf = None

# Le modèle ``Scenario`` et le service d'optimisation sont fournis par un autre
# module du chantier. Les imports tolérants permettent de charger ce blueprint
# même si ce travail parallèle n'est pas encore terminé ; les helpers ci-dessous
# retentent l'import au moment où la route est utilisée.
try:
    from models import Scenario
except ImportError:  # pragma: no cover - dépend de l'avancement du chantier
    Scenario = None

try:
    from services.optimisation import simuler
except ImportError:  # pragma: no cover - dépend de l'avancement du chantier
    simuler = None

try:  # Werkzeug >= 2.1
    from werkzeug.routing.exceptions import BuildError
except ImportError:  # Werkzeug < 2.1
    from werkzeug.routing import BuildError


projects_bp = Blueprint('projects', __name__, url_prefix='/projects')

STATUT_ACTIF = 'actif'
STATUT_ARCHIVE = 'archive'

MIME_CSV = 'text/csv; charset=utf-8'
MIME_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Noms possibles de la colonne « tâche dépendante » selon le modèle retenu.
_COLONNES_PROPRIETAIRES = (
    'task_id',
    'successor_id',
    'task_dependante_id',
    'destination_id',
    'cible_id',
)


# ---------------------------------------------------------------------------
# Utilitaires généraux
# ---------------------------------------------------------------------------

def _url_ou_defaut(*endpoints, defaut='#'):
    """Retourne la première URL d'un endpoint existant, sinon *defaut*."""
    for endpoint in endpoints:
        try:
            return url_for(endpoint)
        except BuildError:  # endpoint non enregistré ou paramètres manquants
            continue
    return defaut


@projects_bp.context_processor
def urls_navigation():
    """Fournit des liens de navigation robustes aux templates du module."""
    return {
        'accueil_url': _url_ou_defaut('accueil', 'main.accueil', defaut='/'),
        'deconnexion_url': _url_ou_defaut(
            'auth.logout', 'logout', 'users.logout', defaut='/logout'
        ),
    }


def _projet_ou_404(project_id):
    """Charge un projet et applique le contrôle serveur de propriété (L111)."""
    projet = Project.query.filter_by(id=project_id).first()
    if projet is None:
        abort(404)
    if not user_can_access(projet, current_user):
        abort(403)
    return projet


def _tache_ou_404(projet, task_id):
    tache = Task.query.filter_by(id=task_id, project_id=projet.id).first()
    if tache is None:
        abort(404)
    return tache


def _journal(action, details):
    """Ajoute une entrée d'audit à la session courante (commit par l'appelant)."""
    db.session.add(
        AuditLog(user_id=current_user.id, action=action, details=str(details))
    )


def _messages_erreur(exception):
    """Découpe les messages agrégés par ``ValidationError`` en lignes propres."""
    return [ligne.strip() for ligne in str(exception).split('\n') if ligne.strip()]


def _simulateur():
    """Retourne ``simuler`` (import différé si le service arrive plus tard)."""
    global simuler
    if simuler is None:
        from services.optimisation import simuler as fonction_simuler

        simuler = fonction_simuler
    return simuler


def _generateur_rapport():
    """Retourne ``generer_rapport_pdf`` (import différé si le service arrive)."""
    global generer_rapport_pdf
    if generer_rapport_pdf is None:
        from services.rapport import generer_rapport_pdf as fonction_rapport

        generer_rapport_pdf = fonction_rapport
    return generer_rapport_pdf


def _modele_scenario():
    """Retourne le modèle ``Scenario`` dès qu'il est disponible."""
    global Scenario
    if Scenario is None:
        try:
            from models import Scenario as modele_scenario
        except ImportError:
            return None
        Scenario = modele_scenario
    return Scenario


def _scenarios_enregistres(projet):
    """Scénarios d'un projet, du plus récent au plus ancien."""
    modele = _modele_scenario()
    if modele is None:
        return []
    try:
        return (
            modele.query.filter_by(project_id=projet.id)
            .order_by(modele.date_creation.desc())
            .all()
        )
    except Exception:  # table pas encore migrée : la page reste consultable
        db.session.rollback()
        return []


def _details_scenario(scenario):
    """Aplatit un scénario pour l'affichage (JSON déjà décodé côté serveur)."""
    def charger(valeur):
        if not valeur:
            return {}
        try:
            donnees = json.loads(valeur)
        except (TypeError, ValueError):
            return {}
        return donnees if isinstance(donnees, dict) else {}

    hypotheses = charger(getattr(scenario, 'hypotheses', None))
    resultats = charger(getattr(scenario, 'resultats', None))
    return {
        'nom': getattr(scenario, 'nom', ''),
        'date_creation': getattr(scenario, 'date_creation', None),
        'reductions': hypotheses.get('reductions') or {},
        'duree_initiale': resultats.get('duree_initiale'),
        'duree_simulee': resultats.get('duree_simulee'),
        'gains': resultats.get('gains'),
    }


def _nom_norme(projet):
    """Nom de fichier sûr, dérivé du nom du projet."""
    return secure_filename(projet.name or '') or 'projet'


def _nom_export(projet, extension):
    """Nom de téléchargement sûr, dérivé du nom du projet."""
    return f'{_nom_norme(projet)}_resultats.{extension}'


def _apercu_import(taches):
    """Prépare les lignes du tableau d'aperçu d'un fichier importé."""
    return [
        {
            'code': code,
            'description': infos.get('description', ''),
            'duree': infos.get('duree', 0),
            'predecesseurs': ', '.join(infos.get('depend_de') or []),
        }
        for code, infos in sorted(taches.items())
    ]


def _date_reference(projet):
    """Normalise la date de référence (date, datetime ou chaîne ISO)."""
    valeur = getattr(projet, 'date_reference', None)
    if valeur is None or valeur == '':
        return None
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    try:
        return date.fromisoformat(str(valeur)[:10])
    except ValueError:
        return None


def _planning(projet):
    """Calcule le planning ; retourne ``None`` si le graphe est invalide."""
    try:
        taches = project_to_taches(projet)
        if not taches:
            return None
        return compute(taches)
    except ValidationError:
        return None


def _duree_totale(projet):
    resultat = _planning(projet)
    return resultat.duree_totale if resultat is not None else None


# ---------------------------------------------------------------------------
# Utilitaires spécifiques aux dépendances
# ---------------------------------------------------------------------------

def _colonne_proprietaire_dependance():
    """Retourne ``(nom_attribut, colonne)`` de la tâche dépendante.

    Le contrat fournit ``Dependency.predecessor_id`` mais pas le nom de
    l'autre extrémité. On le découvre par clé étrangère, puis par nom usuel,
    afin de rester compatible avec plusieurs définitions possibles du modèle.
    """
    try:
        mapper = sa_inspect(Dependency)
    except Exception:
        return None

    colonnes = {}
    for attribut in mapper.column_attrs:
        colonne = attribut.columns[0]
        colonnes[attribut.key] = (attribut.key, colonne)

    for nom, (cle, colonne) in colonnes.items():
        if nom == 'predecessor_id':
            continue
        for cle_etrangere in colonne.foreign_keys:
            cible = cle_etrangere.column
            if cible.name == 'id' and cible.table.name.lower() in ('task', 'tasks'):
                return cle, colonne

    for nom in _COLONNES_PROPRIETAIRES:
        if nom in colonnes:
            return colonnes[nom]
    return None


def _predecesseur_est_un_code():
    """Vrai si ``predecessor_id`` stocke un code plutôt qu'un identifiant."""
    try:
        colonne = sa_inspect(Dependency).column_attrs['predecessor_id'].columns[0]
    except Exception:
        return False
    for cle_etrangere in colonne.foreign_keys:
        if cle_etrangere.column.name == 'id':
            return False
    try:
        return colonne.type.python_type is str
    except Exception:
        return False


def _supprimer_dependances_possedees(taches):
    """Supprime les dépendances dont *taches* sont la partie dépendante."""
    taches = list(taches)
    identifiants = [t.id for t in taches if getattr(t, 'id', None) is not None]
    infos = _colonne_proprietaire_dependance()

    if infos and identifiants:
        _, colonne = infos
        Dependency.query.filter(colonne.in_(identifiants)).delete(
            synchronize_session='fetch'
        )
    else:
        for tache in taches:
            for dependance in list(getattr(tache, 'dependances', ()) or ()):
                db.session.delete(dependance)


def _supprimer_dependances_pour_taches(taches):
    """Supprime toutes les dépendances touchant les tâches données."""
    taches = list(taches)
    _supprimer_dependances_possedees(taches)

    identifiants = [t.id for t in taches if getattr(t, 'id', None) is not None]
    codes = [t.code for t in taches if getattr(t, 'code', None)]

    if identifiants:
        Dependency.query.filter(
            Dependency.predecessor_id.in_(identifiants)
        ).delete(synchronize_session='fetch')
    if codes and _predecesseur_est_un_code():
        Dependency.query.filter(
            Dependency.predecessor_id.in_(codes)
        ).delete(synchronize_session='fetch')


def _creer_dependances(tache, codes_predecesseurs, projet):
    """Crée les lignes Dependency pour la tâche fraîchement enregistrée."""
    if not codes_predecesseurs:
        return
    infos = _colonne_proprietaire_dependance()
    cle_proprietaire = infos[0] if infos else 'task_id'
    par_code = {t.code: t for t in projet.tasks}
    par_code[tache.code] = tache
    par_codes = _predecesseur_est_un_code()

    for code in codes_predecesseurs:
        predecesseur = par_code[code]
        valeur = predecesseur.code if par_codes else predecesseur.id
        db.session.add(
            Dependency(
                **{
                    cle_proprietaire: tache.id,
                    'predecessor_id': valeur,
                }
            )
        )


# ---------------------------------------------------------------------------
# Formulaires
# ---------------------------------------------------------------------------

def _donnees_projet():
    nom = (request.form.get('name') or '').strip()
    description = (request.form.get('description') or '').strip()
    date_brute = (request.form.get('date_reference') or '').strip()
    erreurs = []

    if not nom:
        erreurs.append("Le nom du projet est obligatoire.")
    date_reference = None
    if date_brute:
        try:
            date_reference = date.fromisoformat(date_brute)
        except ValueError:
            erreurs.append(
                "La date de référence doit être au format AAAA-MM-JJ (ex. 2026-11-21)."
            )

    return (
        {
            'name': nom,
            'description': description,
            'date_reference': date_reference,
        },
        erreurs,
    )


def _donnees_tache(projet, tache=None):
    code = (request.form.get('code') or '').strip()
    description = (request.form.get('description') or '').strip()
    duree_brute = (request.form.get('duree') or '').strip()
    predecesseurs_bruts = request.form.get('predecesseurs') or ''
    erreurs = []

    predecesseurs = []
    for morceau in predecesseurs_bruts.split(','):
        pred = morceau.strip()
        if pred and pred not in predecesseurs:
            predecesseurs.append(pred)

    if not code:
        erreurs.append("Le code de la tâche est obligatoire.")
    elif any(
        autre.code == code and (tache is None or autre.id != tache.id)
        for autre in projet.tasks
    ):
        erreurs.append(f"Le code « {code} » existe déjà dans ce projet.")

    if not duree_brute.isdigit():
        erreurs.append("La durée doit être un entier supérieur ou égal à 0.")
    duree = int(duree_brute) if duree_brute.isdigit() else 0

    codes_connus = {
        autre.code for autre in projet.tasks
        if tache is None or autre.id != tache.id
    }
    for pred in predecesseurs:
        if pred == code:
            erreurs.append("Une tâche ne peut pas dépendre d'elle-même.")
        elif pred not in codes_connus:
            erreurs.append(f"Le prédécesseur « {pred} » n'existe pas dans ce projet.")

    if not erreurs:
        try:
            graphe = deepcopy(project_to_taches(projet))
            if tache is not None:
                graphe.pop(tache.code, None)
            graphe[code] = {
                'description': description,
                'duree': duree,
                'depend_de': predecesseurs,
            }
            compute(graphe)
        except ValidationError as exc:
            for ligne in str(exc).split('\n'):
                if ligne.strip():
                    erreurs.append(ligne.strip())

    return (
        {
            'code': code,
            'description': description,
            'duree': duree_brute,
            'predecesseurs': ', '.join(predecesseurs),
        },
        predecesseurs,
        duree,
        erreurs,
    )


# ---------------------------------------------------------------------------
# Routes — projets
# ---------------------------------------------------------------------------

@projects_bp.route('/')
@login_required
def list_projects():
    projets = [p for p in Project.query.all() if user_can_access(p, current_user)]
    projets.sort(key=lambda p: ((p.name or '').lower(), p.id or 0))
    lignes = [
        {
            'projet': projet,
            'nb_taches': len(projet.tasks),
            'duree_totale': _duree_totale(projet),
        }
        for projet in projets
    ]
    return render_template('projects/list.html', lignes=lignes)


@projects_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_project():
    if request.method == 'POST':
        valeurs, erreurs = _donnees_projet()
        if not erreurs:
            projet = Project(
                user_id=current_user.id,
                name=valeurs['name'],
                description=valeurs['description'],
                date_reference=valeurs['date_reference'],
                status=STATUT_ACTIF,
            )
            db.session.add(projet)
            _journal('PROJET_CREE', f"Projet « {valeurs['name']} » créé.")
            db.session.commit()
            flash('Projet créé avec succès.', 'success')
            return redirect(url_for('projects.detail', project_id=projet.id))
        for erreur in erreurs:
            flash(erreur, 'error')
    else:
        valeurs = {'name': '', 'description': '', 'date_reference': ''}

    return render_template(
        'projects/form.html',
        projet=None,
        valeurs=valeurs,
        titre='Nouveau projet',
        action=url_for('projects.new_project'),
    )


@projects_bp.route('/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_project(project_id):
    projet = _projet_ou_404(project_id)

    if request.method == 'POST':
        valeurs, erreurs = _donnees_projet()
        if not erreurs:
            projet.name = valeurs['name']
            projet.description = valeurs['description']
            projet.date_reference = valeurs['date_reference']
            _journal('PROJET_MODIFIE', f"Projet « {projet.name} » modifié.")
            db.session.commit()
            flash('Projet mis à jour.', 'success')
            return redirect(url_for('projects.detail', project_id=projet.id))
        for erreur in erreurs:
            flash(erreur, 'error')
    else:
        reference = _date_reference(projet)
        valeurs = {
            'name': projet.name or '',
            'description': projet.description or '',
            'date_reference': reference.isoformat() if reference else '',
        }

    return render_template(
        'projects/form.html',
        projet=projet,
        valeurs=valeurs,
        titre='Modifier le projet',
        action=url_for('projects.edit_project', project_id=projet.id),
    )


@projects_bp.route('/<int:project_id>/delete', methods=['POST'])
@login_required
def delete_project(project_id):
    projet = _projet_ou_404(project_id)

    if request.form.get('confirm') != 'oui':
        flash(
            "Suppression annulée : saisissez « oui » pour confirmer.",
            'error',
        )
        return redirect(url_for('projects.detail', project_id=projet.id))

    nom = projet.name
    taches = list(projet.tasks)
    _supprimer_dependances_pour_taches(taches)

    # La relation ``tasks`` porte généralement un cascade delete-orphan : dans
    # ce cas on supprime seulement le projet. Sinon on efface les tâches d'abord.
    relation = sa_inspect(projet).mapper.relationships.get('tasks')
    cascade_delete = relation is not None and relation.cascade.delete
    if not cascade_delete:
        for tache in taches:
            db.session.delete(tache)
    db.session.delete(projet)
    _journal('PROJET_SUPPRIME', f"Projet « {nom} » supprimé.")
    db.session.commit()
    flash('Projet supprimé.', 'success')
    return redirect(url_for('projects.list_projects'))


@projects_bp.route('/<int:project_id>/archive', methods=['POST'])
@login_required
def archive_project(project_id):
    projet = _projet_ou_404(project_id)
    projet.status = STATUT_ARCHIVE
    _journal('PROJET_ARCHIVE', f"Projet « {projet.name} » archivé.")
    db.session.commit()
    flash('Projet archivé.', 'success')
    return redirect(url_for('projects.list_projects'))


@projects_bp.route('/<int:project_id>/unarchive', methods=['POST'])
@login_required
def unarchive_project(project_id):
    projet = _projet_ou_404(project_id)
    projet.status = STATUT_ACTIF
    _journal('PROJET_DESARCHIVE', f"Projet « {projet.name} » désarchivé.")
    db.session.commit()
    flash('Projet désarchivé.', 'success')
    return redirect(url_for('projects.list_projects'))


@projects_bp.route('/<int:project_id>')
@login_required
def detail(project_id):
    projet = _projet_ou_404(project_id)
    taches = project_to_taches(projet)

    try:
        resultat = compute(taches) if taches else None
    except ValidationError as exc:
        resultat = None
        erreur_calcul = ' ; '.join(
            ligne.strip() for ligne in str(exc).split('\n') if ligne.strip()
        )
    else:
        erreur_calcul = None

    resultats = resultat.resultats if resultat is not None else {}
    duree_totale = resultat.duree_totale if resultat is not None else None
    chemins = resultat.chemins_critiques if resultat is not None else []
    nb_critiques = sum(1 for r in resultats.values() if r.get('critique'))

    reference = _date_reference(projet)
    fin_estimee = None
    if reference is not None and duree_totale is not None:
        fin_estimee = reference + timedelta(days=duree_totale)

    taches_triees = sorted(
        projet.tasks,
        key=lambda t: (resultats.get(t.code, {}).get('ES', 0), t.code or ''),
    )

    return render_template(
        'projects/detail.html',
        projet=projet,
        taches=taches,
        taches_triees=taches_triees,
        resultats=resultats,
        duree_totale=duree_totale,
        chemins_critiques=chemins,
        erreur_calcul=erreur_calcul,
        nb_critiques=nb_critiques,
        date_fin_estimee=fin_estimee,
    )


# ---------------------------------------------------------------------------
# Routes — optimisation (simulation contemplative)
# ---------------------------------------------------------------------------

@projects_bp.route('/<int:project_id>/optimisation', methods=['GET', 'POST'])
@login_required
def optimisation(project_id):
    """Simule des réductions de durée sans jamais modifier le planning source."""
    projet = _projet_ou_404(project_id)
    taches = project_to_taches(projet)

    try:
        planning = compute(taches) if taches else None
    except ValidationError as exc:
        for erreur in _messages_erreur(exc):
            flash(erreur, 'error')
        planning = None

    resultats_initiaux = planning.resultats if planning is not None else {}
    taches_critiques = [
        code for code in sorted(taches)
        if resultats_initiaux.get(code, {}).get('critique')
        and taches[code].get('duree', 0) > 0
    ]
    durees_formulaire = {
        code: str(taches[code].get('duree', 0)) for code in taches_critiques
    }
    nom_scenario = ''
    resultat_simulation = None

    if request.method == 'POST':
        nom_scenario = (request.form.get('nom_scenario') or '').strip()
        erreurs_saisie = []
        reductions = {}

        for code in taches_critiques:
            valeur_brute = (request.form.get(f'duree_{code}') or '').strip()
            durees_formulaire[code] = valeur_brute
            if not valeur_brute:
                continue
            try:
                nouvelle_duree = int(valeur_brute)
            except ValueError:
                erreurs_saisie.append(
                    f"La durée simulée de la tâche « {code} » doit être un entier."
                )
                continue
            if nouvelle_duree < 0:
                erreurs_saisie.append(
                    f"La durée simulée de la tâche « {code} » ne peut pas être négative."
                )
                continue
            if nouvelle_duree != taches[code].get('duree', 0):
                reductions[code] = nouvelle_duree

        for erreur in erreurs_saisie:
            flash(erreur, 'error')

        if not erreurs_saisie:
            if not reductions:
                flash(
                    "Aucune durée n'a été modifiée : saisissez une durée simulée "
                    "différente de la durée actuelle.",
                    'error',
                )
            else:
                try:
                    resultat_simulation = _simulateur()(deepcopy(taches), reductions)
                except ImportError:
                    flash("Le service d'optimisation n'est pas disponible.", 'error')
                except ValueError as exc:
                    for erreur in _messages_erreur(exc):
                        flash(erreur, 'error')
                else:
                    flash('Simulation terminée. Le planning de référence est inchangé.', 'success')

                    if nom_scenario:
                        modele = _modele_scenario()
                        if modele is None:
                            flash(
                                "Le scénario n'a pas pu être enregistré : modèle indisponible.",
                                'error',
                            )
                        else:
                            initial = resultat_simulation.get('initial') or {}
                            simule = resultat_simulation.get('simule') or {}
                            scenario = modele(
                                project_id=projet.id,
                                nom=nom_scenario,
                                hypotheses=json.dumps(
                                    {'reductions': reductions}, ensure_ascii=False
                                ),
                                resultats=json.dumps(
                                    {
                                        'duree_initiale': initial.get('duree_totale'),
                                        'duree_simulee': simule.get('duree_totale'),
                                        'gains': resultat_simulation.get('gains'),
                                    },
                                    ensure_ascii=False,
                                ),
                            )
                            db.session.add(scenario)
                            _journal(
                                'SCENARIO_ENREGISTRE',
                                f"Scénario « {nom_scenario} » enregistré pour le "
                                f"projet « {projet.name} ».",
                            )
                            try:
                                db.session.commit()
                            except Exception:
                                db.session.rollback()
                                flash(
                                    "Le scénario n'a pas pu être enregistré.",
                                    'error',
                                )
                            else:
                                flash('Scénario enregistré.', 'success')

    scenarios = _scenarios_enregistres(projet)
    return render_template(
        'projects/optimisation.html',
        projet=projet,
        taches=taches,
        taches_critiques=taches_critiques,
        durees_formulaire=durees_formulaire,
        chemins_critiques=planning.chemins_critiques if planning is not None else [],
        duree_totale=planning.duree_totale if planning is not None else None,
        resultat=resultat_simulation,
        nom_scenario=nom_scenario,
        scenarios=scenarios,
        scenarios_affiches=[_details_scenario(scenario) for scenario in scenarios],
    )


# ---------------------------------------------------------------------------
# Routes — import / export
# ---------------------------------------------------------------------------

@projects_bp.route('/<int:project_id>/import', methods=['GET', 'POST'])
@login_required
def import_fichier(project_id):
    projet = _projet_ou_404(project_id)
    apercu = None
    erreurs = []
    nom_fichier = ''

    if request.method == 'POST':
        fichier = request.files.get('fichier')
        confirmer = bool(request.form.get('confirmer'))
        nom_fichier = (fichier.filename if fichier is not None else '') or ''
        taches_importees = {}

        if fichier is None or not nom_fichier:
            erreurs.append("Sélectionnez un fichier .csv ou .xlsx à importer.")
        else:
            texte, erreurs_fichier = importer_fichier(fichier.read(), nom_fichier)
            erreurs.extend(erreurs_fichier)

            taches_importees, erreurs_parser = parser_taches(texte or '')
            erreurs.extend(erreurs_parser)
            if not taches_importees and not erreurs:
                erreurs.append("Aucune tâche n'a été trouvée dans ce fichier.")

            taches_existantes = project_to_taches(projet)
            graphe_fusionne = deepcopy(taches_existantes)
            collisions = sorted(set(taches_existantes) & set(taches_importees))
            for code in collisions:
                erreurs.append(
                    f"Le code « {code} » existe déjà dans ce projet : "
                    "l'import est annulé pour éviter de l'écraser."
                )
            if not collisions:
                graphe_fusionne.update(deepcopy(taches_importees))
                if not erreurs:
                    try:
                        compute(graphe_fusionne)
                    except ValidationError as exc:
                        erreurs.extend(_messages_erreur(exc))

        apercu = _apercu_import(taches_importees)

        if confirmer and not erreurs:
            nouvelles = []
            for code, infos in taches_importees.items():
                tache = Task(
                    project_id=projet.id,
                    code=code,
                    description=infos.get('description', ''),
                    duree=int(infos.get('duree', 0)),
                )
                db.session.add(tache)
                nouvelles.append(tache)
            db.session.flush()

            par_code = {tache.code: tache for tache in projet.tasks}
            par_code.update({tache.code: tache for tache in nouvelles})
            infos_dependance = _colonne_proprietaire_dependance()
            cle_proprietaire = (
                infos_dependance[0] if infos_dependance else 'task_id'
            )
            codes_en_stockage = _predecesseur_est_un_code()

            for code, infos in taches_importees.items():
                for predecesseur in dict.fromkeys(infos.get('depend_de') or []):
                    cible = par_code.get(predecesseur)
                    if cible is None:
                        erreurs.append(
                            f"Le prédécesseur « {predecesseur} » n'a pas pu être résolu."
                        )
                        continue
                    valeur = (
                        cible.code if codes_en_stockage else cible.id
                    )
                    db.session.add(
                        Dependency(
                            **{
                                cle_proprietaire: par_code[code].id,
                                'predecessor_id': valeur,
                            }
                        )
                    )

            if erreurs:
                db.session.rollback()
            else:
                _journal(
                    'IMPORT_FICHIER',
                    f"{len(nouvelles)} tâche(s) importée(s) dans le projet "
                    f"« {projet.name} » depuis « {nom_fichier} ».",
                )
                db.session.commit()
                flash(
                    f"{len(nouvelles)} tâche(s) importée(s) avec succès.",
                    'success',
                )
                return redirect(url_for('projects.detail', project_id=projet.id))

    return render_template(
        'projects/import.html',
        projet=projet,
        apercu=apercu,
        erreurs=erreurs,
        nom_fichier=nom_fichier,
    )


@projects_bp.route('/<int:project_id>/export.csv')
@login_required
def export_csv(project_id):
    projet = _projet_ou_404(project_id)
    taches = project_to_taches(projet)

    try:
        resultat = compute(taches)
    except ValidationError as exc:
        for erreur in _messages_erreur(exc):
            flash(erreur, 'error')
        return redirect(url_for('projects.detail', project_id=projet.id))

    donnees = exporter_csv(taches, resultat.resultats)
    _journal(
        'EXPORT_CSV',
        f"Résultats du projet « {projet.name} » exportés au format CSV.",
    )
    db.session.commit()
    return send_file(
        BytesIO(donnees),
        mimetype=MIME_CSV,
        as_attachment=True,
        download_name=_nom_export(projet, 'csv'),
    )


@projects_bp.route('/<int:project_id>/export.xlsx')
@login_required
def export_xlsx(project_id):
    projet = _projet_ou_404(project_id)
    taches = project_to_taches(projet)

    try:
        resultat = compute(taches)
    except ValidationError as exc:
        for erreur in _messages_erreur(exc):
            flash(erreur, 'error')
        return redirect(url_for('projects.detail', project_id=projet.id))

    donnees = exporter_xlsx(
        taches,
        resultat.resultats,
        resultat.duree_totale,
        resultat.chemins_critiques,
    )
    _journal(
        'EXPORT_XLSX',
        f"Résultats du projet « {projet.name} » exportés au format Excel.",
    )
    db.session.commit()
    return send_file(
        BytesIO(donnees),
        mimetype=MIME_XLSX,
        as_attachment=True,
        download_name=_nom_export(projet, 'xlsx'),
    )


@projects_bp.route('/<int:project_id>/rapport.pdf')
@login_required
def rapport_pdf(project_id):
    """Génère côté serveur le rapport PDF du planning PERT/CPM."""
    projet = _projet_ou_404(project_id)
    taches = project_to_taches(projet)

    try:
        resultat = compute(taches)
    except ValidationError as exc:
        for erreur in _messages_erreur(exc):
            flash(erreur, 'error')
        return redirect(url_for('projects.detail', project_id=projet.id))

    reference = _date_reference(projet)
    date_debut = reference.isoformat() if reference is not None else None

    try:
        generer_rapport_pdf = _generateur_rapport()
        pdf = generer_rapport_pdf(
            projet.name,
            taches,
            resultat.resultats,
            resultat.duree_totale,
            resultat.chemins_critiques,
            date_debut,
        )
    except ImportError:
        flash("Le service de rapport PDF n'est pas disponible.", 'error')
        return redirect(url_for('projects.detail', project_id=projet.id))

    _journal(
        'RAPPORT_PDF',
        f"Rapport PDF du projet « {projet.name} » généré.",
    )
    db.session.commit()
    return send_file(
        BytesIO(pdf),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'{_nom_norme(projet)}_rapport_pert_cpm.pdf',
    )


# ---------------------------------------------------------------------------
# Routes — tâches
# ---------------------------------------------------------------------------

@projects_bp.route('/<int:project_id>/tasks/new', methods=['GET', 'POST'])
@login_required
def new_task(project_id):
    projet = _projet_ou_404(project_id)

    if request.method == 'POST':
        valeurs, predecesseurs, duree, erreurs = _donnees_tache(projet)
        if not erreurs:
            tache = Task(
                project_id=projet.id,
                code=valeurs['code'],
                description=valeurs['description'],
                duree=duree,
            )
            db.session.add(tache)
            db.session.flush()
            _creer_dependances(tache, predecesseurs, projet)
            _journal(
                'TACHE_CREE',
                f"Tâche « {tache.code} » créée dans le projet « {projet.name} ».",
            )
            db.session.commit()
            flash('Tâche créée avec succès.', 'success')
            return redirect(url_for('projects.detail', project_id=projet.id))
        for erreur in erreurs:
            flash(erreur, 'error')
    else:
        valeurs = {'code': '', 'description': '', 'duree': '', 'predecesseurs': ''}

    codes_existants = sorted(t.code for t in projet.tasks if t.code)
    return render_template(
        'projects/task_form.html',
        projet=projet,
        tache=None,
        valeurs=valeurs,
        codes_existants=codes_existants,
        titre='Nouvelle tâche',
        action=url_for('projects.new_task', project_id=projet.id),
    )


@projects_bp.route(
    '/<int:project_id>/tasks/<int:task_id>/edit', methods=['GET', 'POST']
)
@login_required
def edit_task(project_id, task_id):
    projet = _projet_ou_404(project_id)
    tache = _tache_ou_404(projet, task_id)

    if request.method == 'POST':
        valeurs, predecesseurs, duree, erreurs = _donnees_tache(projet, tache)
        if not erreurs:
            tache.code = valeurs['code']
            tache.description = valeurs['description']
            tache.duree = duree
            _supprimer_dependances_possedees([tache])
            _creer_dependances(tache, predecesseurs, projet)
            _journal(
                'TACHE_MODIFIEE',
                f"Tâche « {tache.code} » modifiée dans le projet « {projet.name} ».",
            )
            db.session.commit()
            flash('Tâche mise à jour.', 'success')
            return redirect(url_for('projects.detail', project_id=projet.id))
        for erreur in erreurs:
            flash(erreur, 'error')
    else:
        taches_actuelles = project_to_taches(projet)
        predecesseurs = taches_actuelles.get(tache.code, {}).get('depend_de', [])
        valeurs = {
            'code': tache.code or '',
            'description': tache.description or '',
            'duree': str(tache.duree),
            'predecesseurs': ', '.join(predecesseurs),
        }

    codes_existants = sorted(
        t.code for t in projet.tasks if t.code and t.id != tache.id
    )
    return render_template(
        'projects/task_form.html',
        projet=projet,
        tache=tache,
        valeurs=valeurs,
        codes_existants=codes_existants,
        titre='Modifier la tâche',
        action=url_for(
            'projects.edit_task', project_id=projet.id, task_id=tache.id
        ),
    )


@projects_bp.route(
    '/<int:project_id>/tasks/<int:task_id>/delete', methods=['POST']
)
@login_required
def delete_task(project_id, task_id):
    projet = _projet_ou_404(project_id)
    tache = _tache_ou_404(projet, task_id)

    if request.form.get('confirm') != 'oui':
        flash(
            "Suppression annulée : saisissez « oui » pour confirmer.",
            'error',
        )
        return redirect(url_for('projects.detail', project_id=projet.id))

    code = tache.code
    _supprimer_dependances_pour_taches([tache])
    db.session.delete(tache)
    _journal(
        'TACHE_SUPPRIMEE',
        f"Tâche « {code} » supprimée du projet « {projet.name} ».",
    )
    db.session.commit()
    flash('Tâche supprimée.', 'success')
    return redirect(url_for('projects.detail', project_id=projet.id))
