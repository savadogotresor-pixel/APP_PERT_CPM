"""Authentification des utilisateurs de l'application PERT/CPM.

Fournit l'inscription, la connexion, la déconnexion et la gestion du profil.
Les contrôles d'unicité et de mots de passe sont effectués côté serveur.
"""

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify,
    session, current_app,
)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash

import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import crypto_rsa
from services import mail as service_mail

try:
    from werkzeug.routing.exceptions import BuildError
except ImportError:  # Werkzeug < 2.3
    from werkzeug.routing import BuildError

from sqlalchemy import func

from extensions import db
from models import User

auth_bp = Blueprint('auth', __name__)

LONGUEUR_MIN_MOT_DE_PASSE = 8

# Vérification de l'adresse e-mail : code à 6 chiffres, 15 minutes, 5 essais.
CODE_VERIFICATION_LONGUEUR = 6
CODE_VERIFICATION_TENTATIVES_MAX = 5
CODE_VERIFICATION_DUREE_MINUTES = 15
CODE_VERIFICATION_DELAI_RENVOI = 60


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


def _verification_active():
    """La vérification e-mail est opt-in (désactivée dans les tests historiques)."""
    return bool(current_app.config.get('EMAIL_VERIFICATION_ENABLED', False))


def _maintenant_utc_naif():
    """UTC naïf, compatible avec les colonnes DateTime sans fuseau."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _generer_code_verification():
    """Code à 6 chiffres, généré par le générateur cryptographique de Python."""
    return f"{secrets.randbelow(10 ** CODE_VERIFICATION_LONGUEUR):0{CODE_VERIFICATION_LONGUEUR}d}"


def _hash_code_verification(user, code):
    """HMAC-SHA256(SECRET_KEY, « id:code ») : jamais le code en clair."""
    secret = str(current_app.config.get('SECRET_KEY') or '').encode('utf-8')
    charge = f"{getattr(user, 'id', '')}:{code}".encode('utf-8')
    return hmac.new(secret, charge, hashlib.sha256).hexdigest()


def _code_verification_correspond(user, code):
    """Comparaison à temps constant du code soumis au hash stocké."""
    hash_stocke = getattr(user, 'code_verification_hash', None)
    if not hash_stocke:
        return False
    return hmac.compare_digest(str(hash_stocke), _hash_code_verification(user, code))


def _utilisateur_par_email(email):
    """Recherche l'utilisateur associé à une adresse e-mail (insensible à la casse)."""
    if not email:
        return None
    return User.query.filter(func.lower(User.email) == email.strip().lower()).first()


def _nettoyer_session_verification():
    """Retire toutes les données temporaires de vérification de la session."""
    for cle in ('verify_email', 'verify_remember', 'verify_dernier_envoi'):
        session.pop(cle, None)


def _preparer_code_verification(user, email, remember):
    """Stocke le hash du code, tente l'envoi et informe l'utilisateur.

    Retourne le code en clair uniquement à l'appelant : il n'est ni persisté,
    ni transmis à un autre endpoint que le formulaire de vérification.
    """
    code = _generer_code_verification()
    user.code_verification_hash = _hash_code_verification(user, code)
    user.code_verification_expire = (
        _maintenant_utc_naif()
        + timedelta(minutes=CODE_VERIFICATION_DUREE_MINUTES)
    )
    user.tentatives_code = 0
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    session['verify_email'] = email
    session['verify_remember'] = bool(remember)
    session['verify_dernier_envoi'] = time.time()

    envoye = False
    if service_mail.smtp_configure():
        envoye = service_mail.envoyer_code(email, code, user.username)
    if envoye:
        flash("Un code de vérification vient de vous être envoyé par e-mail.", 'success')
        current_app.logger.info("Code de vérification envoyé par e-mail.")
    else:
        # SMTP absent : affichage unique + log serveur (jamais en base).
        flash(
            "SMTP non configuré (mode développement) : votre code est "
            f"{code}. Il n'a pas été envoyé par e-mail — ne le partagez pas.",
            'warning'
        )
        current_app.logger.warning(
            "Code de vérification (développement, SMTP absent) pour %s : %s",
            email, code,
        )
    return code


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


def _generer_username(email):
    """Dérive un nom d'utilisateur unique de l'adresse e-mail fournie."""
    base = re.sub(r'[^a-z0-9._-]+', '.', str(email or '').split('@')[0].lower())
    base = base.strip('._-')[:80] or 'utilisateur'
    username = base
    suffixe = 1
    while _utilisateur_existe('username', username):
        fin = f'.{suffixe}'
        username = base[:80 - len(fin)] + fin
        suffixe += 1
    return username


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

        if not email or not mot_de_passe:
            flash("L'adresse e-mail et le mot de passe sont obligatoires.", 'error')
        else:
            existant = _utilisateur_par_email(email)
            if existant is not None:
                # Un compte est déjà enregistré pour cette adresse : on ne
                # demande jamais à l'utilisateur d'en créer un second.
                if (
                    _verification_active()
                    and not getattr(existant, 'email_verifie', True)
                ):
                    session['verify_email'] = existant.email
                    session['verify_remember'] = bool(request.form.get('remember'))
                    session.pop('verify_dernier_envoi', None)
                    flash(
                        "Un compte est déjà associé à cette adresse e-mail. "
                        "Saisissez le code de vérification reçu pour finaliser "
                        "votre connexion.",
                        'warning'
                    )
                    return redirect(url_for('auth.verify'))
                flash(
                    "Un compte est déjà associé à cette adresse e-mail. "
                    "Connectez-vous plutôt que d'en créer un nouveau.",
                    'info'
                )
                return redirect(url_for('auth.login', email=email))
            if username and _utilisateur_existe('username', username):
                flash("Ce nom d'utilisateur est déjà utilisé.", 'error')
            elif len(mot_de_passe) < LONGUEUR_MIN_MOT_DE_PASSE:
                flash(
                    "Le mot de passe doit contenir au moins "
                    f"{LONGUEUR_MIN_MOT_DE_PASSE} caractères.",
                    'error'
                )
            elif mot_de_passe != confirmation:
                flash("Les mots de passe ne correspondent pas.", 'error')
            else:
                username = username or _generer_username(email)
                user = User(username=username, email=email)
                user.set_password(mot_de_passe)
                try:
                    db.session.add(user)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    flash(
                        "Impossible de créer le compte. Veuillez réessayer.", 'error'
                    )
                else:
                    remember = bool(request.form.get('remember'))
                    if _verification_active():
                        # Le compte existe mais n'est pas connecté tant que le code
                        # n'a pas été validé.
                        if current_user.is_authenticated:
                            logout_user()
                        try:
                            _preparer_code_verification(user, email, remember)
                        except Exception:
                            db.session.rollback()
                            flash(
                                "Impossible de préparer la vérification de l'e-mail. "
                                "Veuillez réessayer.",
                                'error'
                            )
                            return _rendre(
                                'auth/register.html', username=username, email=email
                            )
                        flash(
                            "Compte créé. Validez votre adresse e-mail pour vous connecter.",
                            'success'
                        )
                        return redirect(url_for('auth.verify'))
                    # Parcours historique (vérification désactivée) : inchangé.
                    login_user(user, remember=remember)
                    flash("Compte créé avec succès. Bienvenue !", 'success')
                    return redirect(_url_accueil())

    return _rendre('auth/register.html', username=username, email=email)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Connexion par nom d'utilisateur ou e-mail (AUTH-02)."""
    # Un utilisateur déjà authentifié n'a pas à revoir le formulaire : on le
    # renvoie vers son tableau de bord, ou vers un paramètre ``next`` local
    # valide (ex. la page protégée qu'il tentait d'ouvrir avant connexion).
    if current_user.is_authenticated:
        return redirect(_cible_apres_connexion() or _url_accueil())

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
        elif _verification_active() and not getattr(user, 'email_verifie', True):
            session['verify_email'] = user.email
            session['verify_remember'] = bool(request.form.get('remember'))
            session.pop('verify_dernier_envoi', None)
            flash(
                "Votre adresse e-mail doit être vérifiée. Saisissez le code "
                "reçu par e-mail (ou demandez-en un nouveau).",
                'warning'
            )
            return redirect(url_for('auth.verify'))
        else:
            login_user(user, remember=bool(request.form.get('remember')))
            flash("Connexion réussie.", 'success')
            return redirect(_cible_apres_connexion() or _url_accueil())

    identifiant_initial = (
        identifiant
        if request.method == 'POST'
        else (request.args.get('email') or request.args.get('identifiant') or '')
    )
    return _rendre(
        'auth/login.html',
        identifiant=identifiant_initial,
        next=request.args.get('next', '')
    )


@auth_bp.route('/verify', methods=['GET', 'POST'])
def verify():
    """Validation du code de vérification envoyé par e-mail (AUTH-05)."""
    if not _verification_active():
        flash("La vérification par e-mail n'est pas activée.", 'info')
        return redirect(url_for('auth.login'))

    email = session.get('verify_email')
    if not email:
        flash("Aucune vérification n'est en cours. Connectez-vous d'abord.", 'error')
        return redirect(url_for('auth.login'))

    user = _utilisateur_par_email(email)
    if user is None or getattr(user, 'email_verifie', True):
        _nettoyer_session_verification()
        if user is not None:
            flash("Adresse e-mail déjà vérifiée. Vous pouvez vous connecter.", 'success')
        else:
            flash("Compte introuvable. Veuillez vous connecter.", 'error')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        code = (request.form.get('code') or '').strip()
        expiration = user.code_verification_expire
        if expiration is not None and expiration <= _maintenant_utc_naif():
            user.code_verification_hash = None
            user.code_verification_expire = None
            user.tentatives_code = 0
            db.session.commit()
            flash("Ce code a expiré. Demandez-en un nouveau.", 'error')
        elif not user.code_verification_hash:
            flash("Aucun code valide n'est en attente. Demandez-en un nouveau.", 'error')
        elif _code_verification_correspond(user, code):
            user.email_verifie = True
            user.code_verification_hash = None
            user.code_verification_expire = None
            user.tentatives_code = 0
            db.session.commit()
            remember = bool(session.get('verify_remember', False))
            _nettoyer_session_verification()
            login_user(user, remember=remember)
            flash("Adresse e-mail vérifiée. Bienvenue !", 'success')
            return redirect(_url_accueil())
        else:
            # Message générique : on ne précise pas ce qui était attendu.
            user.tentatives_code = (user.tentatives_code or 0) + 1
            if user.tentatives_code >= CODE_VERIFICATION_TENTATIVES_MAX:
                user.code_verification_hash = None
                user.code_verification_expire = None
                db.session.commit()
                flash(
                    "Trop de tentatives infructueuses. Demandez un nouveau code.",
                    'error'
                )
            else:
                db.session.commit()
                flash("Code incorrect. Veuillez réessayer.", 'error')

    restantes = max(
        0,
        CODE_VERIFICATION_TENTATIVES_MAX - (user.tentatives_code or 0)
    )
    return _rendre(
        'auth/verify.html',
        email=email,
        tentatives_restantes=restantes,
        delai_renvoi=CODE_VERIFICATION_DELAI_RENVOI,
    )


@auth_bp.route('/verify/renvoyer', methods=['POST'])
def renvoyer_code():
    """Régénère et renvoie le code, au plus une fois par minute (CSRF global)."""
    if not _verification_active():
        flash("La vérification par e-mail n'est pas activée.", 'info')
        return redirect(url_for('auth.login'))

    email = session.get('verify_email')
    if not email:
        flash("Aucune vérification n'est en cours. Connectez-vous d'abord.", 'error')
        return redirect(url_for('auth.login'))

    dernier_envoi = session.get('verify_dernier_envoi')
    if dernier_envoi is not None:
        try:
            ecart = time.time() - float(dernier_envoi)
        except (TypeError, ValueError):
            ecart = CODE_VERIFICATION_DELAI_RENVOI
        if ecart < CODE_VERIFICATION_DELAI_RENVOI:
            restant = int(CODE_VERIFICATION_DELAI_RENVOI - ecart) + 1
            flash(
                f"Veuillez patienter {restant} secondes avant de demander "
                "un nouveau code.",
                'error'
            )
            return redirect(url_for('auth.verify'))

    user = _utilisateur_par_email(email)
    if user is None or getattr(user, 'email_verifie', True):
        _nettoyer_session_verification()
        if user is not None:
            flash("Adresse e-mail déjà vérifiée. Vous pouvez vous connecter.", 'success')
        else:
            flash("Compte introuvable. Veuillez vous connecter.", 'error')
        return redirect(url_for('auth.login'))

    try:
        _preparer_code_verification(
            user, user.email, session.get('verify_remember', False)
        )
    except Exception:
        db.session.rollback()
        flash("Impossible de renvoyer le code. Veuillez réessayer.", 'error')
    return redirect(url_for('auth.verify'))


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
