/* ==========================================================================
   auth_rsa.js — chiffrement RSA-OAEP-SHA256 des mots de passe côté navigateur.

   Script autonome : aucune dépendance externe, uniquement l'API Web Crypto
   native (crypto.subtle). La clé privée ne quitte jamais le serveur et le mot
   de passe en clair n'est jamais affiché ni journalisé.

   En cas d'indisponibilité de crypto.subtle ou de la clé publique, le
   formulaire est soumis en clair (comportement historique, sûr en HTTPS).
   ========================================================================== */
(function (window) {
    'use strict';

    var PREFIXE = 'RSA-OAEP-SHA256:';
    var CHEMIN_CLE = '/auth/public-key';
    var clePubliquePromesse = null;

    /** Encode un ArrayBuffer en base64url (sans padding). */
    function enBase64Url(buffer) {
        var octets = new Uint8Array(buffer);
        var chaine = '';
        var i;
        for (i = 0; i < octets.length; i += 1) {
            chaine += String.fromCharCode(octets[i]);
        }
        return window.btoa(chaine)
            .replace(/\+/g, '-')
            .replace(/\//g, '_')
            .replace(/=+$/g, '');
    }

    /** Convertit un PEM SPKI en ArrayBuffer importable par crypto.subtle. */
    function pemVersDer(pem) {
        var contenu = pem
            .replace(/-----BEGIN PUBLIC KEY-----/g, '')
            .replace(/-----END PUBLIC KEY-----/g, '')
            .replace(/\s+/g, '');
        var binaire = window.atob(contenu);
        var octets = new Uint8Array(binaire.length);
        var i;
        for (i = 0; i < binaire.length; i += 1) {
            octets[i] = binaire.charCodeAt(i);
        }
        return octets.buffer;
    }

    /** Récupère (et met en cache) la clé publique de chiffrement. */
    function recupererCle() {
        if (!clePubliquePromesse) {
            clePubliquePromesse = window.fetch(CHEMIN_CLE, {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' }
            }).then(function (reponse) {
                if (!reponse.ok) {
                    throw new Error('cle publique indisponible');
                }
                return reponse.json();
            }).then(function (donnees) {
                if (!donnees || typeof donnees.cle_publique !== 'string') {
                    throw new Error('cle publique invalide');
                }
                return window.crypto.subtle.importKey(
                    'spki',
                    pemVersDer(donnees.cle_publique),
                    { name: 'RSA-OAEP', hash: 'SHA-256' },
                    false,
                    ['encrypt']
                );
            }).catch(function (erreur) {
                clePubliquePromesse = null; // autorise une nouvelle tentative
                throw erreur;
            });
        }
        return clePubliquePromesse;
    }

    /** Chiffre une valeur avec la clé publique et ajoute le préfixe convenu. */
    function chiffrer(valeur, cle) {
        return window.crypto.subtle.encrypt(
            { name: 'RSA-OAEP' },
            cle,
            new window.TextEncoder().encode(valeur)
        ).then(function (chiffre) {
            return PREFIXE + enBase64Url(chiffre);
        });
    }

    /**
     * Active le chiffrement sur un formulaire.
     *
     * @param {HTMLFormElement} formulaire le formulaire à protéger
     * @param {Array<{plaintext: string, chiffre: string}>} paires couples
     *        « champ mot de passe visible » / « champ caché chiffré »
     */
    function activer(formulaire, paires) {
        if (!formulaire || !paires || !paires.length) {
            return;
        }
        // Fallback silencieux : Web Crypto indisponible (HTTP non sécurisé,
        // navigateur ancien) -> le formulaire reste soumis en clair.
        if (!window.crypto || !window.crypto.subtle || !window.fetch ||
            !window.TextEncoder || !window.btoa || !window.atob) {
            return;
        }

        var enCours = false;

        formulaire.addEventListener('submit', function (evenement) {
            if (enCours) {
                evenement.preventDefault();
                return;
            }

            var aChiffrer = [];
            var i;
            for (i = 0; i < paires.length; i += 1) {
                var source = formulaire.elements.namedItem(paires[i].plaintext);
                var cible = formulaire.elements.namedItem(paires[i].chiffre);
                if (!source || !cible) {
                    return; // configuration invalide : soumission claire
                }
                if (!source.value) {
                    continue; // champ facultatif laissé vide (profil)
                }
                aChiffrer.push({ source: source, cible: cible, valeur: source.value });
            }

            if (!aChiffrer.length) {
                return; // rien à chiffrer : comportement historique
            }

            evenement.preventDefault();
            enCours = true;

            recupererCle().then(function (cle) {
                return Promise.all(aChiffrer.map(function (champ) {
                    return chiffrer(champ.valeur, cle).then(function (valeurChiffree) {
                        return { champ: champ, valeur: valeurChiffree };
                    });
                }));
            }).then(function (resultats) {
                // Tout est chiffré : on bascule atomiquement vers les champs
                // cachés. Les champs désactivés ne sont pas soumis et leur
                // attribut required est ignoré par le navigateur.
                resultats.forEach(function (resultat) {
                    resultat.champ.cible.value = resultat.valeur;
                    resultat.champ.cible.disabled = false;
                    resultat.champ.source.value = '';
                    resultat.champ.source.disabled = true;
                });
                formulaire.submit();
            }).catch(function () {
                // Échec réseau ou cryptographique : on ne modifie rien et on
                // laisse le formulaire partir en clair, silencieusement.
                enCours = false;
                formulaire.submit();
            });
        });
    }

    window.AuthRSA = { activer: activer };
}(window));
