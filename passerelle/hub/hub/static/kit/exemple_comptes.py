"""exemple_comptes.py : un système de comptes dans un site derrière la passerelle.

Exemple à copier et adapter (Flask + SQLite, aucune autre dépendance que passerelle_auth.py).

Le principe :
  1. Authentik a déjà connecté la personne. passerelle_auth.utilisateur() dit qui c'est
     (e-mail, nom, groupes), et seulement si l'en-tête secret de la passerelle est bon.
  2. Le site garde SA table « comptes », retrouvée par l'e-mail. Le premier passage la crée
     tout seul : rien à inscrire à la main, plusieurs personnes = plusieurs comptes.
  3. Les droits DANS le site (rôle, compte suspendu, préférences, données de chacun)
     sont dans cette table. Qui peut ENTRER sur le site se règle dans le hub
     (« Réservé à certains groupes »), pas ici.
  4. Le rôle peut venir des groupes Authentik (ROLES_PAR_GROUPE) : il est recalculé à
     chaque visite, donc changer quelqu'un de groupe dans Authentik suffit.
  5. Claude (MCP) agit au nom du compte qui a cliqué « Autoriser » : mêmes droits.

Usage :
    from exemple_comptes import init_comptes, compte_courant, compte_requis, role_requis
    init_comptes(app)

    @app.get("/")
    @compte_requis
    def accueil():
        c = compte_courant()
        return f"Bonjour {c['nom']} ({c['role']})"

    @app.post("/api/salons")
    @role_requis("animateur")            # « admin » passe partout
    def creer_salon(): ...

Ne jamais écrire de mot de passe ni de page de connexion ici : c'est Authentik qui s'en charge.
"""

from __future__ import annotations

import os
import sqlite3
from functools import wraps

from flask import abort, g, jsonify, request

import passerelle_auth as pa

FICHIER = os.environ.get("COMPTES_DB", "data/app.db")

# Groupe Authentik → rôle dans CE site. Le premier qui correspond gagne ; les admins de la
# passerelle (groupe « authentik Admins ») sont toujours « admin ». Les autres : ROLE_DEFAUT.
ROLES_PAR_GROUPE = [
    ("jeu-animateurs", "animateur"),
]
ROLE_DEFAUT = "membre"
ORDRE_ROLES = ["membre", "animateur", "admin"]   # du plus petit au plus grand

SCHEMA = """
CREATE TABLE IF NOT EXISTS comptes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    email     TEXT NOT NULL UNIQUE,          -- la clé : X-Authentik-Email, en minuscules
    nom       TEXT NOT NULL DEFAULT '',
    role      TEXT NOT NULL DEFAULT 'membre',
    actif     INTEGER NOT NULL DEFAULT 1,    -- 0 = suspendu dans CE site (Authentik le laisse entrer)
    cree_le   TEXT NOT NULL DEFAULT (datetime('now')),
    vu_le     TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def db() -> sqlite3.Connection:
    if "_comptes_db" not in g:
        os.makedirs(os.path.dirname(FICHIER) or ".", exist_ok=True)
        g._comptes_db = sqlite3.connect(FICHIER, timeout=10)
        g._comptes_db.row_factory = sqlite3.Row
    return g._comptes_db


def init_comptes(app) -> None:
    with app.app_context():
        db().executescript(SCHEMA)
        db().commit()

    @app.teardown_appcontext
    def _fermer(_exc=None):
        c = g.pop("_comptes_db", None)
        if c is not None:
            c.close()

    # Le MCP vérifie aussi, à chaque appel, que le compte n'est pas suspendu dans le site.
    def _mcp_permis(email: str) -> bool:
        ligne = db().execute("SELECT actif FROM comptes WHERE email=?", (email.lower(),)).fetchone()
        return ligne is None or bool(ligne["actif"])
    pa.regle_mcp = _mcp_permis


def role_depuis_groupes(u: pa.Utilisateur) -> str:
    if u.admin:
        return "admin"
    for groupe, role in ROLES_PAR_GROUPE:
        if u.dans(groupe):
            return role
    return ROLE_DEFAUT


def compte_courant() -> sqlite3.Row | None:
    """Le compte de la personne (ou de Claude agissant pour elle). Créé à la première visite."""
    if "_compte" in g:
        return g._compte
    u = pa.utilisateur()          # None = pas passé par Authentik, ou une machine
    if u is None:
        g._compte = None
        return None
    email, c = u.email.lower(), db()
    if pa.lire_utilisateur(request.headers) is not None:
        # Passage par Authentik : nom et groupes à jour, on recalcule le rôle.
        c.execute(
            "INSERT INTO comptes (email, nom, role) VALUES (?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET nom=excluded.nom, role=excluded.role, vu_le=datetime('now')",
            (email, u.nom, role_depuis_groupes(u)),
        )
    else:
        # Claude (jeton MCP) : le jeton ne porte pas les groupes, on garde le rôle enregistré.
        c.execute("INSERT OR IGNORE INTO comptes (email, nom, role) VALUES (?, ?, ?)",
                  (email, u.nom, "admin" if u.admin else ROLE_DEFAUT))
        c.execute("UPDATE comptes SET vu_le=datetime('now') WHERE email=?", (email,))
    c.commit()
    g._compte = c.execute("SELECT * FROM comptes WHERE email=?", (email,)).fetchone()
    return g._compte


def _refus(code: int, texte: str):
    if request.path.startswith("/api/"):
        return jsonify({"erreur": texte}), code
    abort(code, texte)


def compte_requis(f):
    @wraps(f)
    def emballe(*a, **kw):
        c = compte_courant()
        if c is None:
            return _refus(401, "Connexion requise.")
        if not c["actif"]:
            return _refus(403, "Ton compte est suspendu sur ce site.")
        return f(*a, **kw)
    return emballe


def role_requis(minimum: str):
    def deco(f):
        @wraps(f)
        @compte_requis
        def emballe(*a, **kw):
            if ORDRE_ROLES.index(compte_courant()["role"]) < ORDRE_ROLES.index(minimum):
                return _refus(403, "Tu n'as pas les droits pour ça.")
            return f(*a, **kw)
        return emballe
    return deco
