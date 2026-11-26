"""Réglages du hub, lus dans les variables d'environnement (.env de docker compose)."""

from __future__ import annotations

import os
from pathlib import Path


def _b(nom: str, defaut: bool = False) -> bool:
    v = os.environ.get(nom)
    if v is None:
        return defaut
    return v.strip().lower() in ("1", "true", "oui", "yes", "on")


def _s(nom: str, defaut: str = "") -> str:
    return os.environ.get(nom, defaut).strip()


class Config:
    def __init__(self) -> None:
        # Domaine principal : le hub est sur https://DOMAINE, Authentik sur https://auth.DOMAINE
        self.DOMAINE = _s("DOMAINE", "super-nono.cc").lower()
        self.AUTH_HOTE = _s("AUTH_HOTE", f"auth.{self.DOMAINE}")
        self.AUTH_URL = _s("AUTH_URL", f"https://{self.AUTH_HOTE}").rstrip("/")
        self.HUB_URL = _s("HUB_URL", f"https://{self.DOMAINE}").rstrip("/")
        self.HUB_INTERNE = _s("HUB_INTERNE", "http://hub:8000")
        self.NOM_SITE = _s("NOM_SITE", "Super Nono")

        self.AUTHENTIK_URL_INTERNE = _s("AUTHENTIK_URL_INTERNE", "http://server:9000")
        self.DATA_DIR = _s("DATA_DIR", "/data")
        self.GROUPE_ADMINS = _s("GROUPE_ADMINS", "authentik Admins")
        self.GROUPE_MEMBRES = _s("GROUPE_MEMBRES", "Membres")
        self.DUREE_SESSION = _s("DUREE_SESSION", "days=30")
        self.INVITATION_JOURS = int(_s("INVITATION_JOURS", "7") or 7)

        # Secret que seul Authentik envoie au hub (en-tête X-Hub-Cle).
        self.HUB_CLE_PROXY = _s("HUB_CLE_PROXY")

        self.ADMIN_EMAIL = _s("ADMIN_EMAIL")
        self.ADMIN_NOM = _s("ADMIN_NOM")
        self.GOOGLE_CLIENT_ID = _s("GOOGLE_CLIENT_ID")
        self.GOOGLE_CLIENT_SECRET = _s("GOOGLE_CLIENT_SECRET")

        # E-mails (Brevo) : les mêmes réglages servent à Authentik.
        self.SMTP_HOST = _s("SMTP_HOST")
        self.SMTP_PORT = int(_s("SMTP_PORT", "587") or 587)
        self.SMTP_USER = _s("SMTP_USER")
        self.SMTP_PASSWORD = _s("SMTP_PASSWORD")
        self.SMTP_FROM = _s("SMTP_FROM")
        self.SMTP_TLS = _b("SMTP_TLS", True)
        self.SMTP_SSL = _b("SMTP_SSL", False)

        # BotPanel
        self.BOTPANEL_URL = _s("BOTPANEL_URL").rstrip("/")
        self.BOTPANEL_SLUG_DEMANDE = _s("BOTPANEL_SLUG_DEMANDE", "hub_demande")
        self.BOTPANEL_WEBHOOK_SECRET = _s("BOTPANEL_WEBHOOK_SECRET")

        # Développement : faux visiteur sans Authentik (jamais en production).
        self.DEV = _b("HUB_DEV")
        self.DEV_EMAIL = _s("HUB_DEV_EMAIL", "noe@exemple.fr")
        self.DEV_ADMIN = _b("HUB_DEV_ADMIN", True)

        self.SECRET = _s("HUB_SECRET") or self.HUB_CLE_PROXY or "dev-seulement"

    # Jeton du compte de service : variable d'environnement, sinon fichier écrit par configurer.py
    @property
    def AUTHENTIK_TOKEN(self) -> str:
        jeton = _s("AUTHENTIK_TOKEN")
        if jeton:
            return jeton
        f = Path(self.DATA_DIR) / "authentik-token"
        try:
            return f.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @property
    def smtp_pret(self) -> bool:
        return bool(self.SMTP_HOST and self.SMTP_FROM)

    @property
    def lien_admin_authentik(self) -> str:
        return f"{self.AUTH_URL}/if/admin/"

    @property
    def lien_mon_compte(self) -> str:
        return f"{self.AUTH_URL}/if/user/"
