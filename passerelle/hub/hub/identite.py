"""Qui visite le hub ? Authentik le dit, dans les en-têtes qu'il ajoute.

Le hub n'est joignable que par Authentik (réseau interne de docker, aucun
port ouvert). En plus, Authentik envoie un en-tête secret `X-Hub-Cle` :
sans lui, le hub considère la requête comme anonyme, même si quelqu'un
forge de faux en-têtes `X-authentik-*`.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from functools import wraps

from flask import abort, current_app, g, request


@dataclass
class Visiteur:
    email: str
    nom: str
    username: str
    uid: str = ""
    groupes: list[str] = field(default_factory=list)
    admin: bool = False

    @property
    def prenom(self) -> str:
        return (self.nom or self.email).split(" ")[0].split("@")[0]

    @property
    def initiale(self) -> str:
        return (self.prenom[:1] or "?").upper()


def lire_visiteur() -> Visiteur | None:
    cfg = current_app.config["HUB"]
    if cfg.DEV:
        return Visiteur(email=cfg.DEV_EMAIL, nom="Noë (dev)", username=cfg.DEV_EMAIL,
                        groupes=[cfg.GROUPE_ADMINS] if cfg.DEV_ADMIN else [cfg.GROUPE_MEMBRES],
                        admin=cfg.DEV_ADMIN)
    cle = request.headers.get("X-Hub-Cle", "")
    if not cfg.HUB_CLE_PROXY or not hmac.compare_digest(cle.encode(), cfg.HUB_CLE_PROXY.encode()):
        return None
    email = request.headers.get("X-Authentik-Email", "").strip()
    username = request.headers.get("X-Authentik-Username", "").strip()
    if not (email or username):
        return None
    groupes = [x for x in request.headers.get("X-Authentik-Groups", "").split("|") if x]
    return Visiteur(
        email=email or username,
        nom=request.headers.get("X-Authentik-Name", "").strip(),
        username=username,
        uid=request.headers.get("X-Authentik-Uid", "").strip(),
        groupes=groupes,
        admin=cfg.GROUPE_ADMINS in groupes,
    )


def visiteur() -> Visiteur | None:
    if "visiteur" not in g:
        g.visiteur = lire_visiteur()
    return g.visiteur


def connecte(f):
    @wraps(f)
    def emballe(*a, **kw):
        if visiteur() is None:
            abort(401)
        return f(*a, **kw)
    return emballe


def admin(f):
    @wraps(f)
    def emballe(*a, **kw):
        v = visiteur()
        if v is None:
            abort(401)
        if not v.admin:
            abort(403)
        return f(*a, **kw)
    return emballe
