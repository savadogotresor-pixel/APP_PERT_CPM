/*
 * Moteur PERT/CPM offline — port ES5 strict de logic/pert.py.
 *
 * Le moteur Python reste la source de vérité : chaque étape (validation, Kahn,
 * passes avant/arrière, marges, énumération des chemins critiques) est
 * reproduite dans le même ordre pour garantir une parité stricte des résultats.
 *
 * API publique : window.PertEngine.validate(taches) et
 *                window.PertEngine.compute(taches)
 * (module.exports est également renseigné en environnement CommonJS).
 */
(function (root) {
    "use strict";

    var EPSILON = 1e-9;
    var ARROW = " \u2192 ";

    /* ---------- utilitaires de parité avec le Python ---------- */

    function aCle(nom) {
        return "$" + nom;
    }

    function aPropriete(objet, cle) {
        return Object.prototype.hasOwnProperty.call(objet, cle);
    }

    function possede(table, nom) {
        return Object.prototype.hasOwnProperty.call(table, aCle(nom));
    }

    function obtenir(table, nom) {
        return table[aCle(nom)];
    }

    function poser(table, nom, valeur) {
        table[aCle(nom)] = valeur;
    }

    function estDictionnaire(valeur) {
        return valeur !== null && typeof valeur === "object" &&
               !Array.isArray(valeur);
    }

    function nettoie(texte) {
        return texte.replace(/^\s+/, "").replace(/\s+$/, "");
    }

    /* Représentation Python des valeurs utilisées dans les messages d'erreur.
       Les nombres entiers JS correspondent aux entiers Python (les durées
       décimales ne sont pas discriminables après passage dans le moteur JS). */
    function reprPython(valeur) {
        var texte;
        if (typeof valeur === "string") {
            texte = valeur.replace(/\\/g, "\\\\")
                          .replace(/'/g, "\\'")
                          .replace(/\n/g, "\\n")
                          .replace(/\r/g, "\\r")
                          .replace(/\t/g, "\\t");
            return "'" + texte + "'";
        }
        if (typeof valeur === "boolean") {
            return valeur ? "True" : "False";
        }
        if (typeof valeur === "number" && isFinite(valeur)) {
            return String(valeur);
        }
        if (valeur === null || typeof valeur === "undefined") {
            return "None";
        }
        if (typeof valeur === "number") {
            if (isNaN(valeur)) {
                return "nan";
            }
            return valeur > 0 ? "inf" : "-inf";
        }
        if (Array.isArray(valeur)) {
            var elements = [];
            for (var i = 0; i < valeur.length; i++) {
                elements.push(reprPython(valeur[i]));
            }
            return "[" + elements.join(", ") + "]";
        }
        return String(valeur);
    }

    /* ---------- normalisation et validation ---------- */

    function dependancesNormalisees(taches) {
        var dependances = {};
        var noms = Object.keys(taches);
        for (var i = 0; i < noms.length; i++) {
            var nom = noms[i];
            var infos = taches[nom];
            if (!estDictionnaire(infos)) {
                poser(dependances, nom, []);
                continue;
            }
            var bruts = aPropriete(infos, "depend_de") ? infos.depend_de : null;
            if (bruts === null || typeof bruts === "undefined") {
                bruts = [];
            }
            var propres = [];
            if (Array.isArray(bruts)) {
                for (var j = 0; j < bruts.length; j++) {
                    var dep = bruts[j];
                    if (typeof dep === "string" && nettoie(dep)) {
                        propres.push(nettoie(dep));
                    }
                }
            }
            poser(dependances, nom, propres);
        }
        return dependances;
    }

    function extraireCycle(restantsNoms, dependances) {
        var restants = {};
        for (var r = 0; r < restantsNoms.length; r++) {
            restants[aCle(restantsNoms[r])] = true;
        }
        var index = {};
        var chemin = [];
        var courant = restantsNoms[0];

        while (!possede(index, courant)) {
            poser(index, courant, chemin.length);
            chemin.push(courant);
            var deps = obtenir(dependances, courant) || [];
            var preds = [];
            for (var d = 0; d < deps.length; d++) {
                if (possede(restants, deps[d])) {
                    preds.push(deps[d]);
                }
            }
            if (!preds.length) {
                break;
            }
            courant = preds[0];
        }

        var cycle;
        if (possede(index, courant)) {
            cycle = chemin.slice(obtenir(index, courant)).reverse();
        } else {
            cycle = chemin.slice();
        }
        if (!cycle.length) {
            return [String(courant), String(courant)];
        }

        var position = 0;
        for (var p = 1; p < cycle.length; p++) {
            if (cycle[p] < cycle[position]) {
                position = p;
            }
        }
        cycle = cycle.slice(position).concat(cycle.slice(0, position));
        cycle.push(cycle[0]);
        return cycle;
    }

    function kahn(noms, dependances) {
        var successeurs = {};
        var restants = {};
        var i;
        var j;

        for (i = 0; i < noms.length; i++) {
            poser(successeurs, noms[i], []);
        }
        for (i = 0; i < noms.length; i++) {
            var nom = noms[i];
            var deps = obtenir(dependances, nom) || [];
            var uniques = [];
            var vus = {};
            for (j = 0; j < deps.length; j++) {
                if (!possede(vus, deps[j])) {
                    poser(vus, deps[j], true);
                    uniques.push(deps[j]);
                }
            }
            poser(restants, nom, uniques.length);
            for (j = 0; j < uniques.length; j++) {
                obtenir(successeurs, uniques[j]).push(nom);
            }
        }

        var file = [];
        for (i = 0; i < noms.length; i++) {
            if (obtenir(restants, noms[i]) === 0) {
                file.push(noms[i]);
            }
        }
        var ordre = [];
        var tete = 0;
        while (tete < file.length) {
            var courant = file[tete++];
            ordre.push(courant);
            var succs = obtenir(successeurs, courant);
            for (i = 0; i < succs.length; i++) {
                var succ = succs[i];
                poser(restants, succ, obtenir(restants, succ) - 1);
                if (obtenir(restants, succ) === 0) {
                    file.push(succ);
                }
            }
        }

        if (ordre.length !== noms.length) {
            var bloques = [];
            for (i = 0; i < noms.length; i++) {
                if (obtenir(restants, noms[i]) > 0) {
                    bloques.push(noms[i]);
                }
            }
            var cycle = extraireCycle(bloques, dependances);
            throw new Error("Dépendance circulaire détectée : " +
                            cycle.join(ARROW));
        }
        return ordre;
    }

    function validate(taches) {
        var erreurs = [];
        if (!estDictionnaire(taches)) {
            return ["Le graphe de tâches doit être un dictionnaire nom -> informations"];
        }

        var noms = Object.keys(taches);
        var nomsConnus = {};
        var identifiantsVus = {};
        var i;
        var j;

        for (i = 0; i < noms.length; i++) {
            nomsConnus[aCle(noms[i])] = true;
        }
        for (i = 0; i < noms.length; i++) {
            var nom = noms[i];
            if (typeof nom !== "string" || !nettoie(nom)) {
                erreurs.push("Identifiant de tâche vide ou invalide : " +
                             reprPython(nom));
                continue;
            }
            var propre = nettoie(nom);
            if (possede(identifiantsVus, propre)) {
                erreurs.push("Identifiant de tâche en double : \"" +
                             propre + "\"");
            }
            poser(identifiantsVus, propre, true);
        }

        var dependances = {};
        for (i = 0; i < noms.length; i++) {
            var nomTache = noms[i];
            var infos = taches[nomTache];
            if (!estDictionnaire(infos)) {
                erreurs.push("Informations invalides pour la tâche \"" +
                             nomTache + "\" : dictionnaire attendu");
                poser(dependances, nomTache, []);
                continue;
            }

            var duree = aPropriete(infos, "duree") ? infos.duree : null;
            var prefixe = "Durée invalide pour la tâche \"" + nomTache + "\" : ";
            if (typeof duree === "boolean") {
                erreurs.push(prefixe +
                             "un booléen n'est pas une durée");
            } else if (typeof duree !== "number") {
                erreurs.push(prefixe + reprPython(duree) +
                             " n'est pas un nombre");
            } else if (!isFinite(duree)) {
                erreurs.push(prefixe + "la durée doit être finie");
            } else if (duree < 0) {
                erreurs.push(prefixe + String(duree) + " est négative");
            }

            var bruts = aPropriete(infos, "depend_de") ? infos.depend_de : null;
            if (bruts === null || typeof bruts === "undefined") {
                bruts = [];
            }
            if (typeof bruts === "string" || !Array.isArray(bruts)) {
                erreurs.push("Prédécesseurs invalides pour la tâche \"" +
                             nomTache + "\" : liste attendue");
                poser(dependances, nomTache, []);
                continue;
            }

            var propres = [];
            for (j = 0; j < bruts.length; j++) {
                var dep = bruts[j];
                if (typeof dep !== "string" || !nettoie(dep)) {
                    erreurs.push("Prédécesseur vide ou invalide pour la tâche \"" +
                                 nomTache + "\" : " + reprPython(dep));
                    continue;
                }
                var depPropre = nettoie(dep);
                propres.push(depPropre);
                if (!possede(nomsConnus, depPropre)) {
                    erreurs.push("La tâche \"" + nomTache + "\" dépend de \"" +
                                 depPropre + "\", qui n'existe pas");
                }
            }
            poser(dependances, nomTache, propres);
        }

        if (!erreurs.length) {
            try {
                kahn(noms, dependances);
            } catch (exc) {
                erreurs.push(exc.message);
            }
        }
        return erreurs;
    }

    function propre(valeur) {
        if (typeof valeur === "number" && Math.abs(valeur) <= EPSILON) {
            return 0;
        }
        return valeur;
    }

    /* ---------- calcul PERT/CPM ---------- */

    function compute(taches) {
        var erreurs = validate(taches);
        if (erreurs.length) {
            throw new Error(erreurs.join("\n"));
        }

        var noms = Object.keys(taches);
        var dependances = dependancesNormalisees(taches);
        var ordre = kahn(noms, dependances);
        var successeurs = {};
        var i;
        var j;

        for (i = 0; i < noms.length; i++) {
            poser(successeurs, noms[i], []);
        }
        for (i = 0; i < noms.length; i++) {
            var nom = noms[i];
            var deps = obtenir(dependances, nom);
            for (j = 0; j < deps.length; j++) {
                obtenir(successeurs, deps[j]).push(nom);
            }
        }

        /* Passe avant : ES / EF dans l'ordre topologique. */
        var avant = {};
        for (i = 0; i < ordre.length; i++) {
            var nomAvant = ordre[i];
            var preds = obtenir(dependances, nomAvant);
            var es = 0;
            if (preds.length) {
                es = -Infinity;
                for (j = 0; j < preds.length; j++) {
                    es = Math.max(es, obtenir(avant, preds[j]).EF);
                }
            }
            var ef = es + taches[nomAvant].duree;
            poser(avant, nomAvant, {ES: es, EF: ef});
        }

        var terminaux = [];
        for (i = 0; i < noms.length; i++) {
            if (!obtenir(successeurs, noms[i]).length) {
                terminaux.push(noms[i]);
            }
        }
        var dureeTotale = 0;
        if (terminaux.length) {
            dureeTotale = -Infinity;
            for (i = 0; i < terminaux.length; i++) {
                dureeTotale = Math.max(dureeTotale,
                                       obtenir(avant, terminaux[i]).EF);
            }
        }

        /* Passe arrière : LF / LS en sens inverse de l'ordre topologique. */
        var arriere = {};
        for (i = ordre.length - 1; i >= 0; i--) {
            var nomArriere = ordre[i];
            var succs = obtenir(successeurs, nomArriere);
            var lf = dureeTotale;
            if (succs.length) {
                lf = Infinity;
                for (j = 0; j < succs.length; j++) {
                    lf = Math.min(lf, obtenir(arriere, succs[j]).LS);
                }
            }
            var ls = lf - taches[nomArriere].duree;
            poser(arriere, nomArriere, {LS: ls, LF: lf});
        }

        var resultats = {};
        for (i = 0; i < noms.length; i++) {
            var nomRes = noms[i];
            var av = obtenir(avant, nomRes);
            var ar = obtenir(arriere, nomRes);
            var mt = propre(ar.LS - av.ES);
            var ml;
            var listeSuccs = obtenir(successeurs, nomRes);
            if (listeSuccs.length) {
                ml = Infinity;
                for (j = 0; j < listeSuccs.length; j++) {
                    ml = Math.min(ml, obtenir(avant, listeSuccs[j]).ES);
                }
                ml = propre(ml - av.EF);
            } else {
                ml = propre(dureeTotale - av.EF);
            }
            resultats[nomRes] = {
                ES: av.ES,
                EF: av.EF,
                LS: ar.LS,
                LF: ar.LF,
                marge: mt,
                marge_totale: mt,
                marge_libre: ml,
                critique: Math.abs(mt) <= EPSILON
            };
        }

        /* Tous les chemins critiques du sous-graphe critique, dans l'ordre
           topologique (itération, sans récursion). */
        var critique = {};
        for (i = 0; i < noms.length; i++) {
            poser(critique, noms[i], resultats[noms[i]].critique);
        }
        var cheminsVers = {};
        for (i = 0; i < ordre.length; i++) {
            var nomChemin = ordre[i];
            if (!obtenir(critique, nomChemin)) {
                continue;
            }
            var predsCritiques = [];
            var depsChemin = obtenir(dependances, nomChemin);
            for (j = 0; j < depsChemin.length; j++) {
                if (obtenir(critique, depsChemin[j])) {
                    predsCritiques.push(depsChemin[j]);
                }
            }
            var chemins = [];
            if (predsCritiques.length) {
                for (j = 0; j < predsCritiques.length; j++) {
                    var precedents = possede(cheminsVers, predsCritiques[j]) ?
                        obtenir(cheminsVers, predsCritiques[j]) : [];
                    for (var k = 0; k < precedents.length; k++) {
                        chemins.push(precedents[k].concat([nomChemin]));
                    }
                }
            } else {
                chemins.push([nomChemin]);
            }
            poser(cheminsVers, nomChemin, chemins);
        }

        var puits = [];
        for (i = 0; i < ordre.length; i++) {
            var nomPuits = ordre[i];
            if (!obtenir(critique, nomPuits)) {
                continue;
            }
            var aUnSuccesseurCritique = false;
            var succsPuits = obtenir(successeurs, nomPuits);
            for (j = 0; j < succsPuits.length; j++) {
                if (obtenir(critique, succsPuits[j])) {
                    aUnSuccesseurCritique = true;
                    break;
                }
            }
            if (!aUnSuccesseurCritique) {
                puits.push(nomPuits);
            }
        }

        var cheminsCritiques = [];
        for (i = 0; i < puits.length; i++) {
            var sortants = possede(cheminsVers, puits[i]) ?
                obtenir(cheminsVers, puits[i]) : [];
            for (j = 0; j < sortants.length; j++) {
                cheminsCritiques.push(sortants[j]);
            }
        }

        /* Conversion des tables internes préfixées en objets de résultat. */
        var successeursPublics = {};
        for (i = 0; i < noms.length; i++) {
            successeursPublics[noms[i]] = obtenir(successeurs, noms[i]);
        }

        return {
            duree_totale: dureeTotale,
            resultats: resultats,
            chemins_critiques: cheminsCritiques,
            successeurs: successeursPublics,
            ordre: ordre
        };
    }

    var PertEngine = {
        EPSILON: EPSILON,
        validate: validate,
        compute: compute
    };

    root.PertEngine = PertEngine;
    if (typeof module !== "undefined" && module && module.exports) {
        module.exports = PertEngine;
    }
}(typeof window !== "undefined" ? window : this));
