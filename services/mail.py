"""Envoi des codes de vérification d'e-mail (stdlib uniquement).

Aucune dépendance externe : ``smtplib`` et ``email.message.EmailMessage``.
Le code en clair n'est utilisé que pour composer le message ; il n'est jamais
écrit en base de données ni journalisé par ce module.
"""

import smtplib
from email.message import EmailMessage
from html import escape

from flask import current_app


def smtp_configure():
    """Vrai si la configuration SMTP est exploitable, faux sinon."""
    config = current_app.config
    serveur = str(config.get('MAIL_SERVER') or '').strip()
    expediteur = str(config.get('MAIL_DEFAULT_SENDER') or '').strip()
    if not serveur or not expediteur:
        return False
    try:
        port = int(config.get('MAIL_PORT') or 0)
    except (TypeError, ValueError):
        return False
    if port <= 0:
        return False
    identifiant = str(config.get('MAIL_USERNAME') or '').strip()
    if identifiant and not config.get('MAIL_PASSWORD'):
        return False
    return True


def envoyer_code(email, code, username):
    """Envoie le code à ``email`` ; retourne False sans lever en cas d'échec.

    Le message contient une version texte et une version HTML simple. Les
    détails d'erreur SMTP ne sont jamais journalisés avec le code.
    """
    if not smtp_configure():
        return False

    config = current_app.config
    serveur = str(config['MAIL_SERVER']).strip()
    port = int(config['MAIL_PORT'])
    expediteur = str(config['MAIL_DEFAULT_SENDER']).strip()
    identifiant = str(config.get('MAIL_USERNAME') or '').strip()
    mot_de_passe = config.get('MAIL_PASSWORD') or ''

    texte = (
        f"Bonjour {username},\n\n"
        "Voici votre code de vérification PERT/CPM :\n"
        f"{code}\n\n"
        "Ce code est valable 15 minutes et ne doit être partagé avec personne.\n"
        "Si vous n'avez pas demandé ce code, ignorez cet e-mail.\n"
    )
    html = (
        "<!DOCTYPE html><html lang=\"fr\"><body>"
        f"<p>Bonjour {escape(str(username))},</p>"
        "<p>Voici votre code de vérification PERT/CPM :</p>"
        f"<p style=\"font-size:1.4em;font-weight:bold;letter-spacing:.2em;\">{escape(str(code))}</p>"
        "<p>Ce code est valable 15 minutes et ne doit être partagé avec personne.</p>"
        "<p>Si vous n'avez pas demandé ce code, ignorez cet e-mail.</p>"
        "</body></html>"
    )

    message = EmailMessage()
    message['Subject'] = 'Votre code de vérification PERT/CPM'
    message['From'] = expediteur
    message['To'] = email
    message.set_content(texte)
    message.add_alternative(html, subtype='html')

    try:
        with smtplib.SMTP(serveur, port, timeout=10) as smtp:
            if config.get('MAIL_USE_TLS'):
                smtp.starttls()
            if identifiant:
                smtp.login(identifiant, mot_de_passe)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        # Volontairement générique : ni le code, ni les identifiants SMTP.
        current_app.logger.warning(
            "Envoi du code de vérification impossible (%s)", type(exc).__name__
        )
        return False
    return True
