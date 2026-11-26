"""Le hub : la page d'accueil de tous les sites, derrière Authentik."""

from __future__ import annotations

import logging
import os
import subprocess

from flask import Flask, request

from . import db
from .config import Config

VERSION = "1.0"


def _version() -> str:
    v = os.environ.get("HUB_VERSION", "").strip()
    if v:
        return v
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL,
                                       cwd=os.path.dirname(__file__), timeout=2).decode().strip()
    except (OSError, subprocess.SubprocessError):
        return VERSION


MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def date_fr(valeur: str, heure: bool = False) -> str:
    """« 2026-11-26 14:02:00 » (UTC) → « 26 nov. 2026 » (+ « à 15 h 02 », heure de Paris)."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    try:
        d = datetime.fromisoformat(str(valeur).replace("Z", "+00:00"))
    except ValueError:
        return str(valeur)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    d = d.astimezone(ZoneInfo(os.environ.get("TZ") or "Europe/Paris"))
    texte = f"{d.day} {MOIS[d.month - 1]} {d.year}"
    if heure:
        texte += f" à {d.hour} h {d.minute:02d}"
    return texte


def create_app(config: Config | None = None) -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    app = Flask(__name__)
    cfg = config or Config()
    app.config["HUB"] = cfg
    app.config["SECRET_KEY"] = cfg.SECRET
    app.config["VERSION"] = _version()
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.teardown_appcontext(db.fermer_db)

    with app.app_context():
        db.init_db()

    from .routes import bp
    app.register_blueprint(bp)
    app.add_template_filter(date_fr, "date_fr")

    @app.after_request
    def entetes(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if request.path.startswith("/api/") or request.path.startswith("/admin") or request.path == "/":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app
