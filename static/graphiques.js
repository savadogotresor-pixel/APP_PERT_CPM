/* ==========================================================================
   graphiques.js — visualisations avancées générées côté navigateur
   --------------------------------------------------------------------------
   Aucune librairie externe : les cinq graphiques sont construits en SVG natif
   avec createElementNS. Toutes les données proviennent de la variable injectée
   par resultats.html (`DONNEES_GRAPHiques`), elle-même issue du moteur
   canonique logic.pert.compute. Aucune valeur n'est recalculée ici.
   JavaScript ES5 : aucun `let`/`const`/arrow function, compatible older DOM.
   ========================================================================== */

(function (window, document) {
    'use strict';

    var SVG_NS = 'http://www.w3.org/2000/svg';
    var LARGEUR = 640;
    var HAUTEUR = 360;
    var MARGE = { gauche: 60, droite: 20, haut: 58, bas: 74 };

    /* Palette indigo, alignée sur la refonte (design.css / badges .badge-role). */
    var COULEURS = {
        indigo: '#4f46e5',
        indigoClair: '#818cf8',
        cyan: '#0891b2',
        ambre: '#d97706',
        violet: '#7c3aed',
        critique: '#dc2626',
        success: '#059669',
        axe: '#cbd5e1',
        grille: '#e2e8f0'
    };

    var GRAPHIQUES = [
        { cle: 'durees', titre: 'Histogramme des durées de tâches' },
        { cle: 'marges', titre: 'Histogramme des marges totales' },
        { cle: 'criticite', titre: 'Répartition critiques / non critiques' },
        { cle: 'nuage', titre: 'Nuage de points : durée vs marge totale' },
        { cle: 'calendrier', titre: 'Barres ES / EF / LS / LF par tâche' }
    ];

    var compteurSvg = 0;

    /* ------------------------------------------------------------------
       Utilitaires
       ------------------------------------------------------------------ */

    function nombre(valeur) {
        var n = Number(valeur);
        return isFinite(n) ? n : 0;
    }

    function arrondi(valeur) {
        return Math.round(valeur * 100) / 100;
    }

    function format(valeur) {
        if (Math.abs(valeur - Math.round(valeur)) < 0.000001) {
            return String(Math.round(valeur));
        }
        return valeur.toFixed(1);
    }

    function vide(noeud) {
        while (noeud.firstChild) {
            noeud.removeChild(noeud.firstChild);
        }
    }

    /* Création d'un élément SVG : les textes passent TOUJOURS par createTextNode
       (jamais innerHTML) car les noms de tâches sont saisis par l'utilisateur. */
    function elementSVG(tag, attributs, texte) {
        var noeud = document.createElementNS(SVG_NS, tag);
        var cle;
        if (attributs) {
            for (cle in attributs) {
                if (Object.prototype.hasOwnProperty.call(attributs, cle)) {
                    noeud.setAttribute(cle, String(attributs[cle]));
                }
            }
        }
        if (texte !== undefined && texte !== null) {
            noeud.appendChild(document.createTextNode(String(texte)));
        }
        return noeud;
    }

    function donneesBrutes() {
        var donnees = window.DONNEES_GRAPHiques;
        if (!donnees && typeof DONNEES_GRAPHiques !== 'undefined') {
            donnees = DONNEES_GRAPHiques;
        }
        return donnees || {};
    }

    /* Normalise { taches, resultats } en une liste de fiches complètes. */
    function enregistrements() {
        var donnees = donneesBrutes();
        var taches = donnees.taches || {};
        var resultats = donnees.resultats || {};
        var noms = taches && typeof taches === 'object' ? Object.keys(taches) : [];
        var liste = [];
        var i, nom, tache, resultat;

        for (i = 0; i < noms.length; i += 1) {
            nom = noms[i];
            tache = taches[nom] || {};
            resultat = resultats[nom] || {};
            liste.push({
                nom: String(nom),
                duree: nombre(tache.duree),
                ES: nombre(resultat.ES),
                EF: nombre(resultat.EF),
                LS: nombre(resultat.LS),
                LF: nombre(resultat.LF),
                marge: nombre(resultat.marge !== undefined
                    ? resultat.marge : resultat.marge_totale),
                critique: resultat.critique === true
            });
        }
        return liste;
    }

    function jolieEchelle(maximum) {
        if (!(maximum > 0)) {
            return { max: 1, pas: 1 };
        }
        var exposant = Math.pow(10, Math.floor(Math.log(maximum) / Math.LN10));
        var pas = Math.max(exposant, Math.ceil(maximum / 5 / exposant) * exposant);
        return { max: Math.ceil(maximum / pas) * pas, pas: pas };
    }

    /* ------------------------------------------------------------------
       Structure commune des SVG (titres ARIA, axes, légendes)
       ------------------------------------------------------------------ */

    function nouveauSvg(titre, description) {
        compteurSvg += 1;
        var idTitre = 'graphique-titre-' + compteurSvg;
        var idDescription = 'graphique-desc-' + compteurSvg;
        var svg = elementSVG('svg', {
            'viewBox': '0 0 ' + LARGEUR + ' ' + HAUTEUR,
            'preserveAspectRatio': 'xMidYMid meet',
            'class': 'graphique-svg',
            'role': 'img',
            'aria-labelledby': idTitre + ' ' + idDescription
        });
        svg.appendChild(elementSVG('title', { 'id': idTitre }, titre));
        svg.appendChild(elementSVG('desc', { 'id': idDescription }, description));
        return svg;
    }

    function ligneBase(svg) {
        var y = HAUTEUR - MARGE.bas;
        svg.appendChild(elementSVG('line', {
            x1: MARGE.gauche, y1: y,
            x2: LARGEUR - MARGE.droite, y2: y,
            stroke: COULEURS.axe, 'stroke-width': 1
        }));
    }

    function axeVertical(svg, echelle, etiquette) {
        var y0 = HAUTEUR - MARGE.bas;
        var hauteurUtile = y0 - MARGE.haut;
        var valeur = 0;
        var y;

        while (valeur <= echelle.max + 0.000001) {
            y = y0 - (valeur / echelle.max) * hauteurUtile;
            svg.appendChild(elementSVG('line', {
                x1: MARGE.gauche - 4, y1: arrondi(y),
                x2: MARGE.gauche, y2: arrondi(y),
                stroke: COULEURS.axe, 'stroke-width': 1
            }));
            svg.appendChild(elementSVG('text', {
                x: MARGE.gauche - 10, y: arrondi(y + 4),
                'text-anchor': 'end'
            }, format(valeur)));
            if (valeur > 0) {
                svg.appendChild(elementSVG('line', {
                    x1: MARGE.gauche, y1: arrondi(y),
                    x2: LARGEUR - MARGE.droite, y2: arrondi(y),
                    stroke: COULEURS.grille, 'stroke-width': 0.6,
                    'stroke-dasharray': '3 4'
                }));
            }
            valeur += echelle.pas;
        }

        svg.appendChild(elementSVG('text', {
            x: 16, y: arrondi(MARGE.haut + hauteurUtile / 2),
            transform: 'rotate(-90 16 ' + arrondi(MARGE.haut + hauteurUtile / 2) + ')',
            'text-anchor': 'middle', 'class': 'graphique-titre-axe'
        }, etiquette));
    }

    function graduationsHorizontales(svg, echelle) {
        var y0 = HAUTEUR - MARGE.bas;
        var largeurUtile = LARGEUR - MARGE.gauche - MARGE.droite;
        var valeur = 0;
        while (valeur <= echelle.max + 0.000001) {
            var x = MARGE.gauche + (valeur / echelle.max) * largeurUtile;
            svg.appendChild(elementSVG('line', {
                x1: arrondi(x), y1: y0,
                x2: arrondi(x), y2: y0 + 4,
                stroke: COULEURS.axe, 'stroke-width': 1
            }));
            svg.appendChild(elementSVG('text', {
                x: arrondi(x), y: y0 + 18, 'text-anchor': 'middle'
            }, format(valeur)));
            valeur += echelle.pas;
        }
    }

    function titreAxeHorizontal(svg, etiquette) {
        svg.appendChild(elementSVG('text', {
            x: LARGEUR / 2, y: HAUTEUR - 16,
            'text-anchor': 'middle', 'class': 'graphique-titre-axe'
        }, etiquette));
    }

    function categories(svg, liste) {
        var y0 = HAUTEUR - MARGE.bas;
        var pas = (LARGEUR - MARGE.gauche - MARGE.droite) / Math.max(liste.length, 1);
        var incline = liste.length > 8;
        var i, x, etiquette;

        for (i = 0; i < liste.length; i += 1) {
            x = MARGE.gauche + i * pas + pas / 2;
            etiquette = elementSVG('text', {
                x: arrondi(x), y: y0 + (incline ? 16 : 18),
                'text-anchor': incline ? 'end' : 'middle'
            }, liste[i].nom);
            if (incline) {
                etiquette.setAttribute('transform',
                    'rotate(-35 ' + arrondi(x) + ' ' + arrondi(y0 + 16) + ')');
            }
            svg.appendChild(etiquette);
        }
    }

    function legende(svg, entrees, y) {
        var x = MARGE.gauche + 12;
        var i, libelle;
        for (i = 0; i < entrees.length; i += 1) {
            libelle = String(entrees[i].libelle);
            svg.appendChild(elementSVG('rect', {
                x: arrondi(x), y: y - 10, width: 12, height: 12,
                rx: 3, fill: entrees[i].couleur
            }));
            svg.appendChild(elementSVG('text', {
                x: arrondi(x + 18), y: y,
                'class': 'graphique-legende-texte'
            }, libelle));
            x += 32 + libelle.length * 6.4;
        }
    }

    function infobulle(noeud, texte) {
        noeud.appendChild(elementSVG('title', {}, texte));
    }

    /* ------------------------------------------------------------------
       1. Histogramme des durées
       ------------------------------------------------------------------ */

    function dessineDurees(svg, liste) {
        var maximum = 0;
        var i;
        for (i = 0; i < liste.length; i += 1) {
            maximum = Math.max(maximum, liste[i].duree);
        }
        var echelle = jolieEchelle(maximum);
        var y0 = HAUTEUR - MARGE.bas;
        var hauteurUtile = y0 - MARGE.haut;
        var pas = (LARGEUR - MARGE.gauche - MARGE.droite) / Math.max(liste.length, 1);
        var largeurBarre = Math.min(32, pas * 0.68);
        var barre, x, hauteur;

        ligneBase(svg);
        axeVertical(svg, echelle, 'Durée (jours)');
        categories(svg, liste);
        titreAxeHorizontal(svg, 'Tâches');

        for (i = 0; i < liste.length; i += 1) {
            x = MARGE.gauche + i * pas + (pas - largeurBarre) / 2;
            hauteur = (liste[i].duree / echelle.max) * hauteurUtile;
            barre = elementSVG('rect', {
                x: arrondi(x), y: arrondi(y0 - hauteur),
                width: arrondi(largeurBarre),
                height: arrondi(Math.max(hauteur, 0.8)),
                rx: 3,
                fill: liste[i].critique ? COULEURS.critique : COULEURS.indigo,
                opacity: 0.9
            });
            infobulle(barre, liste[i].nom + ' : ' + format(liste[i].duree) + ' jour(s)');
            svg.appendChild(barre);
        }

        legende(svg, [
            { libelle: 'Critique', couleur: COULEURS.critique },
            { libelle: 'Non critique', couleur: COULEURS.indigo }
        ], 30);
    }

    /* ------------------------------------------------------------------
       2. Histogramme des marges totales
       ------------------------------------------------------------------ */

    function dessineMarges(svg, liste) {
        var maximum = 0;
        var i;
        for (i = 0; i < liste.length; i += 1) {
            maximum = Math.max(maximum, liste[i].marge);
        }
        var echelle = jolieEchelle(maximum);
        var y0 = HAUTEUR - MARGE.bas;
        var hauteurUtile = y0 - MARGE.haut;
        var pas = (LARGEUR - MARGE.gauche - MARGE.droite) / Math.max(liste.length, 1);
        var largeurBarre = Math.min(32, pas * 0.68);
        var barre, x, hauteur, valeur;

        ligneBase(svg);
        axeVertical(svg, echelle, 'Marge totale (jours)');
        categories(svg, liste);
        titreAxeHorizontal(svg, 'Tâches');

        for (i = 0; i < liste.length; i += 1) {
            x = MARGE.gauche + i * pas + (pas - largeurBarre) / 2;
            valeur = liste[i].marge;
            hauteur = (valeur / echelle.max) * hauteurUtile;
            barre = elementSVG('rect', {
                x: arrondi(x), y: arrondi(y0 - hauteur),
                width: arrondi(largeurBarre),
                height: arrondi(Math.max(hauteur, 1.5)),
                rx: 3,
                fill: valeur <= 0 ? COULEURS.critique : COULEURS.success,
                opacity: 0.9
            });
            infobulle(barre, liste[i].nom + ' : marge totale ' + format(valeur) + ' jour(s)');
            svg.appendChild(barre);
            if (valeur > 0 && liste.length <= 14) {
                svg.appendChild(elementSVG('text', {
                    x: arrondi(x + largeurBarre / 2),
                    y: arrondi(y0 - hauteur - 5),
                    'text-anchor': 'middle', 'class': 'graphique-valeur'
                }, format(valeur)));
            }
        }

        legende(svg, [
            { libelle: 'Marge nulle (critique)', couleur: COULEURS.critique },
            { libelle: 'Marge > 0', couleur: COULEURS.success }
        ], 30);
    }

    /* ------------------------------------------------------------------
       3. Diagramme circulaire (donut) critiques / non critiques
       ------------------------------------------------------------------ */

    function arcDonut(cx, cy, externe, interne, debut, fin) {
        var grandArc = (fin - debut) > Math.PI ? 1 : 0;
        return 'M ' + arrondi(cx + externe * Math.cos(debut)) + ' ' +
            arrondi(cy + externe * Math.sin(debut)) +
            ' A ' + externe + ' ' + externe + ' 0 ' + grandArc + ' 1 ' +
            arrondi(cx + externe * Math.cos(fin)) + ' ' +
            arrondi(cy + externe * Math.sin(fin)) +
            ' L ' + arrondi(cx + interne * Math.cos(fin)) + ' ' +
            arrondi(cy + interne * Math.sin(fin)) +
            ' A ' + interne + ' ' + interne + ' 0 ' + grandArc + ' 0 ' +
            arrondi(cx + interne * Math.cos(debut)) + ' ' +
            arrondi(cy + interne * Math.sin(debut)) + ' Z';
    }

    function dessineCriticite(svg, liste) {
        var critiques = 0;
        var i;
        for (i = 0; i < liste.length; i += 1) {
            if (liste[i].critique) {
                critiques += 1;
            }
        }
        var normaux = liste.length - critiques;
        var cx = LARGEUR / 2;
        var cy = 156;
        var externe = 92;
        var interne = 58;
        var total = liste.length;
        var debut = -Math.PI / 2;
        var secteur, portion, couleur, libelle;

        if (critiques > 0 && normaux > 0) {
            portion = critiques / total;
            secteur = elementSVG('path', {
                d: arcDonut(cx, cy, externe, interne, debut, debut + portion * 2 * Math.PI),
                fill: COULEURS.critique, opacity: 0.92
            });
            infobulle(secteur, 'Critiques : ' + critiques + ' tâches');
            svg.appendChild(secteur);

            secteur = elementSVG('path', {
                d: arcDonut(cx, cy, externe, interne,
                    debut + portion * 2 * Math.PI, debut + 2 * Math.PI),
                fill: COULEURS.indigo, opacity: 0.9
            });
            infobulle(secteur, 'Non critiques : ' + normaux + ' tâches');
            svg.appendChild(secteur);
        } else {
            couleur = critiques > 0 ? COULEURS.critique : COULEURS.indigo;
            libelle = critiques > 0 ? 'Critiques' : 'Non critiques';
            secteur = elementSVG('circle', {
                cx: cx, cy: cy, r: (externe + interne) / 2,
                fill: 'none', stroke: couleur,
                'stroke-width': externe - interne, opacity: 0.92
            });
            infobulle(secteur, libelle + ' : ' + total + ' tâches');
            svg.appendChild(secteur);
        }

        svg.appendChild(elementSVG('text', {
            x: cx, y: cy - 2, 'text-anchor': 'middle',
            style: 'font-size:26px;font-weight:700;fill:#3730a3'
        }, String(total)));
        svg.appendChild(elementSVG('text', {
            x: cx, y: cy + 20, 'text-anchor': 'middle',
            'class': 'graphique-titre-axe'
        }, 'tâches au total'));

        legende(svg, [
            {
                libelle: 'Critiques : ' + critiques + ' (' +
                    format(Math.round(critiques / total * 100)) + '%)',
                couleur: COULEURS.critique
            },
            {
                libelle: 'Non critiques : ' + normaux + ' (' +
                    format(Math.round(normaux / total * 100)) + '%)',
                couleur: COULEURS.indigo
            }
        ], 316);
    }

    /* ------------------------------------------------------------------
       4. Nuage de points durée vs marge totale
       ------------------------------------------------------------------ */

    function dessineNuage(svg, liste) {
        var maxDuree = 0;
        var maxMarge = 0;
        var i;
        for (i = 0; i < liste.length; i += 1) {
            maxDuree = Math.max(maxDuree, liste[i].duree);
            maxMarge = Math.max(maxMarge, liste[i].marge);
        }
        var echelleX = jolieEchelle(maxDuree);
        var echelleY = jolieEchelle(maxMarge);
        var y0 = HAUTEUR - MARGE.bas;
        var hauteurUtile = y0 - MARGE.haut;
        var largeurUtile = LARGEUR - MARGE.gauche - MARGE.droite;
        var point, x, y;

        ligneBase(svg);
        axeVertical(svg, echelleY, 'Marge totale (jours)');
        graduationsHorizontales(svg, echelleX);
        titreAxeHorizontal(svg, 'Durée de la tâche (jours)');

        for (i = 0; i < liste.length; i += 1) {
            x = MARGE.gauche + (liste[i].duree / echelleX.max) * largeurUtile;
            y = y0 - (liste[i].marge / echelleY.max) * hauteurUtile;
            point = elementSVG('circle', {
                cx: arrondi(x), cy: arrondi(y), r: 6,
                fill: liste[i].critique ? COULEURS.critique : COULEURS.indigo,
                stroke: '#ffffff', 'stroke-width': 1.5, opacity: 0.9
            });
            infobulle(point, liste[i].nom + ' — durée : ' + format(liste[i].duree) +
                ' j, marge totale : ' + format(liste[i].marge) + ' j');
            svg.appendChild(point);
        }

        legende(svg, [
            { libelle: 'Critique', couleur: COULEURS.critique },
            { libelle: 'Non critique', couleur: COULEURS.indigo }
        ], 30);
    }

    /* ------------------------------------------------------------------
       5. Barres groupées ES / EF / LS / LF
       ------------------------------------------------------------------ */

    function dessineCalendrier(svg, liste) {
        var series = [
            { cle: 'ES', couleur: COULEURS.indigo },
            { cle: 'EF', couleur: COULEURS.cyan },
            { cle: 'LS', couleur: COULEURS.ambre },
            { cle: 'LF', couleur: COULEURS.violet }
        ];
        var maximum = 0;
        var i, j;
        for (i = 0; i < liste.length; i += 1) {
            for (j = 0; j < series.length; j += 1) {
                maximum = Math.max(maximum, liste[i][series[j].cle]);
            }
        }
        var echelle = jolieEchelle(maximum);
        var y0 = HAUTEUR - MARGE.bas;
        var hauteurUtile = y0 - MARGE.haut;
        var pasGroupe = (LARGEUR - MARGE.gauche - MARGE.droite) / Math.max(liste.length, 1);
        var largeurBarre = Math.min(11, (pasGroupe * 0.8) / series.length);
        var barre, x, hauteur;

        ligneBase(svg);
        axeVertical(svg, echelle, 'Jours depuis le début');
        categories(svg, liste);
        titreAxeHorizontal(svg, 'Tâches');

        for (i = 0; i < liste.length; i += 1) {
            for (j = 0; j < series.length; j += 1) {
                x = MARGE.gauche + i * pasGroupe +
                    (pasGroupe - largeurBarre * series.length) / 2 + j * largeurBarre;
                hauteur = (liste[i][series[j].cle] / echelle.max) * hauteurUtile;
                barre = elementSVG('rect', {
                    x: arrondi(x), y: arrondi(y0 - hauteur),
                    width: arrondi(Math.max(largeurBarre - 1, 1)),
                    height: arrondi(Math.max(hauteur, 0.8)),
                    rx: 2, fill: series[j].couleur, opacity: 0.9
                });
                infobulle(barre, liste[i].nom + ' — ' + series[j].cle + ' : ' +
                    format(liste[i][series[j].cle]) + ' j');
                svg.appendChild(barre);
            }
        }

        legende(svg, series.map(function (serie) {
            return { libelle: serie.cle, couleur: serie.couleur };
        }), 30);
    }

    var DESSINATEURS = {
        durees: dessineDurees,
        marges: dessineMarges,
        criticite: dessineCriticite,
        nuage: dessineNuage,
        calendrier: dessineCalendrier
    };

    /* ------------------------------------------------------------------
       API publique : window.Graphiques.generer(nom) / genererTous()
       ------------------------------------------------------------------ */

    function titreDe(cle) {
        for (var i = 0; i < GRAPHIQUES.length; i += 1) {
            if (GRAPHIQUES[i].cle === cle) {
                return GRAPHIQUES[i].titre;
            }
        }
        return cle;
    }

    function zoneResultats() {
        return document.getElementById('graphiques-resultats');
    }

    function carteDe(zone, cle) {
        var cartes = zone.querySelectorAll('[data-carte-graphique]');
        for (var i = 0; i < cartes.length; i += 1) {
            if (cartes[i].getAttribute('data-carte-graphique') === cle) {
                return cartes[i];
            }
        }
        return null;
    }

    function nombreCartes(zone) {
        return zone.querySelectorAll('[data-carte-graphique]').length;
    }

    function mettreAJourInvite(nombre) {
        var invite = document.getElementById('graphiques-invite');
        if (invite) {
            invite.hidden = nombre > 0;
        }
    }

    function generer(nom) {
        var cle = String(nom || '').toLowerCase();
        var dessinateur = DESSINATEURS[cle];
        if (!dessinateur) {
            return false;
        }
        var zone = zoneResultats();
        if (!zone) {
            return false;
        }

        var carte = carteDe(zone, cle);
        if (!carte) {
            carte = document.createElement('section');
            carte.setAttribute('class', 'graphique-carte');
            carte.setAttribute('data-carte-graphique', cle);
            carte.setAttribute('aria-label', titreDe(cle));

            var titre = document.createElement('h3');
            titre.setAttribute('class', 'graphique-carte-titre');
            titre.textContent = titreDe(cle);
            carte.appendChild(titre);

            var corps = document.createElement('div');
            corps.setAttribute('class', 'graphique-carte-corps');
            carte.appendChild(corps);
            zone.appendChild(carte);
        }

        var contenu = carte.querySelector('.graphique-carte-corps');
        vide(contenu);
        var donnees = enregistrements();

        if (!donnees.length) {
            var etatVide = document.createElement('div');
            etatVide.setAttribute('class', 'graphique-vide');
            etatVide.setAttribute('role', 'status');
            etatVide.textContent = 'Aucune tâche à représenter pour ce graphique.';
            contenu.appendChild(etatVide);
        } else {
            var svg = nouveauSvg(titreDe(cle), titreDe(cle) + ' — ' +
                donnees.length + ' tâches, valeurs issues du calcul PERT/CPM.');
            dessinateur(svg, donnees);
            contenu.appendChild(svg);
        }

        mettreAJourInvite(nombreCartes(zone));
        return true;
    }

    function genererTous() {
        var zone = zoneResultats();
        if (!zone) {
            return 0;
        }
        var compte = 0;
        var i, cle, boite, actif, carte;

        for (i = 0; i < GRAPHIQUES.length; i += 1) {
            cle = GRAPHIQUES[i].cle;
            boite = document.querySelector(
                '.graphiques-controles input[data-graphique="' + cle + '"]'
            );
            actif = boite ? !!boite.checked : true;
            if (actif) {
                if (generer(cle)) {
                    compte += 1;
                }
            } else {
                carte = carteDe(zone, cle);
                if (carte && carte.parentNode) {
                    carte.parentNode.removeChild(carte);
                }
            }
        }

        mettreAJourInvite(nombreCartes(zone));
        return compte;
    }

    function initialiser() {
        var cases = document.querySelectorAll(
            '.graphiques-controles input[data-graphique]'
        );
        var i;
        for (i = 0; i < cases.length; i += 1) {
            cases[i].addEventListener('change', genererTous);
        }
    }

    window.Graphiques = {
        generer: generer,
        genererTous: genererTous,
        noms: [
            'durees', 'marges', 'criticite', 'nuage', 'calendrier'
        ]
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialiser);
    } else {
        initialiser();
    }
}(window, document));
