"""BotPanel : notification Discord d'une demande d'accès, et webhook des boutons.

1. Le hub appelle  POST {BOTPANEL_URL}/api/notify  {"id": slug, "vars": {...}}.
2. Dans BotPanel, la notification a deux boutons « Webhook » (Accepter /
   Refuser) qui appellent  POST https://DOMAINE/api/botpanel/decision.
3. Le hub vérifie la signature HMAC (secret webhook de BotPanel) avant d'agir.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

import requests
from flask import current_app

log = logging.getLogger(__name__)

TOLERANCE_S = 300  # une signature de plus de 5 minutes est refusée


def notifier(slug: str, **variables) -> bool:
    cfg = current_app.config["HUB"]
    if not cfg.BOTPANEL_URL or not slug:
        log.info("BotPanel non configuré : notification %s ignorée", slug)
        return False
    try:
        r = requests.post(
            f"{cfg.BOTPANEL_URL}/api/notify",
            json={"id": slug, "vars": {k: str(v) for k, v in variables.items()}},
            timeout=8,
        )
        if r.status_code >= 300:
            log.warning("BotPanel %s → HTTP %s : %s", slug, r.status_code, r.text[:200])
            return False
        return True
    except requests.RequestException as exc:
        log.warning("BotPanel injoignable (%s) : %s", slug, exc)
        return False


def signature_valide(corps: bytes, horodatage: str, signature: str, secret: str,
                     maintenant: float | None = None) -> bool:
    if not (secret and horodatage and signature):
        return False
    try:
        ts = int(horodatage)
    except ValueError:
        return False
    if abs((maintenant or time.time()) - ts) > TOLERANCE_S:
        return False
    attendu = "sha256=" + hmac.new(secret.encode(), horodatage.encode() + b"." + corps,
                                   hashlib.sha256).hexdigest()
    return hmac.compare_digest(attendu, signature.strip())
