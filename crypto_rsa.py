"""Chiffrement RSA-OAEP-SHA256 des mots de passe côté navigateur.

Le serveur détient seul la clé privée (en mémoire par défaut, ou fournie via
``RSA_PRIVATE_KEY_PEM``). La clé publique SPKI est exposée au navigateur qui
chiffre le mot de passe avant l'envoi du formulaire.

Aucune donnée sensible (clé privée, mot de passe, mot de passe chiffré) n'est
journalisée par ce module.
"""

import base64
import os
import re

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

PREFIXE = 'RSA-OAEP-SHA256:'
"""Préfixe exigé pour tout mot de passe chiffré."""

_CLE_EXTENSION = 'auth_rsa'
_TAILLE_CLE = 2048
_EXPOSANT = 65537
_LONGUEUR_MAX = 512
_BASE64URL = re.compile(r'^[A-Za-z0-9_-]+$')

# Clé active du processus. ``initialiser`` la synchronize avec
# ``app.extensions['auth_rsa']`` à chaque démarrage d'application.
_cle_privee = None


def _generer_cle():
    """Crée une clé RSA éphémère (jamais écrite sur disque ni journalisée)."""
    return rsa.generate_private_key(public_exponent=_EXPOSANT, key_size=_TAILLE_CLE)


def _charger_cle_depuis_environnement(pem):
    """Charge une clé privée PEM, avec tolérance aux ``\\n`` littéraux."""
    if isinstance(pem, bytes):
        contenu = pem
    else:
        contenu = str(pem).replace('\\n', '\n').encode('utf-8')
    return serialization.load_pem_private_key(contenu, password=None)


def initialiser(app):
    """Prépare la clé RSA de l'application, de façon idempotente.

    La clé est stockée dans ``app.extensions['auth_rsa']``. Si aucune clé n'est
    configurée, une clé RSA 2048 bits est générée en mémoire : elle ne survit
    pas à un redémarrage, ce qui est sans conséquence puisque les mots de passe
    chiffrés ne sont jamais persistés.
    """
    global _cle_privee

    cle = app.extensions.get(_CLE_EXTENSION)
    if cle is None:
        pem = os.environ.get('RSA_PRIVATE_KEY_PEM')
        if pem:
            cle = _charger_cle_depuis_environnement(pem)
        else:
            cle = _generer_cle()
        app.extensions[_CLE_EXTENSION] = cle

    _cle_privee = cle
    return cle


def _cle_active():
    """Retourne la clé privée active (clé éphémère en usage unitaire isolé)."""
    global _cle_privee
    if _cle_privee is None:
        _cle_privee = _generer_cle()
    return _cle_privee


def cle_publique_pem():
    """Retourne la clé publique au format PEM SPKI (chaîne ASCII)."""
    return _cle_active().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode('ascii')


def dechiffrer(payload):
    """Déchiffre un payload ``RSA-OAEP-SHA256:<base64url>``.

    Retourne le mot de passe en clair, ou ``None`` pour toute entrée invalide
    (préfixe absent, base64 invalide, taille excessive, déchiffrement impossible).
    """
    if not isinstance(payload, str):
        return None
    if not payload or len(payload) > _LONGUEUR_MAX:
        return None
    if not payload.startswith(PREFIXE):
        return None

    donnees = payload[len(PREFIXE):].rstrip('=')
    # Tolère l'alphabet base64 standard tout en exigeant un base64url valide.
    donnees = donnees.replace('+', '-').replace('/', '_')
    if not donnees or not _BASE64URL.match(donnees):
        return None

    try:
        octets = base64.urlsafe_b64decode(donnees + '=' * (-len(donnees) % 4))
        clair = _cle_active().decrypt(
            octets,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        mot_de_passe = clair.decode('utf-8')
    except Exception:
        # Toute anomalie (clé absente, base64 invalide, OAEP invalide, UTF-8
        # invalide) est traitée de la même manière : échec silencieux.
        return None

    if not mot_de_passe or len(mot_de_passe) > _LONGUEUR_MAX:
        return None
    return mot_de_passe
