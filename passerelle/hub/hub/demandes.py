"""Demandes d'accès : formulaire public → BotPanel → Accepter / Refuser.

Accepter = créer une invitation Authentik (valable 7 jours, effacée une fois le compte créé) et envoyer
par e-mail le lien où la personne choisit son mot de passe. Son compte est
créé à ce moment-là, dans le groupe « Membres ».
Refuser = la demande reste en base, marquée refusée : si la personne revient,
elle voit « Accès refusé » (et ne peut pas redemander).
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import current_app

from .authentik import Authentik, AuthentikErreur
from .botpanel import notifier
from .configurer import SLUG_INVITATION
from .db import get_db, journaliser
from .mail import MailErreur, envoyer

log = logging.getLogger(__name__)

RE_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[a-zA-Z]{2,}$")
MAX_PAR_HEURE_ET_IP = 5


@dataclass
class Resultat:
    ok: bool
    message: str
    lien: str = ""


def cfg():
    return current_app.config["HUB"]


def ak() -> Authentik:
    return Authentik(cfg().AUTHENTIK_URL_INTERNE, cfg().AUTHENTIK_TOKEN)


def valider(form) -> tuple[dict, dict]:
    donnees = {
        "email": (form.get("email") or "").strip().lower()[:254],
        "prenom": (form.get("prenom") or "").strip()[:60],
        "nom": (form.get("nom") or "").strip()[:60],
        "message": (form.get("message") or "").strip()[:500],
    }
    erreurs = {}
    if not RE_EMAIL.match(donnees["email"]):
        erreurs["email"] = "Cette adresse e-mail ne semble pas valide."
    if not donnees["prenom"]:
        erreurs["prenom"] = "Écris ton prénom."
    if not donnees["nom"]:
        erreurs["nom"] = "Écris ton nom."
    return donnees, erreurs


def par_email(email: str):
    return get_db().execute("SELECT * FROM demandes WHERE email = ?", (email.lower(),)).fetchone()


def par_id(demande_id: int):
    return get_db().execute("SELECT * FROM demandes WHERE id = ?", (demande_id,)).fetchone()


def par_jeton(jeton: str):
    return get_db().execute("SELECT * FROM demandes WHERE jeton_suivi = ?", (jeton,)).fetchone()


def compte_existe(email: str) -> bool:
    try:
        return ak().premier("/core/users/", email=email) is not None
    except AuthentikErreur:
        return False


def trop_de_demandes(ip: str) -> bool:
    if not ip:
        return False
    n = get_db().execute(
        "SELECT COUNT(*) FROM demandes WHERE ip = ? AND cree_le > datetime('now', '-1 hour')", (ip,)
    ).fetchone()[0]
    return n >= MAX_PAR_HEURE_ET_IP


def soumettre(donnees: dict, ip: str) -> tuple[str, object]:
    """Renvoie (etat, demande) avec etat : nouvelle | en_attente | refusee | acceptee | inscrite | compte."""
    existante = par_email(donnees["email"])
    if existante:
        return existante["statut"], existante
    if compte_existe(donnees["email"]):
        return "compte", None

    db = get_db()
    jeton = secrets.token_urlsafe(24)
    cur = db.execute(
        "INSERT INTO demandes (email, prenom, nom, message, jeton_suivi, ip) VALUES (?, ?, ?, ?, ?, ?)",
        (donnees["email"], donnees["prenom"], donnees["nom"], donnees["message"], jeton, ip),
    )
    db.commit()
    demande = par_id(cur.lastrowid)
    journaliser(demande["email"], "a demandé un accès")

    c = cfg()
    notifier(
        c.BOTPANEL_SLUG_DEMANDE,
        id=demande["id"], email=demande["email"], prenom=demande["prenom"], nom=demande["nom"],
        nom_complet=f"{demande['prenom']} {demande['nom']}", message=demande["message"] or "—",
        lien=f"{c.HUB_URL}/admin/demandes", site=c.NOM_SITE,
    )
    try:
        envoyer(demande["email"], f"{c.NOM_SITE} : ta demande est bien arrivée", "demande_recue",
                demande=demande, lien_suivi=f"{c.HUB_URL}/demande/suivi/{jeton}")
    except MailErreur as exc:
        log.info("Accusé de réception pas envoyé : %s", exc)
    return "nouvelle", demande


def _lien_invitation(pk: str) -> str:
    return f"{cfg().AUTH_URL}/if/flow/{SLUG_INVITATION}/?itoken={pk}"


def _creer_invitation(demande) -> tuple[str, str]:
    c = cfg()
    client = ak()
    flux = client.flux_pk(SLUG_INVITATION)
    expire = datetime.now(timezone.utc) + timedelta(days=c.INVITATION_JOURS)
    nom_complet = f"{demande['prenom']} {demande['nom']}".strip()
    inv = client.post("/stages/invitation/invitations/", {
        "name": f"hub-{demande['id']}-{secrets.token_hex(3)}",
        "expires": expire.isoformat(),
        # Pas « à usage unique » : Authentik l'efface dès qu'on ouvre le lien, et un onglet
        # fermé trop tôt le rendrait inutilisable. Le hub l'efface lui-même une fois le compte créé.
        "single_use": False,
        "flow": flux,
        "fixed_data": {"username": demande["email"], "email": demande["email"], "name": nom_complet},
    })
    return inv["pk"], expire.strftime("%Y-%m-%d %H:%M:%S")


def _supprimer_invitation(pk: str | None) -> None:
    if not pk:
        return
    try:
        ak().delete(f"/stages/invitation/invitations/{pk}/")
    except AuthentikErreur as exc:
        if exc.statut != 404:
            log.warning("Invitation %s pas supprimée : %s", pk, exc)


def _envoyer_invitation(demande, pk: str) -> Resultat:
    c = cfg()
    lien = _lien_invitation(pk)
    try:
        envoyer(demande["email"], f"{c.NOM_SITE} : ton accès est accepté", "invitation",
                demande=demande, lien=lien, jours=c.INVITATION_JOURS)
        return Resultat(True, f"Accès accordé à {demande['prenom']} : l'invitation est partie par e-mail.", lien)
    except MailErreur as exc:
        return Resultat(True, f"Accès accordé à {demande['prenom']}, mais l'e-mail n'est pas parti "
                              f"({exc}). Envoie-lui ce lien toi-même : {lien}", lien)


def accepter(demande_id: int, par: str) -> Resultat:
    d = par_id(demande_id)
    if not d:
        return Resultat(False, "Cette demande n'existe plus.")
    if d["statut"] in ("acceptee", "inscrite"):
        return Resultat(True, f"La demande de {d['prenom']} est déjà acceptée.")
    if compte_existe(d["email"]):
        _marquer(d["id"], "inscrite", par)
        return Resultat(True, f"{d['prenom']} a déjà un compte.")
    try:
        pk, expire = _creer_invitation(d)
    except AuthentikErreur as exc:
        return Resultat(False, f"Invitation impossible : {exc}")
    db = get_db()
    db.execute("UPDATE demandes SET statut='acceptee', invitation=?, invitation_expire=?, decide_par=?, "
               "decide_le=datetime('now') WHERE id=?", (pk, expire, par, d["id"]))
    db.commit()
    journaliser(par, f"a accepté {d['email']}")
    return _envoyer_invitation(par_id(d["id"]), pk)


def refuser(demande_id: int, par: str) -> Resultat:
    d = par_id(demande_id)
    if not d:
        return Resultat(False, "Cette demande n'existe plus.")
    if d["statut"] == "refusee":
        return Resultat(True, f"La demande de {d['prenom']} est déjà refusée.")
    if d["statut"] == "inscrite":
        return Resultat(False, f"{d['prenom']} a déjà créé son compte : bloque-le dans « Membres ».")
    _supprimer_invitation(d["invitation"])
    get_db().execute("UPDATE demandes SET invitation=NULL WHERE id=?", (d["id"],))
    _marquer(d["id"], "refusee", par)
    journaliser(par, f"a refusé {d['email']}")
    return Resultat(True, f"Demande de {d['prenom']} refusée.")


def renvoyer(demande_id: int, par: str) -> Resultat:
    """Nouvelle invitation (l'ancienne est annulée) et nouvel e-mail."""
    d = par_id(demande_id)
    if not d or d["statut"] != "acceptee":
        return Resultat(False, "Seule une demande acceptée peut recevoir une nouvelle invitation.")
    _supprimer_invitation(d["invitation"])
    try:
        pk, expire = _creer_invitation(d)
    except AuthentikErreur as exc:
        return Resultat(False, f"Invitation impossible : {exc}")
    db = get_db()
    db.execute("UPDATE demandes SET invitation=?, invitation_expire=? WHERE id=?", (pk, expire, d["id"]))
    db.commit()
    journaliser(par, f"a renvoyé l'invitation de {d['email']}")
    return _envoyer_invitation(par_id(d["id"]), pk)


def oublier(demande_id: int, par: str) -> Resultat:
    """Efface la demande : la personne pourra en refaire une."""
    d = par_id(demande_id)
    if not d:
        return Resultat(False, "Cette demande n'existe plus.")
    if d["statut"] == "acceptee":
        _supprimer_invitation(d["invitation"])
    db = get_db()
    db.execute("DELETE FROM demandes WHERE id=?", (d["id"],))
    db.commit()
    journaliser(par, f"a effacé la demande de {d['email']}")
    return Resultat(True, f"Demande de {d['prenom']} effacée.")


def _marquer(demande_id: int, statut: str, par: str) -> None:
    db = get_db()
    db.execute("UPDATE demandes SET statut=?, decide_par=?, decide_le=datetime('now') WHERE id=?",
               (statut, par, demande_id))
    db.commit()


def rafraichir_inscrites() -> None:
    """Passe en « inscrite » les demandes acceptées dont le compte existe maintenant."""
    db = get_db()
    acceptees = db.execute("SELECT id, email FROM demandes WHERE statut='acceptee'").fetchall()
    if not acceptees:
        return
    try:
        client = ak()
        for d in db.execute("SELECT id, email, invitation FROM demandes WHERE statut='acceptee'").fetchall():
            if client.premier("/core/users/", email=d["email"]):
                _supprimer_invitation(d["invitation"])
                db.execute("UPDATE demandes SET statut='inscrite', invitation=NULL WHERE id=?", (d["id"],))
        db.commit()
    except AuthentikErreur:
        pass


def lister(statut: str | None = None) -> list:
    db = get_db()
    if statut:
        return db.execute("SELECT * FROM demandes WHERE statut=? ORDER BY cree_le DESC", (statut,)).fetchall()
    return db.execute("SELECT * FROM demandes ORDER BY cree_le DESC").fetchall()


def compter_en_attente() -> int:
    return get_db().execute("SELECT COUNT(*) FROM demandes WHERE statut='en_attente'").fetchone()[0]
