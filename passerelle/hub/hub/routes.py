"""Pages et API du hub."""

from __future__ import annotations

import json
import logging
import time
from itertools import groupby

import requests
import urllib3
from flask import (Blueprint, abort, current_app, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import demandes as D
from .authentik import Authentik, AuthentikErreur
from .botpanel import signature_valide
from .db import get_db, journaliser
from .identite import admin, connecte, visiteur
from .sites import CHEMINS_PUBLICS_DEFAUT, ICONES, Site, Synchro, est_ip_privee, lire_formulaire, site_visible

log = logging.getLogger(__name__)
bp = Blueprint("hub", __name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def cfg():
    return current_app.config["HUB"]


def ak() -> Authentik:
    return Authentik(cfg().AUTHENTIK_URL_INTERNE, cfg().AUTHENTIK_TOKEN)


def synchro() -> Synchro:
    c = cfg()
    return Synchro(ak(), c.DOMAINE, c.GROUPE_ADMINS, c.DUREE_SESSION)


def ip_client() -> str:
    for h in ("Cf-Connecting-Ip", "X-Real-Ip"):
        if request.headers.get(h):
            return request.headers[h].strip()[:64]
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() or request.remote_addr or "")[:64]


def json_err(message: str, statut: int = 400, **extra):
    return jsonify({"ok": False, "message": message, **extra}), statut


@bp.app_context_processor
def contexte():
    v = visiteur() if request.endpoint and not request.path.startswith("/static") else None
    n = 0
    if v and v.admin:
        try:
            n = D.compter_en_attente()
        except Exception:  # noqa: BLE001 — un compteur ne doit jamais casser une page
            n = 0
    c = cfg()
    return {"v": v, "cfg": c, "nb_demandes": n, "version": current_app.config["VERSION"],
            "domaine": c.DOMAINE, "raccourcis": _raccourcis(v) if v else []}


def _raccourcis(v) -> list[dict]:
    """Ce que propose la recherche rapide (Ctrl K) : les pages, puis les sites."""
    r = [{"titre": "Accueil", "sous": "Tous les sites", "icone": "home", "href": "/"}]
    if v.admin:
        r += [
            {"titre": "Ajouter un site", "sous": "Nom, sous-domaine, IP:port", "icone": "plus",
             "mots": "nouveau creer site", "href": "/admin/sites/nouveau"},
            {"titre": "Sites", "sous": "Gérer les sites", "icone": "grid", "href": "/admin/sites"},
            {"titre": "Demandes d'accès", "sous": "Accepter ou refuser", "icone": "mail",
             "mots": "inscription", "href": "/admin/demandes"},
            {"titre": "Membres", "sous": "Comptes et blocages", "icone": "users", "href": "/admin/membres"},
            {"titre": "Authentik", "sous": "Utilisateurs, groupes", "icone": "shield",
             "href": cfg().lien_admin_authentik},
        ]
    r += [
        {"titre": "Apparence", "sous": "Style, couleur, thème", "icone": "palette",
         "mots": "couleur theme sombre clair verre", "href": "/reglages#apparence"},
        {"titre": "Mon compte", "sous": "Mot de passe, Google", "icone": "user", "href": cfg().lien_mon_compte},
        {"titre": "Se déconnecter", "icone": "logout", "href": "/deconnexion"},
    ]
    try:
        for s in get_db().execute("SELECT nom, lien, icone, acces, description FROM sites ORDER BY nom"):
            if s["acces"] == "membres" or v.admin:
                r.append({"groupe": "Sites", "titre": s["nom"], "sous": s["description"] or s["lien"],
                          "icone": s["icone"], "href": s["lien"]})
    except Exception:  # noqa: BLE001
        pass
    return r


@bp.before_app_request
def garde_api():
    """Les appels qui modifient quelque chose viennent du hub lui-même (pas d'un autre site)."""
    if request.path.startswith("/api/") and not request.path.startswith("/api/botpanel/") \
            and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-Hub") != "1":
            abort(403)


# ================================================================ pages publiques
@bp.get("/sante")
def sante():
    return jsonify({"ok": True})


@bp.get("/manifest.webmanifest")
def manifeste():
    return send_from_directory(current_app.static_folder, "manifest.webmanifest",
                               mimetype="application/manifest+json")


@bp.get("/sw.js")
def service_worker():
    resp = send_from_directory(current_app.static_folder, "sw.js", mimetype="text/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def _serialiseur():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="demande")


@bp.route("/demande", methods=["GET", "POST"])
def demande():
    depuis_google = request.args.get("depuis") == "google"
    if request.method == "GET":
        return render_template("demande.html", donnees={}, erreurs={},
                               jeton=_serialiseur().dumps("ok"), depuis_google=depuis_google)

    donnees, erreurs = D.valider(request.form)
    # Anti-robots : champ piège invisible, et formulaire rempli en plus de 2 secondes.
    if request.form.get("site_web"):
        return render_template("demande_etat.html", etat="nouvelle", demande=None)
    try:
        _, ts = _serialiseur().loads(request.form.get("jeton", ""), max_age=86400, return_timestamp=True)
        if time.time() - ts.timestamp() < 2:
            erreurs["general"] = "Doucement ! Réessaie dans un instant."
    except BadSignature:
        erreurs["general"] = "Le formulaire a expiré : recharge la page et réessaie."
    if not erreurs and D.trop_de_demandes(ip_client()):
        erreurs["general"] = "Trop de demandes depuis ta connexion. Réessaie dans une heure."
    if erreurs:
        return render_template("demande.html", donnees=donnees, erreurs=erreurs,
                               jeton=_serialiseur().dumps("ok"), depuis_google=False), 400
    etat, d = D.soumettre(donnees, ip_client())
    return render_template("demande_etat.html", etat=etat, demande=d)


@bp.get("/demande/suivi/<jeton>")
def demande_suivi(jeton):
    d = D.par_jeton(jeton)
    if not d:
        abort(404)
    return render_template("demande_etat.html", etat=d["statut"], demande=d, suivi=True)


@bp.post("/api/botpanel/decision")
def botpanel_decision():
    """Bouton « Accepter » / « Refuser » cliqué dans Discord (bouton webhook de BotPanel)."""
    c = cfg()
    corps = request.get_data(cache=True)
    if not signature_valide(corps, request.headers.get("X-BotPanel-Timestamp", ""),
                            request.headers.get("X-BotPanel-Signature", ""), c.BOTPANEL_WEBHOOK_SECRET):
        return json_err("Signature BotPanel invalide.", 401)
    try:
        charge = json.loads(corps or b"{}")
    except ValueError:
        return json_err("JSON invalide.")
    data = charge.get("data") or {}
    variables = charge.get("vars") or {}
    action = str(data.get("action") or "").strip().lower()
    try:
        demande_id = int(data.get("id") or variables.get("id") or 0)
    except (TypeError, ValueError):
        demande_id = 0
    qui = "Discord : " + str((charge.get("user") or {}).get("name") or "?")
    if not demande_id or action not in ("accepter", "refuser"):
        return jsonify({"ok": False, "message": "❌ Demande ou action inconnue (data : action + id)."}), 400
    res = D.accepter(demande_id, qui) if action == "accepter" else D.refuser(demande_id, qui)
    prefixe = "✅ " if res.ok else "❌ "
    return jsonify({"ok": res.ok, "message": prefixe + res.message})


# ================================================================ pages connectées
@bp.get("/")
@connecte
def accueil():
    v = visiteur()
    lignes = get_db().execute("SELECT * FROM sites ORDER BY categorie = '', categorie, ordre, nom").fetchall()
    sites = [s for s in lignes if site_visible(s, v)]
    groupes = [(cat, list(items)) for cat, items in groupby(sites, key=lambda s: s["categorie"])]
    return render_template("accueil.html", groupes=groupes, nb_sites=len(sites), est_admin=v.admin, page="accueil")


@bp.get("/reglages")
@connecte
def reglages():
    return render_template("reglages.html", page="reglages")


@bp.get("/admin/sites")
@admin
def admin_sites():
    lignes = get_db().execute("SELECT * FROM sites ORDER BY categorie = '', categorie, ordre, nom").fetchall()
    etat, erreur = {}, ""
    try:
        etat = synchro().etat()
    except AuthentikErreur as exc:
        erreur = str(exc)
    return render_template("admin_sites.html", sites=lignes, etat=etat, erreur_ak=erreur, page="sites")


@bp.get("/admin/sites/nouveau")
@admin
def site_nouveau():
    return render_template("site_form.html", site=Site(), site_id=None, icones=ICONES,
                           categories=_categories(), page="sites")


@bp.get("/admin/sites/<int:site_id>")
@admin
def site_modifier(site_id):
    ligne = get_db().execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    if not ligne:
        abort(404)
    return render_template("site_form.html", site=Site.depuis_ligne(ligne), site_id=site_id, icones=ICONES,
                           categories=_categories(), page="sites")


def _categories() -> list[str]:
    return [r[0] for r in get_db().execute(
        "SELECT DISTINCT categorie FROM sites WHERE categorie != '' ORDER BY categorie")]


@bp.get("/admin/demandes")
@admin
def admin_demandes():
    D.rafraichir_inscrites()
    toutes = D.lister()
    compte: dict[str, int] = {}
    for d in toutes:
        compte[d["statut"]] = compte.get(d["statut"], 0) + 1
    return render_template("admin_demandes.html", demandes=toutes, compte=compte, page="demandes")


@bp.get("/admin/membres")
@admin
def admin_membres():
    erreur, membres = "", []
    try:
        users = ak().liste("/core/users/", type="internal", include_groups="true", ordering="name")
        for u in users:
            if u["username"] == "akadmin":
                continue
            noms = [g["name"] for g in u.get("groups_obj") or []]
            membres.append({
                "pk": u["pk"], "nom": u.get("name") or u["username"], "email": u.get("email") or u["username"],
                "actif": u["is_active"], "admin": cfg().GROUPE_ADMINS in noms or u.get("is_superuser"),
                "derniere": (u.get("last_login") or "")[:10],
            })
    except AuthentikErreur as exc:
        erreur = str(exc)
    return render_template("admin_membres.html", membres=membres, erreur_ak=erreur, page="membres")


@bp.get("/admin/journal")
@admin
def admin_journal():
    lignes = get_db().execute("SELECT * FROM journal ORDER BY id DESC LIMIT 200").fetchall()
    return render_template("admin_journal.html", lignes=lignes, page="journal")


@bp.get("/admin/brancher")
@admin
def admin_brancher():
    return render_template("admin_brancher.html", page="brancher")


# ================================================================ API (admins)
def _qui() -> str:
    return visiteur().email


@bp.post("/api/sites")
@admin
def api_site_creer():
    return _enregistrer_site(None)


@bp.put("/api/sites/<int:site_id>")
@admin
def api_site_modifier(site_id):
    return _enregistrer_site(site_id)


def _enregistrer_site(site_id: int | None):
    c = cfg()
    db = get_db()
    ancien = None
    if site_id is not None:
        ancien = db.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
        if not ancien:
            return json_err("Ce site n'existe plus.", 404)
    form = request.get_json(silent=True) or {}
    s = lire_formulaire(form, c.DOMAINE)
    if s.type == "protege" and not s.erreurs.get("sous_domaine"):
        double = db.execute("SELECT id FROM sites WHERE sous_domaine=? AND id IS NOT ?",
                            (s.sous_domaine, site_id)).fetchone()
        if double:
            s.erreurs["sous_domaine"] = "Un autre site utilise déjà ce sous-domaine."
    if s.erreurs:
        return json_err("Il manque quelque chose dans le formulaire.", 422, erreurs=s.erreurs)
    if s.type == "lien":
        # Un simple lien n'a pas de sous-domaine propre : on garde une clé unique.
        s.sous_domaine = (ancien["sous_domaine"] if ancien and ancien["type"] == "lien"
                          else f"lien-{int(time.time() * 1000)}")

    prov, slug = (ancien["ak_provider"], ancien["ak_app_slug"]) if ancien else (None, None)
    try:
        sy = synchro()
        if s.type == "protege":
            prov, slug = sy.modifier(prov, slug, s) if (prov or slug) else sy.creer(s)
        elif prov or slug:
            sy.supprimer(prov, slug)
            prov, slug = None, None
    except AuthentikErreur as exc:
        return json_err(str(exc), 502)

    valeurs = (s.nom, s.sous_domaine, s.cible, s.lien, s.description, s.icone, s.categorie, s.acces,
               s.groupes, s.chemins_publics, s.type, s.ordre, prov, slug)
    if ancien:
        db.execute("UPDATE sites SET nom=?, sous_domaine=?, cible=?, lien=?, description=?, icone=?, categorie=?, "
                   "acces=?, groupes=?, chemins_publics=?, type=?, ordre=?, ak_provider=?, ak_app_slug=? WHERE id=?",
                   (*valeurs, site_id))
        journaliser(_qui(), f"a modifié le site {s.nom}")
    else:
        cur = db.execute("INSERT INTO sites (nom, sous_domaine, cible, lien, description, icone, categorie, acces, "
                         "groupes, chemins_publics, type, ordre, ak_provider, ak_app_slug) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", valeurs)
        site_id = cur.lastrowid
        journaliser(_qui(), f"a ajouté le site {s.nom}")
    db.commit()
    message = f"« {s.nom} » est enregistré."
    if s.type == "protege":
        message += f" Il répond sur {s.lien} (compte une minute le temps qu'Authentik le prenne en compte)."
    return jsonify({"ok": True, "message": message, "id": site_id, "lien": s.lien})


@bp.delete("/api/sites/<int:site_id>")
@admin
def api_site_supprimer(site_id):
    db = get_db()
    ligne = db.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    if not ligne:
        return json_err("Ce site n'existe plus.", 404)
    try:
        synchro().supprimer(ligne["ak_provider"], ligne["ak_app_slug"])
    except AuthentikErreur as exc:
        return json_err(str(exc), 502)
    db.execute("DELETE FROM sites WHERE id=?", (site_id,))
    db.commit()
    journaliser(_qui(), f"a supprimé le site {ligne['nom']}")
    return jsonify({"ok": True, "message": f"« {ligne['nom']} » est supprimé (de la page et d'Authentik)."})


@bp.post("/api/sites/<int:site_id>/reparer")
@admin
def api_site_reparer(site_id):
    db = get_db()
    ligne = db.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    if not ligne or ligne["type"] != "protege":
        return json_err("Rien à réparer pour ce site.", 404)
    try:
        prov, slug = synchro().modifier(ligne["ak_provider"], ligne["ak_app_slug"], Site.depuis_ligne(ligne))
    except AuthentikErreur as exc:
        return json_err(str(exc), 502)
    db.execute("UPDATE sites SET ak_provider=?, ak_app_slug=? WHERE id=?", (prov, slug, site_id))
    db.commit()
    journaliser(_qui(), f"a réparé le site {ligne['nom']}")
    return jsonify({"ok": True, "message": f"« {ligne['nom']} » est de nouveau en place dans Authentik."})


@bp.post("/api/sites/tester")
@admin
def api_site_tester():
    from .sites import normaliser_cible
    cible = normaliser_cible((request.get_json(silent=True) or {}).get("cible", ""))
    if not cible:
        return json_err("Écris d'abord l'IP et le port, par exemple 192.168.1.20:8080.")
    try:
        r = requests.get(cible, timeout=4, allow_redirects=False, verify=False)
        conseil = "" if est_ip_privee(cible) else " Attention : ce n'est pas une adresse du réseau local."
        return jsonify({"ok": True, "message": f"Le site répond (code {r.status_code}).{conseil}"})
    except requests.RequestException as exc:
        return json_err(f"La passerelle n'arrive pas à joindre {cible} ({exc.__class__.__name__}). "
                        "Vérifie l'IP, le port, et le pare-feu du LXC du site.")


@bp.post("/api/demandes/<int:demande_id>/<action>")
@admin
def api_demande(demande_id, action):
    fonctions = {"accepter": D.accepter, "refuser": D.refuser, "renvoyer": D.renvoyer, "oublier": D.oublier}
    if action not in fonctions:
        abort(404)
    res = fonctions[action](demande_id, _qui())
    return jsonify({"ok": res.ok, "message": res.message, "lien": res.lien}), (200 if res.ok else 400)


@bp.post("/api/membres/<int:pk>/actif")
@admin
def api_membre_actif(pk):
    actif = bool((request.get_json(silent=True) or {}).get("actif"))
    try:
        client = ak()
        u = client.get(f"/core/users/{pk}/", include_groups="true")
        noms = [g["name"] for g in u.get("groups_obj") or []]
        if cfg().GROUPE_ADMINS in noms or u.get("is_superuser"):
            return json_err("Les administrateurs se gèrent dans Authentik.", 400)
        client.patch(f"/core/users/{pk}/", {"is_active": actif})
    except AuthentikErreur as exc:
        return json_err(str(exc), 502)
    journaliser(_qui(), f"a {'débloqué' if actif else 'bloqué'} {u.get('email') or u['username']}")
    nom = u.get("name") or u["username"]
    return jsonify({"ok": True, "message": f"{nom} est {'de nouveau autorisé' if actif else 'bloqué'}."})


# ================================================================ erreurs
@bp.app_errorhandler(401)
def e401(_e):
    if request.path.startswith("/api/"):
        return json_err("Connexion requise.", 401)
    return render_template("erreur.html", titre="Connexion requise", icone="lock", classe="warn",
                           texte=f"Passe par https://{cfg().DOMAINE} pour te connecter."), 401


@bp.app_errorhandler(403)
def e403(_e):
    if request.path.startswith("/api/"):
        return json_err("Réservé aux administrateurs.", 403)
    return render_template("erreur.html", titre="Réservé aux administrateurs", icone="shield", classe="warn",
                           texte="Cette page sert à gérer le hub."), 403


@bp.app_errorhandler(404)
def e404(_e):
    if request.path.startswith("/api/"):
        return json_err("Introuvable.", 404)
    return render_template("erreur.html", titre="Page introuvable", icone="compass", classe="acc",
                           texte="Cette adresse ne mène nulle part."), 404


@bp.get("/deconnexion")
def deconnexion():
    return redirect("/outpost.goauthentik.io/sign_out")


@bp.get("/admin")
@admin
def admin_racine():
    return redirect(url_for("hub.admin_sites"))


_ = CHEMINS_PUBLICS_DEFAUT  # utilisé par les gabarits
