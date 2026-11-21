"""Authentification des utilisateurs de l'application PERT/CPM.

Fournit l'inscription, la connexion, la déconnexion et la gestion du profil.
Les contrôles d'unicité et de mots de passe sont effectués côté serveur.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash

import crypto_rsa

try:
    from werkzeug.routing.exceptions import BuildError
except ImportError:  # Werkzeug < 2.3
    from werkzeug.routing import BuildError

from sqlalchemy import func

from extensions import db
from models import User

auth_bp = Blueprint('auth', __name__)

LONGUEUR_MIN_MOT_DE_PASSE = 8


def _url_sure(endpoint, repli):
    """Retourne l'URL d'un endpoint, ou ``repli`` s'il n'est pas enregistré."""
    try:
        return url_for(endpoint)
    except BuildError:
        return repli


def _url_projets():
    """URL de la liste des projets (repli si le blueprint n'existe pas encore)."""
    return _url_sure('projects.list_projects', '/projets')


def _url_accueil():
    """URL de l'accueil, destination par défaut après connexion/inscription."""
    return _url_sure('accueil', '/')


def _rendre(nom_template, **contexte):
    """Rend un template en lui fournissant toujours l'URL des projets."""
    contexte.setdefault('projects_url', _url_projets())
    return render_template(nom_template, **contexte)


def _utilisateur_existe(champ, valeur, exclure_id=None):
    """Vérifie en base qu'un nom d'utilisateur ou un e-mail est déjà pris."""
    colonne = getattr(User, champ)
    requete = User.query.filter(func.lower(colonne) == valeur.strip().lower())
    if exclure_id is not None:
        requete = requete.filter(User.id != exclure_id)
    return requete.first() is not None


def _mot_de_passe_valide(user, mot_de_passe):
    """Compare un mot de passe au hachage stocké, sans fuite d'information."""
    if not mot_de_passe or not getattr(user, 'password_hash', None):
        return False
    try:
        return check_password_hash(user.password_hash, mot_de_passe)
    except (TypeError, ValueError):
        return False


def _premier_champ(*noms):
    """Retourne la première valeur non vide parmi plusieurs noms de champs."""
    for nom in noms:
        valeur = request.form.get(nom)
        if valeur is not None and str(valeur).strip():
            return str(valeur)
    return ''


def _mot_de_passe_depuis_formulaire(nom, champ_chiffre):
    """Lit un mot de passe en déchiffrant la variante RSA si elle est soumise.

    Compatibilité : les tests et les clients historiques POSTent le champ en
    clair ; si ``champ_chiffre`` est absent, la valeur de ``nom`` est renvoyée
    telle quelle. Si le champ chiffré est présent, le déchiffrement est
    obligatoire : un échec renvoie ``None``, sans jamais retomber sur le clair.
    """
    if request.form.get(champ_chiffre) is None:
        return request.form.get(nom)
    return crypto_rsa.dechiffrer(request.form.get(champ_chiffre))


def _premier_mot_de_passe(*paires):
    """Premier mot de passe non vide parmi des paires (clair, champ chiffré).

    Dès qu'un champ chiffré est soumis, il fait foi : un échec de
    déchiffrement ne doit jamais retomber sur une valeur en clair.
    """
    for nom, champ_chiffre in paires:
        if request.form.get(champ_chiffre) is not None:
            return _mot_de_passe_depuis_formulaire(nom, champ_chiffre) or ''
    return _premier_champ(*(nom for nom, _chiffre in paires))


@auth_bp.route('/auth/public-key')
def public_key():
    """Expose la clé publique RSA-OAEP-SHA256 au navigateur (endpoint public)."""
    reponse = jsonify({
        "cle_publique": crypto_rsa.cle_publique_pem(),
        "algorithme": "RSA-OAEP-SHA256",
        "champ": "password",
        "format": "RSA-OAEP-SHA256:<base64url>",
    })
    reponse.headers['Cache-Control'] = 'public, max-age=3600'
    return reponse


def _cible_apres_connexion():
    """Valide le paramètre ``next`` : uniquement un chemin local relatif.

    Refuse les URL absolues, les chemins protocolaires (``//``, ``/\\``), les
    antislashs et les caractères de contrôle afin d'empêcher les redirections
    ouvertes.
    """
    cible = (request.form.get('next') or request.args.get('next') or '').strip()
    if not cible.startswith('/'):
        return None
    if cible.startswith('//') or cible.startswith('/\\'):
        return None
    if '\\' in cible or any(ord(caractere) < 32 for caractere in cible):
        return None
    return cible


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Création de compte (AUTH-01) avec mot de passe haché (AUTH-03)."""
    username = (request.form.get('username') or '').strip()
    email = (request.form.get('email') or '').strip()

    if request.method == 'POST':
        mot_de_passe = (
            _mot_de_passe_depuis_formulaire('password', 'password_chiffre') or ''
        )
        confirmation = (
            _mot_de_passe_depuis_formulaire(
                'password_confirm', 'password_chiffre_confirm'
            ) or ''
        )

        if not username or not email or not mot_de_passe:
            flash("Tous les champs sont obligatoires.", 'error')
        elif _utilisateur_existe('username', username):
            flash("Ce nom d'utilisateur est déjà utilisé.", 'error')
        elif _utilisateur_existe('email', email):
            flash("Cette adresse e-mail est déjà utilisée.", 'error')
        elif len(mot_de_passe) < LONGUEUR_MIN_MOT_DE_PASSE:
            flash(
                "Le mot de passe doit contenir au moins "
                f"{LONGUEUR_MIN_MOT_DE_PASSE} caractères.",
                'error'
            )
        elif mot_de_passe != confirmation:
            flash("Les mots de passe ne correspondent pas.", 'error')
        else:
            user = User(username=username, email=email)
            user.set_password(mot_de_passe)
            try:
                db.session.add(user)
                db.session.commit()
            except Exception:
                db.session.rollback()
                flash("Impossible de créer le compte. Veuillez réessayer.", 'error')
            else:
                login_user(user, remember=bool(request.form.get('remember')))
                flash("Compte créé avec succès. Bienvenue !", 'success')
                return redirect(_url_accueil())

    return _rendre('auth/register.html', username=username, email=email)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Connexion par nom d'utilisateur ou e-mail (AUTH-02)."""
    identifiant = (
        request.form.get('username')
        or request.form.get('email')
        or request.form.get('identifiant')
        or ''
    ).strip()

    if request.method == 'POST':
        mot_de_passe = (
            _mot_de_passe_depuis_formulaire('password', 'password_chiffre') or ''
        )
        user = None
        if identifiant:
            user = User.query.filter(
                (func.lower(User.username) == identifiant.lower())
                | (func.lower(User.email) == identifiant.lower())
            ).first()

        if user is None or not _mot_de_passe_valide(user, mot_de_passe):
            # Message volontairement générique : ne révèle pas si le compte existe.
            flash("Identifiants invalides", 'error')
        else:
            login_user(user, remember=bool(request.form.get('remember')))
            flash("Connexion réussie.", 'success')
            return redirect(_cible_apres_connexion() or _url_accueil())

    return _rendre(
        'auth/login.html',
        identifiant=identifiant,
        next=request.args.get('next', '')
    )


@auth_bp.route('/logout')
def logout():
    """Déconnexion de l'utilisateur courant."""
    logout_user()
    flash("Vous êtes déconnecté.", 'success')
    return redirect(_url_sure('auth.login', '/login'))


@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """Consultation et modification du profil (AUTH-04)."""
    username = (
        request.form.get('username', current_user.username)
        if request.method == 'POST' else current_user.username
    )
    email = (
        request.form.get('email', current_user.email)
        if request.method == 'POST' else current_user.email
    )

    if request.method == 'POST':
        username = (username or '').strip()
        email = (email or '').strip()
        ancien = _premier_mot_de_passe(
            ('old_password', 'old_password_chiffre'),
            ('ancien_mot_de_passe', 'ancien_mot_de_passe_chiffre'),
            ('current_password', 'current_password_chiffre'),
            ('mot_de_passe_actuel', 'mot_de_passe_actuel_chiffre'),
        )
        nouveau = _premier_mot_de_passe(
            ('new_password', 'new_password_chiffre'),
            ('nouveau_mot_de_passe', 'nouveau_mot_de_passe_chiffre'),
            ('password_nouveau', 'password_nouveau_chiffre'),
            ('password', 'password_chiffre'),
        )
        confirmation = _premier_mot_de_passe(
            ('new_password_confirm', 'new_password_confirm_chiffre'),
            ('nouveau_mot_de_passe_confirm', 'nouveau_mot_de_passe_confirm_chiffre'),
            ('confirm_password', 'confirm_password_chiffre'),
            ('password_confirm', 'password_chiffre_confirm'),
            ('confirmation', 'confirmation_chiffre'),
        )

        erreur = None
        if not username or not email:
            erreur = "Le nom d'utilisateur et l'adresse e-mail sont obligatoires."
        elif _utilisateur_existe('username', username, exclure_id=current_user.id):
            erreur = "Ce nom d'utilisateur est déjà utilisé."
        elif _utilisateur_existe('email', email, exclure_id=current_user.id):
            erreur = "Cette adresse e-mail est déjà utilisée."
        elif ancien or nouveau or confirmation:
            if not ancien:
                erreur = "Veuillez saisir votre ancien mot de passe."
            elif not nouveau:
                erreur = "Veuillez saisir le nouveau mot de passe."
            elif not _mot_de_passe_valide(current_user, ancien):
                erreur = "L'ancien mot de passe est incorrect."
            elif len(nouveau) < LONGUEUR_MIN_MOT_DE_PASSE:
                erreur = (
                    "Le nouveau mot de passe doit contenir au moins "
                    f"{LONGUEUR_MIN_MOT_DE_PASSE} caractères."
                )
            elif nouveau != confirmation:
                erreur = "Les nouveaux mots de passe ne correspondent pas."

        if erreur is not None:
            flash(erreur, 'error')
        else:
            try:
                current_user.username = username
                current_user.email = email
                if nouveau:
                    current_user.set_password(nouveau)
                db.session.commit()
            except Exception:
                db.session.rollback()
                flash("Impossible de mettre à jour le profil.", 'error')
            else:
                flash("Profil mis à jour avec succès.", 'success')
                return redirect(url_for('auth.profile'))

    return _rendre(
        'auth/profile.html',
        username=username or current_user.username,
        email=email or current_user.email
    )
