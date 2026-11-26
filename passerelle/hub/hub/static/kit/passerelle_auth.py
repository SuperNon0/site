"""passerelle_auth.py : brancher une application Python derrière la passerelle (Authentik).

Copie ce fichier tel quel dans ton projet. Aucune dépendance obligatoire ;
les aides Flask ne servent que si Flask est installé.

Il y a trois façons d'arriver jusqu'à ton application :

1. Une PERSONNE, dans un navigateur, par https://<site>.super-nono.cc
   Authentik l'a déjà connectée et ajoute ses en-têtes X-Authentik-*. On ne les
   croit que si l'en-tête secret X-Passerelle-Cle est le bon.
   → utilisateur(), @connexion_requise, @admin_requis

2. Une MACHINE (autre site, Home Assistant, script, client MCP) avec un jeton :
   en direct sur l'adresse locale (IP:port), ou de l'extérieur par un chemin
   public déclaré dans le hub (/api/..., /mcp). Authentik ne vérifie rien sur
   ces chemins : c'est ton application qui vérifie le jeton.
   → machine(), @machine_requise, @personne_ou_machine

3. Un WEBHOOK signé (BotPanel, GitHub…) sur un chemin public.
   → signature_valide()

Variables d'environnement lues :
  PASSERELLE_CLE            le secret de la passerelle (hub → « Brancher un projet »)
  PASSERELLE_GROUPE_ADMINS  groupe des administrateurs (défaut « authentik Admins »)
  SERVICE_JETONS            jetons acceptés pour les machines : « nom:jeton,nom2:jeton2 »
  PASSERELLE_DEV_EMAIL      développement seulement : fait comme si cette personne
                            était connectée. Ignoré dès que PASSERELLE_CLE est rempli.
  PASSERELLE_DEV_ADMIN      « 1 » pour que la personne de dev soit admin
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass, field
from functools import wraps
from typing import Callable, Mapping

LIEN_DECONNEXION = "/outpost.goauthentik.io/sign_out"


@dataclass
class Utilisateur:
    email: str
    nom: str = ""
    username: str = ""
    uid: str = ""
    groupes: list[str] = field(default_factory=list)

    @property
    def admin(self) -> bool:
        return os.environ.get("PASSERELLE_GROUPE_ADMINS", "authentik Admins") in self.groupes

    @property
    def prenom(self) -> str:
        return (self.nom or self.email).split(" ")[0].split("@")[0]

    def dans(self, groupe: str) -> bool:
        return groupe in self.groupes


# ------------------------------------------------------------------ outils
def _egal(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def _get(entetes: Mapping, nom: str) -> str:
    """Lit un en-tête sans tenir compte de la casse, et répare l'UTF-8 (« Noë », pas « NoÃ« »)."""
    val = entetes.get(nom)
    if val is None:
        bas = nom.lower()
        val = next((v for k, v in entetes.items() if k.lower() == bas), "")
    if isinstance(val, (list, tuple)):
        val = val[0] if val else ""
    val = str(val or "")
    try:
        return val.encode("latin-1").decode("utf-8").strip()
    except (UnicodeEncodeError, UnicodeDecodeError):
        return val.strip()


def _jetons() -> dict[str, str]:
    """SERVICE_JETONS="recipelog:xxxx,ha:yyyy" → {"recipelog": "xxxx", "ha": "yyyy"}."""
    out = {}
    for morceau in os.environ.get("SERVICE_JETONS", "").split(","):
        morceau = morceau.strip()
        if not morceau:
            continue
        nom, _, jeton = morceau.partition(":") if ":" in morceau else ("machine", "", morceau)
        if len(jeton) >= 24:  # un jeton trop court est refusé, exprès
            out[nom.strip()] = jeton.strip()
    return out


# ------------------------------------------------------------------ 1. personnes
def lire_utilisateur(entetes: Mapping) -> Utilisateur | None:
    """La personne connectée par Authentik, ou None (requête anonyme, directe ou forgée)."""
    cle = os.environ.get("PASSERELLE_CLE", "").strip()
    if not cle:
        dev = os.environ.get("PASSERELLE_DEV_EMAIL", "").strip()
        if dev:
            groupes = ["authentik Admins"] if os.environ.get("PASSERELLE_DEV_ADMIN") == "1" else ["Membres"]
            return Utilisateur(email=dev, nom="Dev", username=dev, groupes=groupes)
        return None
    if not _egal(_get(entetes, "X-Passerelle-Cle"), cle):
        return None
    email = _get(entetes, "X-Authentik-Email").lower()
    username = _get(entetes, "X-Authentik-Username")
    if not (email or username):
        return None
    return Utilisateur(
        email=email or username,
        nom=_get(entetes, "X-Authentik-Name"),
        username=username,
        uid=_get(entetes, "X-Authentik-Uid"),
        groupes=[g for g in _get(entetes, "X-Authentik-Groups").split("|") if g],
    )


# ------------------------------------------------------------------ 2. machines
def lire_machine(entetes: Mapping, parametres: Mapping | None = None) -> str | None:
    """Le nom de la machine dont le jeton est valide, ou None.

    Le jeton peut arriver par « Authorization: Bearer <jeton> » (à préférer),
    « X-Api-Key: <jeton> », ou ?cle=<jeton> dans l'adresse (seulement pour les
    clients qui ne savent pas envoyer d'en-tête, comme un connecteur MCP).
    """
    fourni = ""
    auth = _get(entetes, "Authorization")
    if auth.lower().startswith("bearer "):
        fourni = auth[7:].strip()
    fourni = fourni or _get(entetes, "X-Api-Key")
    if not fourni and parametres is not None:
        fourni = str(parametres.get("cle") or "").strip()
    if not fourni:
        return None
    trouve = None
    for nom, jeton in _jetons().items():  # on compare tout, pour un temps constant
        if _egal(fourni, jeton):
            trouve = nom
    return trouve


# ------------------------------------------------------------------ 3. webhooks
def signature_valide(corps: bytes, horodatage: str, signature: str, secret: str,
                     tolerance: int = 300) -> bool:
    """Signature HMAC-SHA256 au format BotPanel : "sha256=" + hex(HMAC(secret, horodatage + "." + corps)).

    En-têtes BotPanel : X-BotPanel-Timestamp et X-BotPanel-Signature.
    """
    if not (secret and horodatage and signature):
        return False
    try:
        if abs(time.time() - int(horodatage)) > tolerance:
            return False
    except ValueError:
        return False
    attendu = "sha256=" + hmac.new(secret.encode(), horodatage.encode() + b"." + corps, hashlib.sha256).hexdigest()
    return hmac.compare_digest(attendu, signature.strip())


# ------------------------------------------------------------------ aides Flask
try:
    from flask import abort, g, jsonify, request
except ImportError:  # pas Flask : seules les fonctions ci-dessus servent
    pass
else:
    def utilisateur() -> Utilisateur | None:
        if "_passerelle_u" not in g:
            g._passerelle_u = lire_utilisateur(request.headers)
        return g._passerelle_u

    def machine() -> str | None:
        if "_passerelle_m" not in g:
            g._passerelle_m = lire_machine(request.headers, request.args)
        return g._passerelle_m

    def _refus(code: int, texte: str):
        if request.path.startswith(("/api/", "/mcp")):
            return jsonify({"erreur": texte}), code
        abort(code)

    def connexion_requise(f: Callable):
        @wraps(f)
        def emballe(*a, **kw):
            if utilisateur() is None:
                return _refus(401, "Connexion requise.")
            return f(*a, **kw)
        return emballe

    def admin_requis(f: Callable):
        @wraps(f)
        def emballe(*a, **kw):
            u = utilisateur()
            if u is None:
                return _refus(401, "Connexion requise.")
            if not u.admin:
                return _refus(403, "Réservé aux administrateurs.")
            return f(*a, **kw)
        return emballe

    def machine_requise(f: Callable):
        @wraps(f)
        def emballe(*a, **kw):
            if machine() is None:
                return _refus(401, "Jeton manquant ou invalide.")
            return f(*a, **kw)
        return emballe

    def personne_ou_machine(f: Callable):
        """Une personne connectée OU une machine avec un jeton (pratique pour une API)."""
        @wraps(f)
        def emballe(*a, **kw):
            if utilisateur() is None and machine() is None:
                return _refus(401, "Connexion ou jeton requis.")
            return f(*a, **kw)
        return emballe
