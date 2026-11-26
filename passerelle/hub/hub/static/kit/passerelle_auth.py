"""passerelle_auth.py : brancher une application Python derrière la passerelle (Authentik).

Copie ce fichier tel quel dans ton projet. Aucune dépendance obligatoire ;
les aides Flask ne servent que si Flask est installé.

Il y a trois façons d'arriver jusqu'à ton application :

1. Une PERSONNE, dans un navigateur, par https://<site>.super-nono.cc
   Authentik l'a déjà connectée et ajoute ses en-têtes X-Authentik-*. On ne les
   croit que si l'en-tête secret X-Passerelle-Cle est le bon.
   → utilisateur(), @connexion_requise, @admin_requis

2. Une MACHINE (autre site, Home Assistant, script) avec un jeton :
   en direct sur l'adresse locale (IP:port), ou de l'extérieur par un chemin
   public déclaré dans le hub (/api/..., /mcp). Authentik ne vérifie rien sur
   ces chemins : c'est ton application qui vérifie le jeton.
   → machine(), @machine_requise, @personne_ou_machine

3. Un WEBHOOK signé (BotPanel, GitHub…) sur un chemin public.
   → signature_valide()

4. CLAUDE, par un connecteur MCP : connexion OAuth avec ton compte Authentik.
   Dans Claude, tu colles juste https://<site>.super-nono.cc/mcp puis « Se connecter ».
   → brancher_mcp(app) et @mcp_requis (Flask), ou mcp_verifier() ailleurs

Variables d'environnement lues :
  PASSERELLE_CLE            le secret de la passerelle (hub → « Brancher un projet »)
  PASSERELLE_GROUPE_ADMINS  groupe des administrateurs (défaut « authentik Admins »)
  SERVICE_JETONS            jetons acceptés pour les machines : « nom:jeton,nom2:jeton2 »
  PASSERELLE_DEV_EMAIL      développement seulement : fait comme si cette personne
                            était connectée. Ignoré dès que PASSERELLE_CLE est rempli.
  PASSERELLE_DEV_ADMIN      « 1 » pour que la personne de dev soit admin
  PASSERELLE_AUTH_URL       adresse d'Authentik (défaut https://auth.super-nono.cc)
  PASSERELLE_URL_SITE       adresse publique du site, ex. https://recettes.super-nono.cc
                            (sinon déduite de la requête)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
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
    « X-Api-Key: <jeton> », ou ?cle=<jeton> dans l'adresse (seulement pour un
    outil qui ne sait pas envoyer d'en-tête ; Claude, lui, utilise la connexion OAuth).
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


# ------------------------------------------------------------------ 4. MCP (OAuth avec Authentik)
# Claude découvre tout seul comment se connecter :
#   /mcp répond 401 + WWW-Authenticate → /.well-known/oauth-protected-resource (ce site)
#   → /.well-known/oauth-authorization-server (ce site, qui renvoie vers Authentik)
#   → /oauth/register (donne l'identifiant du client « claude-mcp » créé par la passerelle)
#   → page de connexion Authentik → jeton → /mcp avec « Authorization: Bearer <jeton> ».
# Le site vérifie le jeton auprès d'Authentik (userinfo) et sait qui l'utilise.
CLIENT_MCP = "claude-mcp"
SLUG_MCP = "mcp"
SCOPES_MCP = ["openid", "email", "profile", "offline_access"]
RETOURS_MCP = [
    re.compile(r"^https://claude\.ai/api/mcp/auth_callback$"),
    re.compile(r"^https://claude\.com/api/mcp/auth_callback$"),
    re.compile(r"^http://localhost:\d+/callback$"),
    re.compile(r"^http://127\.0\.0\.1:\d+/callback$"),
]
_cache_mcp: dict[str, tuple[float, Utilisateur | None]] = {}
_verrou = threading.Lock()


def _auth_url() -> str:
    return os.environ.get("PASSERELLE_AUTH_URL", "https://auth.super-nono.cc").rstrip("/")


def base_site(entetes: Mapping) -> str:
    """https://recettes.super-nono.cc : réglage PASSERELLE_URL_SITE, sinon déduit de la requête."""
    fixe = os.environ.get("PASSERELLE_URL_SITE", "").strip().rstrip("/")
    if fixe:
        return fixe
    hote = _get(entetes, "X-Forwarded-Host") or _get(entetes, "Host")
    proto = _get(entetes, "X-Forwarded-Proto") or "https"
    return f"{proto.split(',')[0].strip()}://{hote.split(',')[0].strip()}"


def mcp_ressource(base: str, chemin_mcp: str = "/mcp") -> dict:
    """/.well-known/oauth-protected-resource (RFC 9728)."""
    return {
        "resource": base + chemin_mcp,
        "authorization_servers": [base],
        "scopes_supported": SCOPES_MCP,
        "bearer_methods_supported": ["header"],
    }


def mcp_serveur(base: str) -> dict:
    """/.well-known/oauth-authorization-server (RFC 8414) : les adresses d'Authentik, vues depuis ce site."""
    a = _auth_url() + "/application/o"
    return {
        "issuer": base,
        "authorization_endpoint": f"{a}/authorize/",
        "token_endpoint": f"{a}/token/",
        "revocation_endpoint": f"{a}/revoke/",
        "userinfo_endpoint": f"{a}/userinfo/",
        "jwks_uri": f"{a}/{SLUG_MCP}/jwks/",
        "registration_endpoint": base + "/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": SCOPES_MCP,
    }


def mcp_enregistrer(demande: dict) -> tuple[dict, int]:
    """/oauth/register (RFC 7591) : tous les clients reçoivent le même client public « claude-mcp ».

    Rien n'est secret ici : c'est Authentik qui n'accepte que les adresses de retour de Claude,
    et chaque personne doit se connecter avec son compte.
    """
    retours = [str(u) for u in (demande or {}).get("redirect_uris") or []]
    if not retours or not all(any(r.match(u) for r in RETOURS_MCP) for u in retours):
        return {"error": "invalid_redirect_uri",
                "error_description": "Seuls Claude (claude.ai) et Claude Code (localhost) sont acceptés."}, 400
    return {
        "client_id": CLIENT_MCP,
        "client_id_issued_at": int(time.time()),
        "client_name": (demande or {}).get("client_name") or "Claude",
        "redirect_uris": retours,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": " ".join(SCOPES_MCP),
    }, 201


def _charge_jwt(jeton: str) -> dict:
    try:
        morceau = jeton.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(morceau + "=" * (-len(morceau) % 4)))
    except (IndexError, ValueError):
        return {}


def mcp_verifier(autorisation: str) -> Utilisateur | None:
    """« Bearer <jeton> » d'un client MCP → la personne, ou None. Résultat gardé 5 minutes."""
    if not autorisation.lower().startswith("bearer "):
        return None
    jeton = autorisation[7:].strip()
    charge = _charge_jwt(jeton)
    aud = charge.get("aud")
    if CLIENT_MCP not in (aud if isinstance(aud, list) else [aud]) and charge.get("azp") != CLIENT_MCP:
        return None  # jeton d'une autre application, ou pas un jeton d'Authentik
    cle = hashlib.sha256(jeton.encode()).hexdigest()
    maintenant = time.time()
    with _verrou:
        trouve = _cache_mcp.get(cle)
        if trouve and trouve[0] > maintenant:
            return trouve[1]
    try:
        req = urllib.request.Request(_auth_url() + "/application/o/userinfo/",
                                     headers={"Authorization": f"Bearer {jeton}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            info = json.loads(r.read())
        u = Utilisateur(email=str(info.get("email") or "").lower(), nom=info.get("name") or "",
                        username=info.get("preferred_username") or "", uid=info.get("sub") or "",
                        groupes=list(info.get("groups") or []))
        if not u.email:
            u = None
    except urllib.error.HTTPError:
        u = None  # jeton refusé (expiré, révoqué)
    except (urllib.error.URLError, OSError, ValueError):
        return None  # Authentik injoignable : on ne garde rien en cache
    expire = min(maintenant + 300, float(charge.get("exp") or maintenant + 300))
    with _verrou:
        if len(_cache_mcp) > 500:
            _cache_mcp.clear()
        _cache_mcp[cle] = (expire, u)
    return u


def mcp_entete_refus(base: str) -> str:
    """Valeur de WWW-Authenticate à renvoyer avec un 401 sur /mcp : c'est ce qui lance la connexion."""
    return f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource", scope="{" ".join(SCOPES_MCP)}"'


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

    def brancher_mcp(app, chemin_mcp: str = "/mcp") -> None:
        """Ajoute les adresses de découverte OAuth. À appeler une fois après avoir créé l'app Flask.

        Chemins publics à déclarer dans le hub : /mcp, /.well-known, /oauth
        """
        def ressource(_suite=None):
            return jsonify(mcp_ressource(base_site(request.headers), chemin_mcp))

        def serveur():
            return jsonify(mcp_serveur(base_site(request.headers)))

        def enregistrer():
            corps, code = mcp_enregistrer(request.get_json(silent=True) or {})
            return jsonify(corps), code

        app.add_url_rule("/.well-known/oauth-protected-resource", "passerelle_mcp_ressource", ressource)
        app.add_url_rule("/.well-known/oauth-protected-resource/<path:_suite>", "passerelle_mcp_ressource2", ressource)
        app.add_url_rule("/.well-known/oauth-authorization-server", "passerelle_mcp_serveur", serveur)
        app.add_url_rule("/.well-known/openid-configuration", "passerelle_mcp_oidc", serveur)
        app.add_url_rule("/oauth/register", "passerelle_mcp_register", enregistrer, methods=["POST"])

    def mcp_requis(f: Callable):
        """Pour la route /mcp : un client MCP connecté (OAuth), ou une machine avec un jeton du .env."""
        @wraps(f)
        def emballe(*a, **kw):
            u = mcp_verifier(request.headers.get("Authorization", ""))
            if u is None and machine() is None:
                rep = jsonify({"error": "invalid_token", "error_description": "Connexion requise."})
                rep.status_code = 401
                rep.headers["WWW-Authenticate"] = mcp_entete_refus(base_site(request.headers))
                return rep
            g._passerelle_u = u
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
