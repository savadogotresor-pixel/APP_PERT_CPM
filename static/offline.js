/*
 * Couche offline PERT/CPM — stockage local, état réseau et synchronisation.
 *
 * API publique (window.Offline) :
 *   estDisponible()       -> boolean, état du réseau (navigator.onLine)
 *   enregistrer(projet)   -> Promise du projet normalisé, écrit dans IndexedDB
 *   lireTous()            -> Promise d'un tableau de projets locaux
 *   supprimer(id)         -> Promise (suppression d'un projet local)
 *   sync()                -> Promise d'un rapport de synchronisation
 *
 * Base IndexedDB : "pert-cpm-offline", store "projets" (clé "id").
 * Aucun mot de passe n'est jamais lu, stocké ou transmis par ce fichier.
 * Code ES5 (var / function / Promise), compatible avec les navigateurs
 *anciens pour la syntaxe.
 */
(function (root) {
    "use strict";

    var NOM_BASE = "pert-cpm-offline";
    var VERSION_BASE = 1;
    var NOM_STORE = "projets";
    var URL_SYNC = "/offline/sync";

    var baseEnCours = null;

    /* ---------- utilitaires ---------- */

    function promesseRejetee(erreur) {
        return new Promise(function (resoudre, rejeter) {
            rejeter(erreur);
        });
    }

    function texte(valeur) {
        return valeur === null || valeur === undefined ? "" : String(valeur);
    }

    function nettoie(valeur) {
        return texte(valeur).replace(/^\s+/, "").replace(/\s+$/, "");
    }

    function lireJetonCsrf() {
        if (typeof document === "undefined" || !document) {
            return "";
        }
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? nettoie(meta.getAttribute("content")) : "";
    }

    /* ---------- IndexedDB ---------- */

    function ouvrirBase() {
        if (baseEnCours) {
            return baseEnCours;
        }
        if (!root.indexedDB) {
            return promesseRejetee(new Error("IndexedDB indisponible"));
        }

        baseEnCours = new Promise(function (resoudre, rejeter) {
            var demande;
            try {
                demande = root.indexedDB.open(NOM_BASE, VERSION_BASE);
            } catch (erreur) {
                rejeter(erreur);
                return;
            }

            demande.onupgradeneeded = function () {
                var base = demande.result;
                if (!base.objectStoreNames.contains(NOM_STORE)) {
                    base.createObjectStore(NOM_STORE, { keyPath: "id" });
                }
            };

            demande.onsuccess = function () {
                var base = demande.result;
                base.onversionchange = function () {
                    base.close();
                    baseEnCours = null;
                };
                resoudre(base);
            };

            demande.onerror = function () {
                rejeter(demande.error || new Error("Ouverture IndexedDB impossible"));
            };

            demande.onblocked = function () {
                rejeter(new Error("Base IndexedDB bloquée par un autre onglet"));
            };
        });

        baseEnCours["catch"](function () {
            baseEnCours = null;
        });
        return baseEnCours;
    }

    /* Exécute une action sur le store et résout à la fin de la transaction.
       `action` renvoie une IDBRequest ou une valeur simple. */
    function executer(mode, action) {
        return ouvrirBase().then(function (base) {
            return new Promise(function (resoudre, rejeter) {
                var transaction;
                var requete;
                var valeur;

                try {
                    transaction = base.transaction(NOM_STORE, mode);
                    requete = action(transaction.objectStore(NOM_STORE));
                } catch (erreur) {
                    rejeter(erreur);
                    return;
                }

                if (requete && typeof requete !== "function" &&
                    typeof requete.then !== "function" &&
                    typeof requete === "object" && "result" in requete) {
                    requete.onsuccess = function () {
                        valeur = requete.result;
                    };
                    requete.onerror = function () {
                        rejeter(requete.error || new Error("Requête IndexedDB échouée"));
                    };
                }

                transaction.oncomplete = function () {
                    resoudre(valeur);
                };
                transaction.onerror = function () {
                    rejeter(transaction.error || new Error("Transaction IndexedDB échouée"));
                };
                transaction.onabort = function () {
                    rejeter(transaction.error || new Error("Transaction IndexedDB annulée"));
                };
            });
        });
    }

    function normaliser(projet) {
        var source = projet || {};
        var identifiant = source.id;
        if (identifiant === null || identifiant === undefined || identifiant === "") {
            identifiant = "local-" + new Date().getTime() + "-" +
                          Math.random().toString(36).slice(2, 9);
        }
        return {
            id: texte(identifiant),
            nom: nettoie(source.nom) || "Projet sans nom",
            taches: source.taches || {},
            date_modification: source.date_modification || new Date().toISOString(),
            synchrone: false
        };
    }

    /* ---------- API publique ---------- */

    function estDisponible() {
        if (!root.navigator || typeof root.navigator.onLine !== "boolean") {
            return true;
        }
        return root.navigator.onLine;
    }

    function enregistrer(projet) {
        var enregistrement = normaliser(projet);
        return executer("readwrite", function (store) {
            return store.put(enregistrement);
        }).then(function () {
            rafraichirListe();
            return enregistrement;
        });
    }

    function lireTous() {
        return ouvrirBase().then(function (base) {
            return new Promise(function (resoudre, rejeter) {
                var projets = [];
                var transaction = base.transaction(NOM_STORE, "readonly");
                var store = transaction.objectStore(NOM_STORE);

                if (typeof store.getAll === "function") {
                    var demande = store.getAll();
                    demande.onsuccess = function () {
                        projets = demande.result || [];
                    };
                    demande.onerror = function () {
                        rejeter(demande.error || new Error("Lecture IndexedDB impossible"));
                    };
                } else {
                    var curseurDemande = store.openCursor();
                    curseurDemande.onsuccess = function () {
                        var curseur = curseurDemande.result;
                        if (curseur) {
                            projets.push(curseur.value);
                            curseur["continue"]();
                        }
                    };
                    curseurDemande.onerror = function () {
                        rejeter(curseurDemande.error || new Error("Lecture IndexedDB impossible"));
                    };
                }

                transaction.oncomplete = function () {
                    projets.sort(function (a, b) {
                        var da = texte(a && a.date_modification);
                        var db = texte(b && b.date_modification);
                        if (da === db) {
                            return 0;
                        }
                        return da > db ? -1 : 1;
                    });
                    resoudre(projets);
                };
                transaction.onerror = function () {
                    rejeter(transaction.error || new Error("Lecture IndexedDB impossible"));
                };
            });
        })["catch"](function () {
            return [];
        });
    }

    function supprimer(id) {
        return executer("readwrite", function (store) {
            return store["delete"](texte(id));
        }).then(function () {
            rafraichirListe();
            return true;
        });
    }

    function marquerSynchronise(id) {
        return ouvrirBase().then(function (base) {
            return new Promise(function (resoudre, rejeter) {
                var transaction = base.transaction(NOM_STORE, "readwrite");
                var store = transaction.objectStore(NOM_STORE);
                var demande = store.get(texte(id));

                demande.onsuccess = function () {
                    var projet = demande.result;
                    if (projet) {
                        projet.synchrone = true;
                        store.put(projet);
                    }
                };
                demande.onerror = function () {
                    rejeter(demande.error || new Error("Mise à jour impossible"));
                };
                transaction.oncomplete = function () {
                    resoudre(true);
                };
                transaction.onerror = function () {
                    rejeter(transaction.error || new Error("Mise à jour impossible"));
                };
            });
        });
    }

    /* Envoie UNIQUEMENT les champs métier : jamais de mot de passe, jamais
       de session, jamais de données d'un autre utilisateur. */
    function envoyer(projet) {
        if (!root.fetch) {
            return Promise.resolve(false);
        }
        var charges = {
            id: projet.id,
            nom: projet.nom,
            taches: projet.taches,
            date_modification: projet.date_modification
        };
        var entetes = {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest"
        };
        var jeton = lireJetonCsrf();
        if (jeton) {
            entetes["X-CSRFToken"] = jeton;
        }

        return root.fetch(URL_SYNC, {
            method: "POST",
            credentials: "same-origin",
            headers: entetes,
            body: JSON.stringify(charges)
        }).then(function (reponse) {
            return !!(reponse && reponse.ok);
        })["catch"](function () {
            return false;
        });
    }

    function sync() {
        var rapport = {
            total: 0,
            synchronises: 0,
            echecs: 0,
            horsLigne: false
        };

        if (!estDisponible()) {
            rapport.horsLigne = true;
            return Promise.resolve(rapport);
        }

        return lireTous().then(function (projets) {
            var enAttente = [];
            var i;
            for (i = 0; i < projets.length; i++) {
                if (projets[i].synchrone === false ||
                    projets[i].synchrone === undefined) {
                    enAttente.push(projets[i]);
                }
            }
            rapport.total = enAttente.length;

            var chaine = Promise.resolve();
            for (i = 0; i < enAttente.length; i++) {
                (function (projet) {
                    chaine = chaine.then(function () {
                        return envoyer(projet).then(function (reussi) {
                            if (reussi) {
                                rapport.synchronises++;
                                return marquerSynchronise(projet.id)["catch"](function () {
                                    return false;
                                });
                            }
                            rapport.echecs++;
                            return false;
                        })["catch"](function () {
                            rapport.echecs++;
                            return false;
                        });
                    });
                }(enAttente[i]));
            }
            return chaine;
        }).then(function () {
            rafraichirListe();
            return rapport;
        });
    }

    /* ---------- badge réseau ---------- */

    function appliquerBadge(badge, enLigne) {
        badge.className = "badge " + (enLigne ? "badge-success" : "badge-warning");
        badge.textContent = enLigne ? "● En ligne" : "● Hors ligne — calcul local disponible";
        badge.setAttribute("title", enLigne ?
            "Connecté : synchronisation et fonctions avancées disponibles" :
            "Hors connexion : les projets locaux et le calcul PERT restent accessibles");
    }

    function mettreAJourBadge() {
        if (typeof document === "undefined" || !document.body) {
            return;
        }
        var enLigne = estDisponible();
        var badge = document.getElementById("offline-statut");
        if (!badge) {
            badge = document.createElement("span");
            badge.id = "offline-statut";
            badge.setAttribute("role", "status");
            badge.style.position = "fixed";
            badge.style.right = "1rem";
            badge.style.bottom = "1rem";
            badge.style.zIndex = "1000";
            badge.style.boxShadow = "0 2px 8px rgba(15, 23, 42, 0.25)";
            document.body.appendChild(badge);
        }
        appliquerBadge(badge, enLigne);

        var boutonSync = document.getElementById("btn-sync");
        if (boutonSync) {
            boutonSync.disabled = !enLigne;
        }
    }

    /* ---------- liste des projets locaux ---------- */

    function formatDate(valeur) {
        var texteDate = texte(valeur);
        if (!texteDate) {
            return "—";
        }
        var date = new Date(texteDate);
        if (isNaN(date.getTime())) {
            return texteDate;
        }
        try {
            return date.toLocaleString("fr-FR");
        } catch (erreur) {
            return texteDate;
        }
    }

    function nombreTaches(projet) {
        var taches = projet && projet.taches;
        if (!taches) {
            return 0;
        }
        if (Array.isArray(taches)) {
            return taches.length;
        }
        if (typeof taches === "object") {
            return Object.keys(taches).length;
        }
        return 0;
    }

    function cellule(texteCellule, classe) {
        var td = document.createElement("td");
        td.textContent = texte(texteCellule);
        if (classe) {
            td.className = classe;
        }
        return td;
    }

    function boutonAction(label, classe, gestionnaire) {
        var bouton = document.createElement("button");
        bouton.type = "button";
        bouton.className = "btn btn-sm " + classe;
        bouton.textContent = label;
        bouton.addEventListener("click", gestionnaire, false);
        return bouton;
    }

    function rendreListe(projets) {
        var conteneur = document.getElementById("liste-projets-locaux");
        if (!conteneur) {
            return;
        }
        while (conteneur.firstChild) {
            conteneur.removeChild(conteneur.firstChild);
        }

        if (!projets || !projets.length) {
            var ligneVide = document.createElement("tr");
            var celluleVide = document.createElement("td");
            celluleVide.colSpan = 5;
            celluleVide.className = "table-empty";
            celluleVide.textContent = "Aucun projet enregistré localement pour le moment.";
            ligneVide.appendChild(celluleVide);
            conteneur.appendChild(ligneVide);
            return;
        }

        for (var i = 0; i < projets.length; i++) {
            (function (projet) {
                var ligne = document.createElement("tr");
                var actions = document.createElement("td");

                ligne.appendChild(cellule(projet.nom, "font-semibold"));
                ligne.appendChild(cellule(nombreTaches(projet) + " tâche(s)"));
                ligne.appendChild(cellule(formatDate(projet.date_modification), "timestamp"));

                var etat = document.createElement("td");
                var pastille = document.createElement("span");
                pastille.className = "badge " +
                    (projet.synchrone ? "badge-success" : "badge-warning");
                pastille.textContent = projet.synchrone ? "Synchronisé" : "À synchroniser";
                etat.appendChild(pastille);
                ligne.appendChild(etat);

                actions.appendChild(boutonAction("Charger", "btn-secondary", function () {
                    if (typeof root.chargerProjetLocal === "function") {
                        root.chargerProjetLocal(projet);
                    }
                }));
                actions.appendChild(document.createTextNode(" "));
                actions.appendChild(boutonAction("Supprimer", "btn-danger", function () {
                    if (root.confirm &&
                        !root.confirm("Supprimer ce projet de cet appareil ?")) {
                        return;
                    }
                    supprimer(projet.id)["catch"](function () {});
                }));
                ligne.appendChild(actions);

                conteneur.appendChild(ligne);
            }(projets[i]));
        }
    }

    function rafraichirListe() {
        if (typeof document === "undefined" ||
            !document.getElementById("liste-projets-locaux")) {
            return;
        }
        lireTous().then(rendreListe);
    }

    /* ---------- enregistrement du service worker + initialisation ---------- */

    function activerServiceWorker() {
        if (!root.navigator || !root.navigator.serviceWorker) {
            return;
        }
        try {
            root.navigator.serviceWorker.register("/sw.js", { scope: "/" })
                ["catch"](function () {});
        } catch (erreur) {
            /* HTTPS ou navigateur sans SW : mode offline IndexedDB conservé */
        }
    }

    function initialiser() {
        mettreAJourBadge();
        rafraichirListe();
        if (root.addEventListener) {
            root.addEventListener("online", mettreAJourBadge, false);
            root.addEventListener("offline", mettreAJourBadge, false);
        }
        activerServiceWorker();
    }

    var Offline = {
        estDisponible: estDisponible,
        enregistrer: enregistrer,
        lireTous: lireTous,
        supprimer: supprimer,
        sync: sync
    };

    root.Offline = Offline;

    if (typeof document !== "undefined" && document) {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", initialiser, false);
        } else {
            initialiser();
        }
    }
}(typeof window !== "undefined" ? window : this));
