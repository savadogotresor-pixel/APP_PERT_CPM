import base64
import binascii
import io
import logging
import os
import sys
from datetime import date, timedelta

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from openpyxl import Workbook
from openpyxl.styles import PatternFill
from openpyxl.drawing.image import Image as XLImage

from config import Config  # noqa: F401 - rend la configuration explicite à l'import
from extensions import db, login_manager, csrf
from logic.calcul import generer_conseils
from logic.data import taches, parser_taches
from logic.pert import compute, ValidationError
from models import User, init_db
from routes_auth import auth_bp
from routes_projects import projects_bp

app = Flask(__name__)
# APP_SETTINGS permet de sélectionner la configuration (ex. "config.TestConfig").
app.config.from_object(os.environ.get('APP_SETTINGS', 'config.Config'))
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024

# Render termine TLS en amont : sans ProxyFix, request.is_secure reste faux et
# les redirections/cookies sécurisés seraient générés en HTTP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Journalisation sur stdout (capturé par Render/gunicorn), sans trace technique
# renvoyée au visiteur. Le niveau DEBUG reste limité au développement local.
for _handler in list(app.logger.handlers):
    app.logger.removeHandler(_handler)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
app.logger.addHandler(_handler)
app.logger.setLevel(logging.DEBUG if os.getenv('FLASK_DEBUG') == '1' else logging.INFO)

db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Veuillez vous connecter pour accéder à cette page.'
csrf.init_app(app)

app.register_blueprint(auth_bp)
app.register_blueprint(projects_bp)

try:
    from routes_admin import admin_bp
except ImportError:
    # Le blueprint admin peut ne pas encore être livré : le reste de l'app
    # doit continuer à démarrer (et les tests existants à passer).
    app.logger.info("routes_admin indisponible : blueprint /admin non enregistré.")
else:
    app.register_blueprint(admin_bp)


@app.after_request
def ajouter_en_tetes_securite(response):
    """En-têtes HTTP de défense en profondeur (§7/§11 du cahier)."""
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    if request.is_secure:
        response.headers.setdefault(
            'Strict-Transport-Security', 'max-age=31536000; includeSubDomains'
        )
    # CSP permissive pour les CDN et scripts inline déjà utilisés vis.js,
    # frappe-gantt, html2canvas, jsPDF, Google Fonts.
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'none'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com "
        "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
        "https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "img-src 'self' data: https: blob:; "
        "connect-src 'self'; worker-src 'self' blob:"
    )
    return response


@login_manager.user_loader
def charger_utilisateur(user_id):
    """Recharge l'utilisateur connecté depuis la session (Flask-Login)."""
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


try:
    init_db(app)
except Exception:
    # Une base indisponible ne doit pas empêcher le démarrage de l'application.
    app.logger.exception("Impossible d'initialiser la base de données au démarrage.")

PREFIXE_PNG = "data:image/png;base64,"
PREFIXES_FORMULE = ("=", "+", "-", "@")
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def taches_vers_texte(taches):
    """Sérialise un graphe de tâches au format attendu par ``parser_taches``."""
    lignes = []
    for nom, infos in taches.items():
        # Un point-virgule ou un saut de ligne dans la description casserait
        # la syntaxe `nom;description;duree;pred1,pred2` au réassemblage.
        description = str(infos.get("description", ""))
        description = description.replace("\r", " ").replace("\n", " ").replace(";", ",")
        dependances = ",".join(str(p) for p in (infos.get("depend_de") or []) if str(p).strip())
        lignes.append(f"{nom};{description};{infos.get('duree', 0)};{dependances}")
    return "\n".join(lignes)


def messages_erreurs(exception):
    """Découpe les messages agrégés par ``ValidationError`` en lignes propres."""
    return [ligne.strip() for ligne in str(exception).split("\n") if ligne.strip()]


def preparer_graphe(taches, resultats):
    nodes = []
    for nom, infos in taches.items():
        couleur = "#ff6b6b" if resultats[nom]["critique"] else "#97c2fc"
        nodes.append({
            "id": nom,
            "label": f"{nom}\n({infos['duree']}j)",
            "color": couleur
        })
    edges = []
    for nom, infos in taches.items():
        for dep in infos["depend_de"]:
            edges.append({"from": dep, "to": nom, "arrows": "to"})
    return nodes, edges


def preparer_gantt(taches, resultats, date_debut=None):
    if date_debut is None:
        date_debut = date.today()
    taches_gantt = []
    for nom, infos in taches.items():
        r = resultats[nom]
        debut = date_debut + timedelta(days=r["ES"])
        fin = date_debut + timedelta(days=r["EF"])
        taches_gantt.append({
            "id": nom,
            "name": f"{nom} - {infos['description']}",
            "start": debut.isoformat(),
            "end": fin.isoformat(),
            "progress": 0,
            "dependencies": ",".join(infos["depend_de"]),
            "custom_class": "critique" if r["critique"] else ""
        })
    return taches_gantt


def neutraliser_formules(feuille, numero_ligne):
    """Force en texte (data_type 's') toute cellule pouvant être une formule."""
    for cellule in feuille[numero_ligne]:
        valeur = cellule.value
        if isinstance(valeur, str) and valeur.startswith(PREFIXES_FORMULE):
            cellule.data_type = "s"


def decoder_image_png(data_url, nom_champ):
    """Valide une data URL PNG et retourne un flux binaire prêt pour XLImage."""
    if not isinstance(data_url, str) or not data_url.startswith(PREFIXE_PNG):
        raise ValueError(f"Le champ '{nom_champ}' doit être une data URL PNG.")
    donnees = data_url[len(PREFIXE_PNG):]
    try:
        octets = base64.b64decode(donnees, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Le champ '{nom_champ}' contient du base64 invalide.") from exc
    if not octets:
        raise ValueError(f"Le champ '{nom_champ}' contient une image vide.")
    return io.BytesIO(octets)


@app.route('/')
def accueil():
    return render_template('accueil.html')


@app.route('/health')
def health():
    return jsonify({"status": "ok"})


@app.route('/demo')
def demo():
    r = compute(taches)
    chemin_critique = r.chemins_critiques[0] if r.chemins_critiques else []
    nodes, edges = preparer_graphe(taches, r.resultats)
    taches_gantt = preparer_gantt(taches, r.resultats)
    return render_template(
        'resultats.html',
        taches=taches,
        resultats=r.resultats,
        chemin_critique=chemin_critique,
        chemins_critiques=r.chemins_critiques,
        duree_totale=r.duree_totale,
        nodes=nodes,
        edges=edges,
        taches_gantt=taches_gantt,
        date_debut=None,
        conseils=[],
        taches_texte=taches_vers_texte(taches)
    )


@app.route('/saisie')
def saisie():
    return render_template('saisie.html')


@app.route('/resultats_custom', methods=['POST'])
def resultats_custom():
    texte = request.form.get('taches', '')
    date_debut_str = request.form.get('date_debut', '').strip()
    date_fin_str = request.form.get('date_fin_cible', '').strip()

    # Conversion date_debut
    try:
        date_debut_obj = date.fromisoformat(date_debut_str) if date_debut_str else date.today()
    except ValueError:
        date_debut_obj = date.today()

    taches_custom, erreurs = parser_taches(texte)

    if not taches_custom and not erreurs:
        erreurs.append("Aucune tâche saisie.")

    r = None
    if not erreurs:
        try:
            r = compute(taches_custom)
        except ValidationError as exc:
            erreurs.extend(messages_erreurs(exc))

    if erreurs:
        return render_template('erreur.html', erreurs=erreurs)

    resultats = r.resultats
    chemin_critique = r.chemins_critiques[0] if r.chemins_critiques else []

    nodes, edges = preparer_graphe(taches_custom, resultats)
    taches_gantt = preparer_gantt(taches_custom, resultats, date_debut_obj)

    # Date de fin affichage JJ/MM/AAAA + objet date pour le calcul des conseils
    date_fin_affichage = None
    date_fin_obj = None
    if date_fin_str:
        try:
            date_fin_obj = date.fromisoformat(date_fin_str)
            date_fin_affichage = date_fin_obj.strftime('%d/%m/%Y')
        except ValueError:
            pass

    conseils = generer_conseils(
        taches_custom, resultats, chemin_critique, r.duree_totale,
        date_debut=date_debut_obj, date_fin_cible=date_fin_obj
    )

    return render_template(
        'resultats.html',
        taches=taches_custom,
        resultats=resultats,
        chemin_critique=chemin_critique,
        chemins_critiques=r.chemins_critiques,
        duree_totale=r.duree_totale,
        nodes=nodes,
        edges=edges,
        taches_gantt=taches_gantt,
        date_debut=date_debut_str,
        date_fin=date_fin_affichage,
        conseils=conseils,
        taches_texte=texte
    )


@app.route('/export_excel', methods=['POST'])
def export_excel():
    """Export sécurisé : tous les chiffres sont recalculés côté serveur."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"erreur": "Corps JSON invalide ou manquant."}), 400

    taches_texte = payload.get("taches_texte")
    date_debut_str = payload.get("date_debut", "")
    image_graphe = payload.get("image_graphe")
    image_gantt = payload.get("image_gantt")

    if not isinstance(taches_texte, str) or not taches_texte.strip():
        return jsonify({"erreur": "Le champ 'taches_texte' doit être une chaîne non vide."}), 400
    if not isinstance(date_debut_str, str):
        return jsonify({"erreur": "Le champ 'date_debut' doit être une chaîne."}), 400
    if date_debut_str:
        try:
            date.fromisoformat(date_debut_str)
        except ValueError:
            return jsonify({
                "erreur": "Le champ 'date_debut' doit être une date ISO (AAAA-MM-JJ) ou vide."
            }), 400
    for nom_champ, valeur in (("image_graphe", image_graphe), ("image_gantt", image_gantt)):
        if not isinstance(valeur, str) or not valeur.startswith(PREFIXE_PNG):
            return jsonify({
                "erreur": f"Le champ '{nom_champ}' doit être une data URL PNG."
            }), 400

    taches_export, erreurs = parser_taches(taches_texte)
    if not taches_export and not erreurs:
        erreurs.append("Aucune tâche saisie.")
    if erreurs:
        return jsonify({
            "erreur": "La saisie des tâches est invalide.",
            "details": erreurs
        }), 400

    try:
        r = compute(taches_export)
    except ValidationError as exc:
        return jsonify({
            "erreur": "Le graphe de tâches est invalide.",
            "details": messages_erreurs(exc)
        }), 400

    try:
        flux_graphe = decoder_image_png(image_graphe, "image_graphe")
        flux_gantt = decoder_image_png(image_gantt, "image_gantt")
    except ValueError as exc:
        return jsonify({"erreur": str(exc)}), 400

    wb = Workbook()
    ws = wb.active
    ws.title = "Résultats"
    ws.append([
        "Tâche", "Description", "Durée", "ES", "EF", "LS", "LF",
        "Marge totale", "Marge libre", "Critique"
    ])
    neutraliser_formules(ws, ws.max_row)
    rouge = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")

    for nom, resultat in r.resultats.items():
        infos = taches_export[nom]
        ws.append([
            nom, infos["description"], infos["duree"],
            resultat["ES"], resultat["EF"], resultat["LS"], resultat["LF"],
            resultat["marge_totale"], resultat["marge_libre"],
            "OUI" if resultat["critique"] else "Non"
        ])
        ligne = ws.max_row
        neutraliser_formules(ws, ligne)
        if resultat["critique"]:
            for cellule in ws[ligne]:
                cellule.fill = rouge

    ws.append([])
    ws.append([f"Durée totale du projet : {r.duree_totale} jours"])
    neutraliser_formules(ws, ws.max_row)
    chemins = " | ".join(" → ".join(chemin) for chemin in r.chemins_critiques)
    ws.append([f"Chemins critiques : {chemins}"])
    neutraliser_formules(ws, ws.max_row)

    try:
        ws_graphe = wb.create_sheet("Graphe PERT")
        ws_graphe.add_image(XLImage(flux_graphe), "A1")
        ws_gantt = wb.create_sheet("Diagramme Gantt")
        ws_gantt.add_image(XLImage(flux_gantt), "A1")
    except Exception:
        return jsonify({"erreur": "Les images PNG fournies sont illisibles."}), 400

    try:
        sortie = io.BytesIO()
        wb.save(sortie)
    except Exception:
        return jsonify({"erreur": "Impossible de générer le classeur Excel."}), 400
    sortie.seek(0)
    return send_file(
        sortie,
        mimetype=MIME_XLSX,
        as_attachment=True,
        download_name="resultats_pert_cpm.xlsx"
    )


@app.errorhandler(413)
def requete_trop_volumineuse(erreur):
    # MAX_CONTENT_LENGTH : un payload trop gros est un payload invalide.
    return jsonify({
        "erreur": "La requête dépasse la taille maximale autorisée (8 Mo)."
    }), 400


@app.errorhandler(404)
def page_introuvable(erreur):
    return render_template('erreur.html', erreurs=["La page demandée n'existe pas (404)."]), 404


@app.errorhandler(500)
def erreur_interne(erreur):
    return render_template(
        'erreur.html',
        erreurs=["Une erreur interne est survenue (500). Veuillez réessayer plus tard."]
    ), 500


@app.errorhandler(Exception)
def erreur_imprevue(exception):
    """Journalise la trace côté serveur, renvoie une page générique au client."""
    if isinstance(exception, HTTPException):
        return exception
    if app.config.get('TESTING'):
        raise exception
    app.logger.exception("Erreur non gérée : %s", exception)
    return render_template(
        'erreur.html',
        erreurs=["Une erreur interne est survenue (500). Veuillez réessayer plus tard."]
    ), 500


# Les pages historiques (/saisie, /resultats_custom, /export_excel) n'embarquent
# pas encore de jeton CSRF : elles restent exemptées pour préserver leur comportement.
csrf.exempt(app.view_functions['resultats_custom'])
csrf.exempt(app.view_functions['export_excel'])
# Marqueur d'introspection (certaines versions de Flask-WTF ne le posent pas).
app.view_functions['resultats_custom'].csrf_exempt = True
app.view_functions['export_excel'].csrf_exempt = True


if __name__ == '__main__':
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
