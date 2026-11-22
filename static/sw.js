/*
 * Service worker PWA — PERT/CPM.
 *
 * Stratégies :
 *   - documents HTML : network-first, repli sur le cache puis sur /offline ;
 *   - ressources statiques (/static/) : cache-first ;
 *   - tout le reste : laissé au réseau.
 *
 * Sécurité : aucune requête sensible (connexion, auth, projets, exports,
 * résultats utilisateur) n'est mise en cache. Code ES5 (var, functions).
 */
(function (self) {
    "use strict";

    var PREFIXE_CACHE = "pert-cpm-";
    var CACHE_ACTUEL = PREFIXE_CACHE + "shell-v2";
    var URL_OFFLINE = "/offline";

    /* App shell pré-cachée à l'installation. Chaque ajout est isolé : une
       route temporairement indisponible ne fait pas échouer l'installation. */
    var SHELL = [
        "/",
        "/static/style.css",
        "/static/design.css?v=3",
        "/static/resultats.css",
        "/static/pert-engine.js",
        "/static/offline.js",
        "/manifest.webmanifest",
        URL_OFFLINE
    ];

    /* Préfixes dont les réponses ne doivent JAMAIS être persistées : ils
       portent des données de compte, des projets ou des résultats privés. */
    var ZONES_SENSIBLES = [
        "/login",
        "/logout",
        "/auth/",
        "/admin",
        "/projects",
        "/export",
        "/resultats",
        "/optimisation",
        "/conseil",
        "/api/"
    ];

    function cheminDe(url) {
        try {
            return new URL(String(url), self.location.href).pathname;
        } catch (erreur) {
            return "";
        }
    }

    function estMemeOrigine(url) {
        try {
            return new URL(String(url), self.location.href).origin ===
                   self.location.origin;
        } catch (erreur) {
            return false;
        }
    }

    function estSensible(chemin) {
        var p = chemin || "";
        var i;
        if (p.charAt(0) !== "/") {
            return true;
        }
        for (i = 0; i < ZONES_SENSIBLES.length; i++) {
            if (p.indexOf(ZONES_SENSIBLES[i]) === 0) {
                return true;
            }
        }
        return false;
    }

    function peutCacher(chemin) {
        return !estSensible(chemin);
    }

    function mettreEnCache(cache, requete, reponse) {
        if (!reponse || !reponse.ok || reponse.type !== "basic") {
            return;
        }
        try {
            cache.put(requete, reponse.clone());
        } catch (erreur) {
            /* stockage plein ou requête non supportée : on continue */
        }
    }

    function reponseAttente() {
        try {
            return new Response(
                "<!DOCTYPE html><html lang=\"fr\"><head><meta charset=\"utf-8\">" +
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
                "<title>Hors ligne</title></head><body><h1>Mode hors ligne</h1>" +
                "<p>La page demandée est indisponible. Le calcul local des tâches " +
                "reste accessible.</p></body></html>",
                { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }
            );
        } catch (erreur) {
            return undefined;
        }
    }

    /* Ressources statiques : cache d'abord, réseau en secours. */
    function cacheFirst(requete) {
        return caches.open(CACHE_ACTUEL).then(function (cache) {
            return cache.match(requete).then(function (repondu) {
                if (repondu) {
                    return repondu;
                }
                return fetch(requete).then(function (reponse) {
                    mettreEnCache(cache, requete, reponse);
                    return reponse;
                });
            });
        });
    }

    /* Documents : réseau d'abord, puis cache de la page, puis /offline.
       Les zones sensibles ne sont jamais lues dans le cache. */
    function networkFirstDocument(requete, chemin) {
        var autorise = peutCacher(chemin);
        return fetch(requete).then(function (reponse) {
            if (autorise && reponse) {
                var copie = reponse.clone();
                caches.open(CACHE_ACTUEL).then(function (cache) {
                    mettreEnCache(cache, requete, copie);
                })["catch"](function () {});
            }
            return reponse;
        })["catch"](function () {
            return caches.open(CACHE_ACTUEL).then(function (cache) {
                if (!autorise) {
                    return cache.match(URL_OFFLINE);
                }
                return cache.match(requete).then(function (repondu) {
                    if (repondu) {
                        return repondu;
                    }
                    return cache.match(URL_OFFLINE);
                });
            }).then(function (repondu) {
                return repondu || reponseAttente();
            });
        });
    }

    self.addEventListener("install", function (evenement) {
        evenement.waitUntil(
            caches.open(CACHE_ACTUEL).then(function (cache) {
                var ajouts = [];
                for (var i = 0; i < SHELL.length; i++) {
                    (function (url) {
                        ajouts.push(cache.add(url)["catch"](function () {
                            return null;
                        }));
                    }(SHELL[i]));
                }
                return Promise.all(ajouts);
            }).then(function () {
                if (self.skipWaiting) {
                    return self.skipWaiting();
                }
                return null;
            })
        );
    });

    self.addEventListener("activate", function (evenement) {
        evenement.waitUntil(
            caches.keys().then(function (cles) {
                var suppressions = [];
                for (var i = 0; i < cles.length; i++) {
                    if (cles[i].indexOf(PREFIXE_CACHE) === 0 &&
                        cles[i] !== CACHE_ACTUEL) {
                        suppressions.push(caches["delete"](cles[i]));
                    }
                }
                return Promise.all(suppressions);
            }).then(function () {
                if (self.clients && self.clients.claim) {
                    return self.clients.claim();
                }
                return null;
            })
        );
    });

    self.addEventListener("fetch", function (evenement) {
        var requete = evenement.request;
        if (!requete || requete.method !== "GET") {
            return;
        }
        if (!estMemeOrigine(requete.url)) {
            return;
        }

        var chemin = cheminDe(requete.url);
        var accepteHtml = false;
        try {
            accepteHtml = /text\/html/.test(requete.headers.get("Accept") || "");
        } catch (erreur) {
            accepteHtml = false;
        }
        var estDocument = requete.mode === "navigate" || accepteHtml;

        if (chemin.indexOf("/static/") === 0) {
            evenement.respondWith(cacheFirst(requete));
            return;
        }
        if (estDocument) {
            evenement.respondWith(networkFirstDocument(requete, chemin));
        }
        /* Les autres requêtes (JSON, exports, auth…) ne sont pas interceptées :
           elles vont directement au réseau et ne sont jamais stockées. */
    });

    self.addEventListener("message", function (evenement) {
        if (evenement && evenement.data && evenement.data.type === "SKIP_WAITING" &&
            self.skipWaiting) {
            self.skipWaiting();
        }
    });
}(self));
