"""Catalogue de projets types prêts à être importés dans un compte.

Chaque entrée de :data:`CATALOGUE` décrit un projet avec un graphe PERT au
format canonique de :func:`logic.pert.compute`. Les durées et dépendances sont
figées dans ce module : aucun calcul ni aucune simplification n'est appliqué
lors de l'import dans la base de données.

Les graphes sont vérifiés dès l'import du module. Toute incohérence (cycle,
prédécesseur inconnu, durée invalide) lève donc une :class:`ValueError` avant
même que l'application ne démarre.
"""

from __future__ import annotations

from extensions import db
from logic.pert import ValidationError, compute
from models import AuditLog, Dependency, Project, Task


def _t(description, duree, *depend_de):
    """Construit une tâche au format attendu par le moteur PERT."""
    return {
        "description": description,
        "duree": duree,
        "depend_de": list(depend_de),
    }


CATALOGUE = [
    {
        "id": "construction-maison",
        "titre": "Construction d'une maison individuelle",
        "categorie": "Construction",
        "description": "Du choix du terrain à la remise des clés : permis, gros œuvre, second œuvre et finitions.",
        "icone": "🏗️",
        "difficulte": "Difficile",
        "taches": {
            "A": _t("Étude de faisabilité et chiffrage du budget", 4),
            "B": _t("Acquisition du terrain et bornage", 6, "A"),
            "C": _t("Obtention du permis de construire", 14, "B", "D"),
            "D": _t("Conception des plans par l'architecte", 10, "A"),
            "E": _t("Appel d'offres et comparaison des devis", 5, "D"),
            "F": _t("Signature du contrat avec le constructeur", 3, "C", "E"),
            "G": _t("Préparation du terrain et terrassement", 4, "F"),
            "H": _t("Fondations et dallage", 6, "G"),
            "I": _t("Gros œuvre : murs, planchers et toiture", 15, "H"),
            "J": _t("Second œuvre : plomberie, électricité, isolation", 18, "I"),
            "K": _t("Finitions, peinture et poses diverses", 10, "J"),
            "L": _t("Aménagement paysager et clôture", 5, "I"),
            "M": _t("Jalon : réception et remise des clés", 0, "K", "L"),
        },
    },
    {
        "id": "application-mobile",
        "titre": "Lancement d'une application mobile",
        "categorie": "Logiciel",
        "description": "Cadrage produit, design, développement iOS/Android, tests et publication sur les stores.",
        "icone": "📱",
        "difficulte": "Difficile",
        "taches": {
            "A": _t("Cadrage produit, personas et parcours", 3),
            "B": _t("Choix techniques et architecture", 4, "A"),
            "C": _t("Maquettes UI/UX et prototype", 6, "A"),
            "D": _t("Design system et assets graphiques", 4, "C"),
            "E": _t("API back-end : authentification et données", 10, "B"),
            "F": _t("Développement des écrans iOS", 12, "D", "E"),
            "G": _t("Développement des écrans Android", 12, "D", "E"),
            "H": _t("Intégration des notifications push", 4, "E"),
            "I": _t("Tests fonctionnels multi-appareils", 6, "F", "G", "H"),
            "J": _t("Correction des anomalies bloquantes", 4, "I"),
            "K": _t("Jalon : validation en interne", 0, "J"),
            "L": _t("Préparation des stores : captures et métadonnées", 3, "K"),
            "M": _t("Publication App Store et Play Store", 2, "L"),
            "N": _t("Suivi des critiques et correctifs", 5, "M"),
        },
    },
    {
        "id": "mariage",
        "titre": "Organisation d'un mariage",
        "categorie": "Événement",
        "description": "De la réservation du lieu au jour J : traiteur, invitations, tenues, décoration et musique.",
        "icone": "💍",
        "difficulte": "Intermédiaire",
        "taches": {
            "A": _t("Choix de la date et du budget global", 3),
            "B": _t("Réservation du lieu de réception", 7, "A"),
            "C": _t("Liste des invités et adresses", 5, "A"),
            "D": _t("Traiteur, menu et boissons", 8, "B"),
            "E": _t("Photographe et vidéaste", 6, "B"),
            "F": _t("Décoration florale et mobilier", 7, "B"),
            "G": _t("Choix et essayage des tenues", 10, "A"),
            "H": _t("Musique live ou DJ", 6, "B"),
            "I": _t("Envoi des faire-part", 4, "B", "C"),
            "J": _t("Confirmations et planning final", 5, "D", "E", "F", "G", "H", "I"),
            "K": _t("Jalon : célébration du jour J", 0, "J"),
            "L": _t("Rangement, remerciements et albums", 4, "K"),
        },
    },
    {
        "id": "campagne-marketing",
        "titre": "Campagne marketing multicanal",
        "categorie": "Marketing",
        "description": "Définir les messages, produire les visuels et la vidéo, lancer les canaux puis analyser les KPI.",
        "icone": "📣",
        "difficulte": "Intermédiaire",
        "taches": {
            "A": _t("Étude de marché et personas", 4),
            "B": _t("Objectifs, budget et KPI", 2, "A"),
            "C": _t("Proposition de valeur et messages clés", 5, "B"),
            "D": _t("Identité visuelle de la campagne", 6, "C"),
            "E": _t("Calendrier éditorial multicanal", 4, "B", "C"),
            "F": _t("Production et montage de la vidéo", 8, "D"),
            "G": _t("Création des visuels sociaux", 5, "D"),
            "H": _t("Landing page et formulaire de capture", 7, "D", "E"),
            "I": _t("Configuration CRM et suivi des conversions", 3, "B"),
            "J": _t("Lancement des campagnes publicitaires", 3, "F", "G", "H", "I"),
            "K": _t("Jalon : pic de diffusion", 0, "J"),
            "L": _t("Analyse des résultats et attribution", 5, "K"),
            "M": _t("Rapport final et recommandations", 3, "L"),
        },
    },
    {
        "id": "these-recherche",
        "titre": "Thèse de recherche doctorale",
        "categorie": "Recherche",
        "description": "Revue de littérature, protocole, collecte et analyse des données, rédaction puis soutenance.",
        "icone": "🎓",
        "difficulte": "Difficile",
        "taches": {
            "A": _t("Cadrage du sujet et question de recherche", 5),
            "B": _t("Revue systématique de la littérature", 30, "A"),
            "C": _t("Construction du cadre théorique", 25, "B"),
            "D": _t("Méthodologie et protocole d'expérience", 20, "C"),
            "E": _t("Autorisations éthiques et accès aux données", 30, "D"),
            "F": _t("Collecte des données sur le terrain", 180, "E"),
            "G": _t("Saisie et nettoyage des données", 20, "F"),
            "H": _t("Analyse statistique et interprétation", 60, "G"),
            "I": _t("Rédaction de l'introduction et de l'état de l'art", 45, "C"),
            "J": _t("Rédaction des chapitres de résultats", 75, "H", "I"),
            "K": _t("Jalon : dépôt du manuscrit", 0, "J"),
            "L": _t("Soutenance et corrections finales", 30, "K"),
            "M": _t("Diffusion : article et archives ouvertes", 45, "L"),
        },
    },
    {
        "id": "ferme-maraichere",
        "titre": "Mise en place d'une ferme maraîchère",
        "categorie": "Agriculture",
        "description": "Diagnostic du sol, irrigation, semis, entretien des cultures, récolte et circuit de vente.",
        "icone": "🥕",
        "difficulte": "Intermédiaire",
        "taches": {
            "A": _t("Diagnostic du terrain et analyse du sol", 4),
            "B": _t("Choix des cultures et calendrier cultural", 5, "A"),
            "C": _t("Commande des semences et du matériel", 6, "B"),
            "D": _t("Installation du réseau d'irrigation", 8, "A"),
            "E": _t("Préparation des parcelles et apport de compost", 7, "A", "B"),
            "F": _t("Semis en pépinière", 5, "C", "E"),
            "G": _t("Repiquage des plants", 4, "D", "F"),
            "H": _t("Entretien, arrosage et désherbage", 12, "G"),
            "I": _t("Récolte, tri et conditionnement", 8, "H"),
            "J": _t("Jalon : première livraison", 0, "I"),
            "K": _t("Mise en place du circuit de vente", 6, "B"),
            "L": _t("Aménagement du point de vente à la ferme", 7, "K"),
            "M": _t("Stockage et conservation des excédents", 5, "I"),
        },
    },
    {
        "id": "demenagement-entreprise",
        "titre": "Déménagement d'une entreprise",
        "categorie": "Entreprise",
        "description": "Nouveau site, aménagement, transfert informatique, déménagement physique et reprise d'activité.",
        "icone": "📦",
        "difficulte": "Difficile",
        "taches": {
            "A": _t("Inventaire des biens et audit des besoins", 4),
            "B": _t("Visite et choix du nouveau site", 8, "A"),
            "C": _t("Négociation du bail et signature", 10, "B"),
            "D": _t("Plan d'aménagement des espaces", 7, "B"),
            "E": _t("Appel d'offres et choix du déménageur", 5, "C"),
            "F": _t("Commande du mobilier et du matériel", 6, "D"),
            "G": _t("Préparation des postes informatiques", 8, "D"),
            "H": _t("Transfert des données, réseau et téléphonie", 6, "F", "G"),
            "I": _t("Emballage et étiquetage des cartons", 5, "E"),
            "J": _t("Déménagement physique sur une journée", 3, "C", "H", "I"),
            "K": _t("Réinstallation et tests des postes", 4, "J"),
            "L": _t("Jalon : reprise d'activité", 0, "K"),
            "M": _t("Information des clients et partenaires", 3, "C"),
        },
    },
    {
        "id": "formation-en-ligne",
        "titre": "Création d'une formation en ligne",
        "categorie": "Formation",
        "description": "Ingénierie pédagogique, tournage, supports, paramétrage LMS, beta-test puis lancement.",
        "icone": "🧑‍🏫",
        "difficulte": "Intermédiaire",
        "taches": {
            "A": _t("Analyse des besoins des apprenants", 4),
            "B": _t("Définition des objectifs pédagogiques", 3, "A"),
            "C": _t("Scénario et découpage des modules", 6, "B"),
            "D": _t("Écriture des scripts vidéo", 6, "C"),
            "E": _t("Tournage des séquences", 9, "D"),
            "F": _t("Montage, musique et sous-titres", 8, "E"),
            "G": _t("Conception des supports PDF et exercices", 5, "C"),
            "H": _t("Paramétrage de la plateforme LMS", 5, "B"),
            "I": _t("Intégration des modules dans le LMS", 6, "F", "G", "H"),
            "J": _t("Création des quiz et évaluations", 4, "I"),
            "K": _t("Jalon : beta-test", 0, "J"),
            "L": _t("Tests utilisateurs et ajustements", 5, "K"),
            "M": _t("Lancement de la formation", 2, "L"),
            "N": _t("Certification et suivi des apprenants", 6, "M"),
        },
    },
    {
        "id": "tournage-film",
        "titre": "Tournage d'un court métrage",
        "categorie": "Audiovisuel",
        "description": "Scénario, financement, casting, repérages, tournage, montage, sound design et distribution.",
        "icone": "🎥",
        "difficulte": "Difficile",
        "taches": {
            "A": _t("Écriture du scénario", 12),
            "B": _t("Découpage technique et storyboard", 6, "A"),
            "C": _t("Recherche de financements et coproducteurs", 10, "A"),
            "D": _t("Casting et répétitions", 8, "B"),
            "E": _t("Repérage des lieux et autorisations", 6, "B"),
            "F": _t("Décors, costumes et accessoires", 9, "D", "E"),
            "G": _t("Planning de tournage et équipe", 5, "C", "F"),
            "H": _t("Tournage des plans", 14, "G"),
            "I": _t("Montage image", 12, "H"),
            "J": _t("Composition musicale et sound design", 8, "I"),
            "K": _t("Étalonnage et effets visuels", 7, "I"),
            "L": _t("Mixage final et livraison DCP", 5, "J", "K"),
            "M": _t("Jalon : première séance", 0, "L"),
            "N": _t("Festivals et distribution", 10, "M"),
        },
    },
    {
        "id": "installation-medicale",
        "titre": "Ouverture d'un cabinet médical",
        "categorie": "Santé",
        "description": "Travaux, achat d'équipements, conformité, formation, calibration et ouverture aux patients.",
        "icone": "🏥",
        "difficulte": "Difficile",
        "taches": {
            "A": _t("Analyse des besoins et flux patients", 5),
            "B": _t("Choix des équipements médicaux", 7, "A"),
            "C": _t("Appel d'offres et commandes", 9, "B"),
            "D": _t("Travaux d'aménagement et rénovation", 12, "A"),
            "E": _t("Mise en conformité hygiène et sécurité", 8, "A"),
            "F": _t("Installation et raccordement des appareils", 6, "C", "D"),
            "G": _t("Formation du personnel soignant", 5, "F"),
            "H": _t("Tests, calibration et contrôles", 4, "F"),
            "I": _t("Validation des procédures de soin", 5, "E", "G", "H"),
            "J": _t("Jalon : ouverture au public", 0, "I"),
            "K": _t("Constitution du dossier d'agrément", 8, "I"),
            "L": _t("Suivi qualité après ouverture", 6, "J", "K"),
        },
    },
    {
        "id": "festival",
        "titre": "Organisation d'un festival culturel",
        "categorie": "Culture",
        "description": "Programmation artistique, partenariats, communication, billetterie, logistique et sécurité.",
        "icone": "🎪",
        "difficulte": "Difficile",
        "taches": {
            "A": _t("Comité d'organisation et budget prévisionnel", 4),
            "B": _t("Choix du thème et de la programmation", 6, "A"),
            "C": _t("Réservation du site et des services", 8, "A"),
            "D": _t("Appel à artistes et candidatures", 7, "B"),
            "E": _t("Sélection et contractualisation des artistes", 5, "D"),
            "F": _t("Recherche de partenaires et subventions", 8, "C"),
            "G": _t("Communication, affiches et réseaux", 6, "E", "F"),
            "H": _t("Mise en place de la billetterie", 5, "F"),
            "I": _t("Logistique scène, son et lumière", 9, "C", "E"),
            "J": _t("Recrutement et formation des bénévoles", 5, "I"),
            "K": _t("Jalon : ouverture du festival", 0, "G", "H", "J"),
            "L": _t("Accueil du public et sécurité", 3, "K"),
            "M": _t("Bilan financier et démontage", 4, "L"),
        },
    },
    {
        "id": "refonte-site-web",
        "titre": "Refonte d'un site web vitrine",
        "categorie": "Web",
        "description": "Audit, architecture de l'information, design, développement, migration SEO et mise en ligne.",
        "icone": "🌐",
        "difficulte": "Intermédiaire",
        "taches": {
            "A": _t("Audit du site existant et analytics", 4),
            "B": _t("Benchmark concurrentiel et personas", 3, "A"),
            "C": _t("Architecture de l'information et arborescence", 5, "B"),
            "D": _t("Maquettes et design system", 7, "C"),
            "E": _t("Rédaction et migration des contenus", 8, "C"),
            "F": _t("Développement front-end responsive", 10, "D"),
            "G": _t("Mise en place du CMS et des API", 9, "C"),
            "H": _t("Intégration des contenus et des modules", 6, "E", "F", "G"),
            "I": _t("Tests navigateurs et accessibilité", 5, "H"),
            "J": _t("Plan de redirections et migration SEO", 4, "H"),
            "K": _t("Jalon : mise en ligne", 0, "I", "J"),
            "L": _t("Formation des équipes au CMS", 3, "K"),
            "M": _t("Monitoring et corrections post-lancement", 6, "K"),
        },
    },
]


def _valider(projet):
    """Calcule le graphe d'un projet et convertit toute erreur en ValueError."""
    try:
        return compute(projet["taches"])
    except ValidationError as exc:
        raise ValueError(
            f"Graphe invalide pour le projet « {projet.get('titre', projet.get('id'))} » : {exc}"
        ) from exc


def valider_catalogue():
    """Vérifie tous les graphes au chargement du module."""
    for projet in CATALOGUE:
        _valider(projet)
    return True


valider_catalogue()


def get_projet(projet_id):
    """Retourne le dictionnaire d'un projet, ou ``None`` s'il est inconnu."""
    for projet in CATALOGUE:
        if str(projet.get("id")) == str(projet_id):
            _valider(projet)
            return projet
    return None


def optimiser(projet_id, user_id):
    """Crée le projet, ses tâches et ses dépendances dans le compte *user_id*.

    Les durées du catalogue sont copiées telles quelles : cette fonction ne
    réalise aucune optimisation numérique. Le calcul PERT reste disponible
    ensuite dans la page existante ``projects.optimisation``.
    """
    modele = get_projet(projet_id)
    if modele is None:
        raise ValueError(f"Projet du catalogue introuvable : {projet_id!r}")

    planning = _valider(modele)
    titre = modele["titre"]

    projet = Project(
        user_id=user_id,
        name=titre,
        description=(
            f"{modele['description']} (catalogue : {modele['categorie']}, "
            f"importé sans modification des durées)"
        ),
        status='actif',
    )
    db.session.add(projet)
    db.session.flush()

    taches = {}
    for code, infos in modele["taches"].items():
        tache = Task(
            project_id=projet.id,
            code=code,
            description=infos.get("description", ""),
            duree=infos["duree"],
        )
        db.session.add(tache)
        db.session.flush()
        taches[code] = tache

    for code, infos in modele["taches"].items():
        for predecesseur in dict.fromkeys(infos.get("depend_de") or []):
            if predecesseur not in taches:
                raise ValueError(
                    f"Prédécesseur inconnu « {predecesseur} » pour {code} "
                    f"dans le projet « {titre} »"
                )
            db.session.add(
                Dependency(
                    successor_id=taches[code].id,
                    predecessor_id=taches[predecesseur].id,
                    type_relation='FS',
                )
            )

    db.session.commit()

    details = (
        f"Catalogue « {modele['id']} » importé : {titre} — "
        f"{len(taches)} tâches, durée PERT de {planning.duree_totale} jour(s)."
    )
    try:
        db.session.add(
            AuditLog(user_id=user_id, action='CATALOGUE_OPTIMISE', details=details)
        )
        db.session.commit()
    except Exception:
        # Le journal est un plus : un schéma d'audit indisponible ne doit pas
        # remettre en cause le projet déjà enregistré.
        db.session.rollback()

    return projet
