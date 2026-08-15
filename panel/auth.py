"""Authentification : Cloudflare Zero Trust (Access) + login local + décorateurs.

Deux couches, conformes à docs/authentification-v2.md §1 et §9 :
  - Cloudflare Access = portier e-mail (qui peut *frapper à la porte*).
  - L'application       = rôles + cycle de vie (ce qui se passe *après* la porte).

⚠️ Sécurité (spec §9.1) : ne JAMAIS faire confiance à l'en-tête
`Cf-Access-Authenticated-User-Email` si l'origine est joignable hors Cloudflare.
On vérifie donc le JWT `Cf-Access-Jwt-Assertion` contre les clés publiques de
l'équipe Cloudflare et on contrôle l'`aud`. Rends aussi l'origine injoignable
sans Cloudflare (tunnel cloudflared / pare-feu IP Cloudflare).
"""

from __future__ import annotations

import functools
import time

from flask import (current_app, flash, g, redirect, request, session, url_for)

from .db import get_db

# PyJWT est optionnel au démarrage : on n'exige la lib que si CF_VERIFY_JWT.
try:
    import jwt
    from jwt import PyJWKClient
except Exception:  # pragma: no cover
    jwt = None
    PyJWKClient = None

_jwk_clients: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# Cloudflare Access
# ─────────────────────────────────────────────────────────────────────────────
def _get_jwk_client(team: str) -> "PyJWKClient | None":
    if not team or PyJWKClient is None:
        return None
    if team not in _jwk_clients:
        certs_url = f"https://{team}.cloudflareaccess.com/cdn-cgi/access/certs"
        _jwk_clients[team] = PyJWKClient(certs_url)
    return _jwk_clients[team]


def reset_jwk_cache() -> None:
    """À appeler quand la config Cloudflare change (via les Paramètres)."""
    _jwk_clients.clear()


def cf_access_email() -> str | None:
    """E-mail Cloudflare vérifié pour la requête courante, sinon None.

    La configuration Cloudflare (équipe, aud, vérification) vient d'abord des
    Paramètres (base), sinon du `.env`.

    - Si vérification active (défaut) : valide le JWT et l'`aud`, renvoie
      l'e-mail contenu dans le token. Un en-tête forgé sans JWT valide est ignoré.
    - Si vérification désactivée (origine déjà rendue injoignable sans CF) :
      se contente de l'en-tête `Cf-Access-Authenticated-User-Email`.
    """
    from .settings import cf_config
    cfg = cf_config()

    header_email = request.headers.get("Cf-Access-Authenticated-User-Email")
    token = request.headers.get("Cf-Access-Jwt-Assertion")

    if not cfg["verify"]:
        return header_email.strip().lower() if header_email else None

    if not token or jwt is None:
        return None

    team, aud = cfg["team"], cfg["aud"]
    client = _get_jwk_client(team)
    if client is None or not aud or not team:
        current_app.logger.warning(
            "Vérification JWT active mais équipe Cloudflare / aud manquants."
        )
        return None

    try:
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=aud,
            issuer=f"https://{team}.cloudflareaccess.com",
        )
    except Exception as exc:  # signature invalide, expiré, aud/iss faux…
        current_app.logger.warning("JWT Cloudflare rejeté : %s", exc)
        return None

    email = (claims.get("email") or "").strip().lower()
    return email or None


# ─────────────────────────────────────────────────────────────────────────────
# Comptes / session
# ─────────────────────────────────────────────────────────────────────────────
def get_compte(compte_id: int):
    if compte_id is None:
        return None
    return get_db().execute("SELECT * FROM comptes WHERE id = ?", (compte_id,)).fetchone()


def current_compte():
    """Compte *effectif* (celui impersonné le cas échéant), attaché à g."""
    if "compte" not in g:
        g.compte = get_compte(session.get("compte_id"))
    return g.compte


def is_super_admin() -> bool:
    # Le rôle réel est celui de l'impersonateur s'il y en a un, sinon du compte.
    if session.get("impersonator_id"):
        imp = get_compte(session["impersonator_id"])
        return bool(imp and imp["role"] == "super_admin")
    c = current_compte()
    return bool(c and c["role"] == "super_admin")


def login_compte(compte_row) -> None:
    """Ouvre la session pour un compte actif et note la dernière connexion."""
    session.clear()
    session["compte_id"] = compte_row["id"]
    session["role"] = compte_row["role"]
    get_db().execute(
        "UPDATE comptes SET derniere_cnx = ? WHERE id = ?",
        (int(time.time()), compte_row["id"]),
    )
    get_db().commit()


# ─────────────────────────────────────────────────────────────────────────────
# Décorateurs
# ─────────────────────────────────────────────────────────────────────────────
def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        c = current_compte()
        if c is None or c["etat"] != "actif":
            return redirect(url_for("auth.gateway"))
        return view(*args, **kwargs)

    return wrapped


def super_admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_compte() is None:
            return redirect(url_for("auth.gateway"))
        if not is_super_admin():
            flash("Réservé au super-admin.", "error")
            return redirect(url_for("main.dashboard"))
        return view(*args, **kwargs)

    return wrapped
