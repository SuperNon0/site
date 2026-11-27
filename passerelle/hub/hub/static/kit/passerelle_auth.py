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

4. CLAUDE, par un connecteur MCP : bouton « Se connecter » dans Claude, puis « Autoriser ? »
   sur le site. Le site est son propre guichet OAuth (modèle RecipeLog, docs/mcp-oauth.md).
   → brancher_mcp(app) et @mcp_requis (Flask), ou les fonctions de la partie 4 ailleurs

Variables d'environnement lues :
  PASSERELLE_CLE            le secret de la passerelle (hub → « Brancher un projet »)
  PASSERELLE_GROUPE_ADMINS  groupe des administrateurs (défaut « authentik Admins »)
  SERVICE_JETONS            jetons acceptés pour les machines : « nom:jeton,nom2:jeton2 »
  PASSERELLE_DEV_EMAIL      développement seulement : fait comme si cette personne
                            était connectée. Ignoré dès que PASSERELLE_CLE est rempli.
  PASSERELLE_DEV_ADMIN      « 1 » pour que la personne de dev soit admin
  PASSERELLE_URL_SITE       adresse publique du site, ex. https://recettes.super-nono.cc
                            (à mettre : l'adresse vue en interne n'est pas la bonne)
  PASSERELLE_MCP_SECRET     secret du guichet OAuth du MCP (32 caractères ou plus, propre au site)
  PASSERELLE_MCP_POUR       qui peut brancher Claude : admins (défaut), tous, ou un groupe
  PASSERELLE_MCP_FICHIER    compteur « tout déconnecter » (défaut mcp-oauth.json)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import wraps
from html import escape as _esc
from typing import Callable, Mapping
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

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


# ------------------------------------------------------------------ 4. MCP : le guichet OAuth du site
# Modèle RecipeLog (docs/mcp-oauth.md) : Authentik ne sait pas inscrire Claude tout seul (ni DCR ni
# CIMD), donc le site est lui-même le serveur d'autorisation de son MCP. Aucune application en plus
# dans Authentik, rien en base : clients, codes et jetons sont signés (HMAC) avec une clé propre au
# site. Seul un compteur « génération » (petit fichier JSON) permet de tout déconnecter.
#
# Adresses (toutes sur le site) :
#   /api/mcp                                       401 + WWW-Authenticate sans jeton       public, exact
#   /.well-known/oauth-protected-resource[/api/mcp] fiche de la ressource                  public
#   /.well-known/oauth-authorization-server         fiche du serveur de connexion          public
#   /api/mcp/oauth/inscription                      inscription automatique (DCR)          public
#   /api/mcp/oauth/autoriser                        page « Autoriser Claude ? »            DERRIÈRE AUTHENTIK
#   /api/mcp/oauth/jeton                            code ou renouvellement → jeton         public
#
# Variables : PASSERELLE_URL_SITE (obligatoire en vrai), PASSERELLE_MCP_SECRET (32 caractères ou plus,
# propre au site ; à défaut, clé tirée de PASSERELLE_CLE et de l'adresse du site),
# PASSERELLE_MCP_POUR (« admins » par défaut, « tous », ou le nom d'un groupe Authentik),
# PASSERELLE_MCP_FICHIER (compteur, défaut « mcp-oauth.json » dans le dossier courant).
CHEMIN_MCP = "/api/mcp"
DUREE_CODE, DUREE_ACCES, DUREE_RENOUV = 5 * 60, 3600, 60 * 86400
LOCALES = ("localhost", "127.0.0.1", "[::1]", "::1")
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, MCP-Protocol-Version",
}

_verrou = threading.Lock()


def base_site(entetes: Mapping) -> str:
    """https://recettes.super-nono.cc : PASSERELLE_URL_SITE, sinon déduit de la requête (à éviter en vrai)."""
    fixe = os.environ.get("PASSERELLE_URL_SITE", "").strip().rstrip("/")
    if fixe:
        return fixe
    hote = _get(entetes, "X-Forwarded-Host") or _get(entetes, "Host")
    proto = _get(entetes, "X-Forwarded-Proto") or "https"
    return f"{proto.split(',')[0].strip()}://{hote.split(',')[0].strip()}"


# Règle facultative de l'application, vérifiée à chaque appel : regle_mcp = lambda email: ...
regle_mcp: Callable[[str], bool] | None = None


@dataclass
class Reponse:
    """Réponse neutre (statut, corps, en-têtes), à convertir pour ton framework (Flask : voir plus bas)."""
    statut: int
    corps: str = ""
    entetes: dict = field(default_factory=dict)


def _json(corps: dict, statut: int = 200, **entetes) -> Reponse:
    return Reponse(statut, json.dumps(corps), {"Content-Type": "application/json", "Cache-Control": "no-store",
                                               **CORS, **entetes})


def _maintenant() -> int:
    return int(time.time())


def _b64(o: bytes) -> str:
    return base64.urlsafe_b64encode(o).rstrip(b"=").decode()


def _deb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _cle_oauth(base: str) -> bytes:
    secret = os.environ.get("PASSERELLE_MCP_SECRET", "").strip() or os.environ.get("PASSERELLE_CLE", "").strip()
    if len(secret) < 32:
        raise RuntimeError("PASSERELLE_MCP_SECRET (32 caractères ou plus) manque dans le .env du site.")
    # L'adresse du site entre dans la clé : un jeton d'un site ne vaut rien sur un autre.
    return hmac.new(secret.encode(), f"mcp-oauth-v1|{base}".encode(), hashlib.sha256).digest()


def _mac(base: str, morceau: str) -> str:
    return _b64(hmac.new(_cle_oauth(base), morceau.encode(), hashlib.sha256).digest())


def sceller(base: str, prefixe: str, charge: dict) -> str:
    """Valeur signée : préfixe + base64url(JSON) + "." + signature."""
    morceau = _b64(json.dumps(charge, separators=(",", ":")).encode())
    return f"{prefixe}{morceau}.{_mac(base, morceau)}"


def desceller(base: str, brut: str | None, prefixe: str, genre: str) -> dict | None:
    """Relit une valeur signée : bonne signature, bon genre, pas expirée. Sinon None."""
    if not brut or not brut.startswith(prefixe) or len(brut) > 6000:
        return None
    morceaux = brut[len(prefixe):].split(".")
    if len(morceaux) != 2 or not all(morceaux):
        return None
    if not _egal(_mac(base, morceaux[0]), morceaux[1]):
        return None
    try:
        v = json.loads(_deb64(morceaux[0]))
    except ValueError:
        return None
    return v if isinstance(v, dict) and v.get("t") == genre and isinstance(v.get("exp"), int) and v["exp"] > _maintenant() else None


def urls_mcp(base: str) -> dict:
    return {
        "resource": base + CHEMIN_MCP,
        "resource_metadata": f"{base}/.well-known/oauth-protected-resource{CHEMIN_MCP}",
        "authorize": f"{base}{CHEMIN_MCP}/oauth/autoriser",
        "token": f"{base}{CHEMIN_MCP}/oauth/jeton",
        "register": f"{base}{CHEMIN_MCP}/oauth/inscription",
    }


def fiche_ressource(base: str, nom: str = "") -> Reponse:
    u = urls_mcp(base)
    return _json({"resource": u["resource"], "authorization_servers": [base], "scopes_supported": ["mcp"],
                  "bearer_methods_supported": ["header"], "resource_name": nom or urlsplit(base).hostname})


def fiche_serveur(base: str) -> Reponse:
    u = urls_mcp(base)
    return _json({
        "issuer": base, "authorization_endpoint": u["authorize"], "token_endpoint": u["token"],
        "registration_endpoint": u["register"], "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "scopes_supported": ["mcp"], "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    })


def refus_mcp(base: str, jeton_present: bool) -> Reponse:
    """Le 401 de /api/mcp sans jeton valable : c'est lui qui fait apparaître « Se connecter » dans Claude."""
    invalide = 'error="invalid_token", ' if jeton_present else ""
    return Reponse(401, "Unauthorized", {
        "WWW-Authenticate": f'Bearer {invalide}resource_metadata="{urls_mcp(base)["resource_metadata"]}", scope="mcp"',
        "Cache-Control": "no-store", **CORS})


# ----- clients : DCR (client_id signé) ou CIMD (client_id = adresse https de sa fiche)
def _retour_valide(brut) -> bool:
    try:
        u = urlsplit(str(brut))
    except ValueError:
        return False
    if u.fragment or u.username or u.password or not u.hostname:
        return False
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in LOCALES)


def _retour_permis(client: dict, donne: str) -> bool:
    """Adresse exactement déclarée ; en http local, le port peut changer (RFC 8252)."""
    if donne in client["retours"]:
        return True
    g = urlsplit(donne)
    if g.scheme != "http" or g.hostname not in LOCALES:
        return False
    return any((r := urlsplit(x)).scheme == "http" and r.hostname == g.hostname and r.path == g.path
               for x in client["retours"])


def _secret_client(base: str, client_id: str) -> str:
    return _b64(hmac.new(_cle_oauth(base), f"secret:{client_id}".encode(), hashlib.sha256).digest())


def inscrire(base: str, demande: dict | None) -> Reponse:
    """POST /api/mcp/oauth/inscription (DCR, RFC 7591). Ouverte à tous : s'inscrire ne donne accès à rien."""
    d = demande if isinstance(demande, dict) else {}
    retours = d.get("redirect_uris") if isinstance(d.get("redirect_uris"), list) else []
    if not retours or len(retours) > 10 or not all(_retour_valide(r) for r in retours):
        return _json({"error": "invalid_redirect_uri"}, 400)
    methode = d.get("token_endpoint_auth_method") or "client_secret_basic"
    if methode not in ("none", "client_secret_post", "client_secret_basic"):
        return _json({"error": "invalid_client_metadata"}, 400)
    nom = str(d.get("client_name") or urlsplit(retours[0]).hostname)[:80]
    client_id = sceller(base, "cl_", {"t": "client", "n": nom, "r": retours, "m": methode,
                                       "exp": _maintenant() + 100 * 365 * 86400})
    sortie = {"client_id": client_id, "client_id_issued_at": _maintenant(), "client_name": nom,
              "redirect_uris": retours, "grant_types": ["authorization_code", "refresh_token"],
              "response_types": ["code"], "token_endpoint_auth_method": methode, "scope": "mcp"}
    if methode != "none":
        sortie.update(client_secret=_secret_client(base, client_id), client_secret_expires_at=0)
    return _json(sortie, 201)


_fiches_cimd: dict[str, tuple[float, dict | None]] = {}


def _adresse_privee(hote: str) -> bool:
    try:
        infos = socket.getaddrinfo(hote, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            return True
    return not infos


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def lire_fiche_cimd(adresse: str) -> dict | None:
    """Fiche publique d'un client CIMD. Protégée : https:443, pas d'IP privée, pas de redirection, 5 s, 20 Ko, 10 min de cache."""
    trouve = _fiches_cimd.get(adresse)
    if trouve and trouve[0] > time.time():
        return trouve[1]
    fiche = None
    try:
        u = urlsplit(adresse)
        if u.scheme == "https" and u.hostname and (u.port in (None, 443)) and not _adresse_privee(u.hostname):
            ouvreur = urllib.request.build_opener(_SansRedirection, urllib.request.ProxyHandler({}))
            with ouvreur.open(urllib.request.Request(adresse, headers={"Accept": "application/json"}), timeout=5) as r:
                brut = r.read(20 * 1024 + 1)
            if len(brut) <= 20 * 1024:
                fiche = json.loads(brut)
    except (OSError, ValueError, urllib.error.URLError):
        fiche = None
    _fiches_cimd[adresse] = (time.time() + 600, fiche if isinstance(fiche, dict) else None)
    return _fiches_cimd[adresse][1]


def trouver_client(base: str, client_id: str) -> dict | None:
    if client_id.startswith("cl_"):
        v = desceller(base, client_id, "cl_", "client")
        return {"id": client_id, "nom": v["n"], "retours": v["r"], "methode": v["m"]} if v else None
    if not client_id.startswith("https://"):
        return None
    fiche = lire_fiche_cimd(client_id)
    if not fiche or fiche.get("client_id") != client_id:
        return None
    retours = [r for r in fiche.get("redirect_uris") or [] if _retour_valide(r)]
    if not retours or (fiche.get("token_endpoint_auth_method") or "none") != "none":
        return None
    return {"id": client_id, "nom": str(fiche.get("client_name") or urlsplit(client_id).hostname)[:80],
            "retours": retours, "methode": "none"}


# ----- génération (tout déconnecter) et droit d'utiliser le MCP
def _fichier_generation() -> str:
    return os.environ.get("PASSERELLE_MCP_FICHIER", "mcp-oauth.json")


def generation() -> int:
    try:
        with open(_fichier_generation(), encoding="utf-8") as f:
            return int(json.load(f).get("generation", 1))
    except (OSError, ValueError, AttributeError):
        return 1


def tout_deconnecter() -> int:
    """Bouton « Déconnecter Claude » : tous les jetons déjà donnés cessent de marcher."""
    with _verrou:
        n = generation() + 1
        tmp = _fichier_generation() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"generation": n}, f)
        os.replace(tmp, _fichier_generation())
    return n


def peut_utiliser_mcp(u: Utilisateur) -> bool:
    """Qui peut brancher Claude : PASSERELLE_MCP_POUR = admins (défaut) | tous | <nom d'un groupe>."""
    pour = os.environ.get("PASSERELLE_MCP_POUR", "admins").strip()
    if pour == "tous":
        return True
    if pour == "admins":
        return u.admin
    return u.dans(pour)


def _toujours_permis(email: str) -> bool:
    return regle_mcp(email) if regle_mcp else True


# ----- étapes 4 à 6 : la page « Autoriser Claude ? »
def _vers_client(retour: str, params: dict) -> Reponse:
    u = urlsplit(retour)
    q = parse_qsl(u.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v is not None]
    return Reponse(303, "", {"Location": urlunsplit(u._replace(query=urlencode(q))), "Cache-Control": "no-store"})


_STYLE = ("body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0f17;color:#e7ebf3;"
          "font:16px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;padding:24px}"
          ".c{max-width:420px;width:100%;background:#141b28;border:1px solid #1e2737;border-radius:22px;padding:28px}"
          "h1{font:400 26px/1.2 Georgia,serif;margin:0 0 12px}p{color:#aab3c5;margin:0 0 20px}b{color:#e7ebf3}"
          ".b{display:flex;gap:10px}button{flex:1;height:48px;border-radius:12px;font:600 15px system-ui,sans-serif;"
          "cursor:pointer;border:1px solid #2b3549;background:transparent;color:#e7ebf3}"
          "button.o{background:#a99bff;color:#120d2e;border-color:transparent}"
          "@media (prefers-color-scheme:light){body{background:#f5f3ee;color:#1b1d22}.c{background:#fff;border-color:#e7e2d7}"
          "p{color:#454a55}b{color:#1b1d22}button{color:#1b1d22;border-color:#d5cfc1}button.o{background:#5a48d0;color:#fff}}")


def _page(titre: str, corps: str, statut: int = 200, origine_retour: str = "") -> Reponse:
    html = (f'<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            f'initial-scale=1"><title>{_esc(titre)}</title><style>{_STYLE}</style><div class="c">{corps}</div></html>')
    csp = f"frame-ancestors 'none'; form-action 'self' {origine_retour}".strip() + "; default-src 'none'; style-src 'unsafe-inline'"
    return Reponse(statut, html, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                                  "X-Frame-Options": "DENY", "Content-Security-Policy": csp})


def _erreur(texte: str, statut: int = 400) -> Reponse:
    return _page("Connexion de Claude", f"<h1>Connexion impossible</h1><p>{_esc(texte)}</p>", statut)


def autoriser_get(base: str, params: Mapping, u: Utilisateur | None) -> Reponse:
    """GET /api/mcp/oauth/autoriser. Cette adresse reste DERRIÈRE Authentik : la personne y arrive connectée."""
    client = trouver_client(base, str(params.get("client_id") or ""))
    if not client:
        return _erreur("Application inconnue. Relance la connexion depuis Claude.")
    retours = client["retours"]
    retour = params.get("redirect_uri") or (retours[0] if len(retours) == 1 else None)
    if not retour or not _retour_permis(client, retour):
        return _erreur("Adresse de retour refusée.")
    etat = params.get("state")

    def echec(code: str) -> Reponse:
        return _vers_client(retour, {"error": code, "state": etat, "iss": base})

    if params.get("response_type") != "code":
        return echec("unsupported_response_type")
    defi = str(params.get("code_challenge") or "")
    if params.get("code_challenge_method") != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43}", defi):
        return echec("invalid_request")
    if u is None:
        return _erreur("Tu dois passer par la passerelle pour te connecter (adresse publique du site).", 401)
    if not peut_utiliser_mcp(u) or not _toujours_permis(u.email):
        return _erreur("Ce compte n'a pas le droit de brancher Claude sur ce site.", 403)
    billet = sceller(base, "", {"t": "consent", "c": client["id"], "r": retour, "s": etat, "ch": defi,
                                "e": u.email, "n": u.nom, "a": u.admin, "cn": client["nom"],
                                "x": _b64(os.urandom(8)), "exp": _maintenant() + 10 * 60})
    nom = _esc(client["nom"])
    corps = (f"<h1>Autoriser {nom} ?</h1><p><b>{nom}</b> demande à utiliser ce site avec le compte "
             f"<!--email_off--><b>{_esc(u.email)}</b><!--/email_off-->.</p>"
             f'<form method="post"><input type="hidden" name="billet" value="{_esc(billet)}">'
             '<div class="b"><button name="choix" value="refuser">Refuser</button>'
             '<button class="o" name="choix" value="autoriser">Autoriser</button></div></form>')
    o = urlsplit(retour)
    return _page(f"Autoriser {client['nom']} ?", corps, origine_retour=f"{o.scheme}://{o.netloc}")


def autoriser_post(base: str, form: Mapping, u: Utilisateur | None) -> Reponse:
    c = desceller(base, form.get("billet"), "", "consent")
    if not c:
        return _erreur("Page expirée : relance la connexion depuis Claude.")
    if u is None or u.email != c["e"]:
        return _erreur("Tu n'es plus connecté avec le même compte : relance depuis Claude.")
    if form.get("choix") != "autoriser":
        return _vers_client(c["r"], {"error": "access_denied", "state": c["s"], "iss": base})
    code = sceller(base, "", {"t": "code", "c": c["c"], "r": c["r"], "ch": c["ch"], "e": c["e"], "n": c["n"],
                              "a": c["a"], "g": generation(), "j": _b64(os.urandom(12)),
                              "exp": _maintenant() + DUREE_CODE})
    return _vers_client(c["r"], {"code": code, "state": c["s"], "iss": base})


# ----- étape 7 : code ou renouvellement → jeton
_codes_utilises: dict[str, int] = {}


def _pkce_ok(verif: str, defi: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9\-._~]{43,128}", verif or ""):
        return False
    return _egal(_b64(hashlib.sha256(verif.encode()).digest()), defi)


def jeton(base: str, form: Mapping, autorisation: str = "") -> Reponse:
    """POST /api/mcp/oauth/jeton (formulaire x-www-form-urlencoded ou JSON, déjà lu en dictionnaire)."""
    client_id, secret = str(form.get("client_id") or ""), form.get("client_secret")
    if autorisation.lower().startswith("basic "):
        try:
            brut = base64.b64decode(autorisation[6:].strip()).decode()
            i = brut.index(":")
            client_id, secret = unquote(brut[:i]), unquote(brut[i + 1:])
        except (ValueError, UnicodeDecodeError):
            return _json({"error": "invalid_client"}, 401)
    client = trouver_client(base, client_id)
    if not client:
        return _json({"error": "invalid_client"}, 401)
    if client["methode"] != "none" and not (secret and _egal(str(secret), _secret_client(base, client_id))):
        return _json({"error": "invalid_client"}, 401)

    genre = form.get("grant_type")
    if genre == "authorization_code":
        v = desceller(base, form.get("code"), "", "code")
        if not v or v["c"] != client_id:
            return _json({"error": "invalid_grant"}, 400)
        if form.get("redirect_uri") and form.get("redirect_uri") != v["r"]:
            return _json({"error": "invalid_grant"}, 400)
        if not _pkce_ok(str(form.get("code_verifier") or ""), v["ch"]):
            return _json({"error": "invalid_grant"}, 400)
        with _verrou:
            maintenant = _maintenant()
            for k in [k for k, e in _codes_utilises.items() if e < maintenant]:
                del _codes_utilises[k]
            if v["j"] in _codes_utilises:
                return _json({"error": "invalid_grant"}, 400)
            _codes_utilises[v["j"]] = v["exp"]
    elif genre == "refresh_token":
        v = desceller(base, form.get("refresh_token"), "rr_", "rt")
        if not v or v["c"] != client_id:
            return _json({"error": "invalid_grant"}, 400)
    else:
        return _json({"error": "unsupported_grant_type"}, 400)

    if v["g"] != generation() or not _toujours_permis(v["e"]):
        return _json({"error": "invalid_grant"}, 400)
    t = _maintenant()
    qui = {"e": v["e"], "n": v.get("n", ""), "a": bool(v.get("a")), "g": v["g"]}
    return _json({
        "access_token": sceller(base, "ra_", {"t": "at", **qui, "exp": t + DUREE_ACCES}),
        "token_type": "Bearer", "expires_in": DUREE_ACCES, "scope": "mcp",
        "refresh_token": sceller(base, "rr_", {"t": "rt", **qui, "c": client_id, "j": _b64(os.urandom(9)),
                                               "exp": t + DUREE_RENOUV}),
    })


def verifier_jeton_mcp(base: str, autorisation: str) -> Utilisateur | None:
    """À chaque appel de /api/mcp : jeton signé, pas expiré, bonne génération, compte toujours permis."""
    m = re.fullmatch(r"\s*Bearer\s+(\S+)\s*", autorisation or "", re.I)
    v = desceller(base, m.group(1), "ra_", "at") if m else None
    if not v or v["g"] != generation() or not _toujours_permis(v["e"]):
        return None
    groupes = [os.environ.get("PASSERELLE_GROUPE_ADMINS", "authentik Admins")] if v.get("a") else []
    return Utilisateur(email=v["e"], nom=v.get("n", ""), username=v["e"], groupes=groupes)


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

    def _flask(r: Reponse):
        from flask import Response
        return Response(r.corps, status=r.statut, headers=r.entetes)

    def _base() -> str:
        return base_site(request.headers)

    def brancher_mcp(app, nom: str = "") -> None:
        """Ajoute le guichet OAuth du MCP (fiches, inscription, autorisation, jeton, déconnexion).

        À appeler une fois après avoir créé l'app Flask. La route /api/mcp reste la tienne, avec @mcp_requis.
        """
        def preflight():
            from flask import Response
            return Response(status=204, headers=CORS)

        def ressource(_suite=None):
            return preflight() if request.method == "OPTIONS" else _flask(fiche_ressource(_base(), nom))

        def serveur():
            return preflight() if request.method == "OPTIONS" else _flask(fiche_serveur(_base()))

        def inscription():
            if request.method == "OPTIONS":
                return preflight()
            return _flask(inscrire(_base(), request.get_json(silent=True)))

        def autoriser():
            if request.method == "POST":
                return _flask(autoriser_post(_base(), request.form, utilisateur()))
            return _flask(autoriser_get(_base(), request.args, utilisateur()))

        def obtenir_jeton():
            if request.method == "OPTIONS":
                return preflight()
            form = request.form if request.form else (request.get_json(silent=True) or {})
            return _flask(jeton(_base(), form, request.headers.get("Authorization", "")))

        def deconnecter():
            u = utilisateur()
            if u is None or not u.admin:
                return jsonify({"erreur": "Réservé aux administrateurs."}), 403
            return jsonify({"ok": True, "generation": tout_deconnecter()})

        m = ["GET", "OPTIONS"]
        app.add_url_rule("/.well-known/oauth-protected-resource", "mcp_ressource", ressource, methods=m)
        app.add_url_rule("/.well-known/oauth-protected-resource/<path:_suite>", "mcp_ressource2", ressource, methods=m)
        app.add_url_rule("/.well-known/oauth-authorization-server", "mcp_serveur", serveur, methods=m)
        app.add_url_rule(f"{CHEMIN_MCP}/oauth/inscription", "mcp_inscription", inscription, methods=["POST", "OPTIONS"])
        app.add_url_rule(f"{CHEMIN_MCP}/oauth/autoriser", "mcp_autoriser", autoriser, methods=["GET", "POST"])
        app.add_url_rule(f"{CHEMIN_MCP}/oauth/jeton", "mcp_jeton", obtenir_jeton, methods=["POST", "OPTIONS"])
        app.add_url_rule(f"{CHEMIN_MCP}/oauth/deconnecter", "mcp_deconnecter", deconnecter, methods=["POST"])

    def mcp_requis(f: Callable):
        """Pour la route /api/mcp : jeton OAuth du guichet (Claude) ou jeton de machine du .env. Sinon 401."""
        @wraps(f)
        def emballe(*a, **kw):
            if request.method == "OPTIONS":
                from flask import Response
                return Response(status=204, headers=CORS)
            u = verifier_jeton_mcp(_base(), request.headers.get("Authorization", ""))
            if u is None and machine() is None:
                return _flask(refus_mcp(_base(), "authorization" in {k.lower() for k in request.headers.keys()}))
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
