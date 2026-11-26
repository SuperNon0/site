"""Envoi des e-mails du hub (par le SMTP de Brevo, comme Authentik)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from flask import current_app, render_template

log = logging.getLogger(__name__)


class MailErreur(Exception):
    pass


def envoyer(destinataire: str, sujet: str, gabarit: str, **contexte) -> None:
    """Envoie un e-mail HTML + texte. Lève MailErreur si ça échoue."""
    cfg = current_app.config["HUB"]
    if not cfg.smtp_pret:
        raise MailErreur("Les e-mails ne sont pas configurés (SMTP_HOST / SMTP_FROM vides).")
    contexte.setdefault("nom_site", cfg.NOM_SITE)
    contexte.setdefault("hub_url", cfg.HUB_URL)
    texte = render_template(f"mails/{gabarit}.txt", **contexte)
    html = render_template(f"mails/{gabarit}.html", **contexte)

    nom, adresse = parseaddr(cfg.SMTP_FROM)
    msg = EmailMessage()
    msg["Subject"] = sujet
    msg["From"] = formataddr((nom or cfg.NOM_SITE, adresse))
    msg["To"] = destinataire
    msg["Message-ID"] = make_msgid(domain=adresse.split("@")[-1] or None)
    msg.set_content(texte)
    msg.add_alternative(html, subtype="html")

    try:
        contexte_ssl = ssl.create_default_context()
        if cfg.SMTP_SSL:
            serveur = smtplib.SMTP_SSL(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=20, context=contexte_ssl)
        else:
            serveur = smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=20)
        with serveur:
            if cfg.SMTP_TLS and not cfg.SMTP_SSL:
                serveur.starttls(context=contexte_ssl)
            if cfg.SMTP_USER:
                serveur.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            serveur.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("E-mail vers %s pas envoyé : %s", destinataire, exc)
        raise MailErreur(f"L'e-mail n'est pas parti ({exc.__class__.__name__}).") from exc
