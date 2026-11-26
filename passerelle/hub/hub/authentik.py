"""Petit client de l'API d'Authentik (v3), limité à ce dont le hub a besoin.

Le hub parle à Authentik par le réseau interne de docker
(`http://server:9000`), avec le jeton du compte de service « hub »
(droits limités : sites, invitations, comptes ; voir configurer.py).
"""

from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)

EMBEDDED_OUTPOST = "goauthentik.io/outposts/embedded"


class AuthentikErreur(Exception):
    """Erreur lisible renvoyée par Authentik (ou Authentik injoignable)."""

    def __init__(self, message: str, statut: int | None = None, details: Any = None):
        super().__init__(message)
        self.statut = statut
        self.details = details


class Authentik:
    def __init__(self, url: str, jeton: str, timeout: float = 15):
        self.url = url.rstrip("/") + "/api/v3"
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {jeton}", "Accept": "application/json"})
        # Le hub parle à Authentik sur le réseau interne : jamais par un proxy.
        self.s.trust_env = False

    # ------------------------------------------------------------ bas niveau
    def req(self, methode: str, chemin: str, **kw) -> Any:
        try:
            r = self.s.request(methode, self.url + chemin, timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise AuthentikErreur("Authentik ne répond pas. Réessaie dans un instant.") from exc
        if r.status_code == 204 or not r.content:
            if r.status_code >= 400:
                raise AuthentikErreur(f"Authentik a répondu {r.status_code}.", r.status_code)
            return None
        try:
            data = r.json()
        except ValueError:
            data = r.text
        if r.status_code >= 400:
            raise AuthentikErreur(_message(data, r.status_code), r.status_code, data)
        return data

    def get(self, chemin, **params):
        return self.req("GET", chemin, params=params or None)

    def post(self, chemin, data=None):
        return self.req("POST", chemin, json=data or {})

    def patch(self, chemin, data):
        return self.req("PATCH", chemin, json=data)

    def put(self, chemin, data):
        return self.req("PUT", chemin, json=data)

    def delete(self, chemin):
        return self.req("DELETE", chemin)

    def liste(self, chemin, **params) -> list[dict]:
        """Toutes les pages d'une liste."""
        params.setdefault("page_size", 100)
        page, out = 1, []
        while True:
            data = self.get(chemin, page=page, **params)
            out.extend(data.get("results", []))
            if not data.get("pagination", {}).get("next"):
                return out
            page += 1

    def premier(self, chemin, **params) -> dict | None:
        res = self.get(chemin, page_size=1, **params).get("results", [])
        return res[0] if res else None

    # ------------------------------------------------------------ santé
    def joignable(self) -> bool:
        try:
            r = self.s.get(self.url.removesuffix("/api/v3") + "/-/health/ready/", timeout=4)
            return r.status_code == 200
        except requests.RequestException:
            return False

    # ------------------------------------------------------------ flux
    def flux_pk(self, slug: str) -> str:
        return self.get(f"/flows/instances/{slug}/")["pk"]

    # ------------------------------------------------------------ groupes
    def groupe(self, nom: str) -> dict | None:
        return self.premier("/core/groups/", name=nom)

    # ------------------------------------------------------------ avant-poste
    def avant_poste_integre(self) -> dict:
        op = self.premier("/outposts/instances/", managed__iexact=EMBEDDED_OUTPOST)
        if not op:
            raise AuthentikErreur("Avant-poste intégré d'Authentik introuvable.")
        return op

    def avant_poste_ajouter(self, provider_pk: int) -> None:
        op = self.avant_poste_integre()
        providers = list(op.get("providers", []))
        if provider_pk not in providers:
            providers.append(provider_pk)
            self.patch(f"/outposts/instances/{op['pk']}/", {"providers": providers})

    def avant_poste_retirer(self, provider_pk: int) -> None:
        op = self.avant_poste_integre()
        providers = [p for p in op.get("providers", []) if p != provider_pk]
        if providers != op.get("providers", []):
            self.patch(f"/outposts/instances/{op['pk']}/", {"providers": providers})


def _message(data: Any, statut: int) -> str:
    """Transforme la réponse d'erreur d'Authentik en une phrase."""
    if statut == 403:
        return "Le hub n'a pas le droit de faire ça dans Authentik (jeton ou droits du compte « hub »)."
    if statut == 404:
        return "Élément introuvable dans Authentik."
    if isinstance(data, dict):
        morceaux = []
        for cle, val in data.items():
            if isinstance(val, list):
                val = " ".join(str(v) for v in val)
            morceaux.append(f"{cle} : {val}" if cle not in ("detail", "non_field_errors") else str(val))
        if morceaux:
            return "Authentik : " + " ; ".join(morceaux)
    return f"Authentik a répondu {statut}."
