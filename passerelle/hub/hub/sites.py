"""Les sites du hub : validation du formulaire et synchronisation avec Authentik.

Un site « protégé » = dans Authentik un fournisseur Proxy (adresse publique →
adresse interne du LXC) + une application, ajoutés à l'avant-poste intégré.
Un site « lien » = un simple bouton vers une adresse, sans Authentik.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

from .authentik import Authentik, AuthentikErreur

# Chemins du hub ouverts sans connexion (le formulaire de demande, le webhook
# BotPanel, les fichiers du design, l'installation sur téléphone).
SKIP_HUB = "\n".join([
    r"^/demande(/.*)?$",
    r"^/static/.*$",
    r"^/api/botpanel/.*$",
    r"^/manifest\.webmanifest$",
    r"^/sw\.js$",
    r"^/sante$",
])

# Le MCP et son guichet OAuth (docs/mcp-oauth.md). « $ » à la fin = ce chemin exactement, sans ce qui est dessous :
# /api/mcp/oauth/autoriser reste derrière Authentik, c'est là que la personne se connecte.
CHEMINS_PUBLICS_DEFAUT = ("/api/notify\n/api/mcp$\n/api/mcp/oauth/inscription\n/api/mcp/oauth/jeton\n"
                          "/.well-known/oauth-protected-resource\n/.well-known/oauth-authorization-server")

SOUS_DOMAINES_RESERVES = {"auth", "www", "hub"}

# Icônes proposées pour un site (celles du sprite du design « encre »).
ICONES = [
    "compass", "home", "lib", "film", "tv", "chart", "bell", "chat", "cal", "cloud",
    "shield", "key", "lock", "wrench", "gear", "plug", "zap", "layers", "grid", "list",
    "users", "user", "mail", "heart", "star", "bookmark", "ticket", "dice", "play",
    "download", "upload", "share", "signal", "wifi", "hash", "palette", "type", "sparkle",
    "eye", "clock",
]

RE_SOUS_DOMAINE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RE_CIBLE = re.compile(r"^(?:(https?)://)?([a-zA-Z0-9.-]+|\[[0-9a-fA-F:]+\]):(\d{1,5})/?$")
MANAGED_PROXY = [
    "goauthentik.io/providers/oauth2/scope-openid",
    "goauthentik.io/providers/oauth2/scope-email",
    "goauthentik.io/providers/oauth2/scope-profile",
    "goauthentik.io/providers/proxy/scope-proxy",
]


@dataclass
class Site:
    nom: str = ""
    sous_domaine: str = ""
    cible: str = ""
    lien: str = ""
    description: str = ""
    icone: str = "compass"
    categorie: str = ""
    acces: str = "membres"
    groupes: str = ""          # « famille,amis » : réservé à ces groupes Authentik (et aux admins)
    chemins_publics: str = CHEMINS_PUBLICS_DEFAUT
    type: str = "protege"
    ordre: int = 0
    erreurs: dict = field(default_factory=dict)

    @classmethod
    def depuis_ligne(cls, ligne) -> "Site":
        return cls(**{k: ligne[k] for k in (
            "nom", "sous_domaine", "cible", "lien", "description", "icone",
            "categorie", "acces", "groupes", "chemins_publics", "type", "ordre")})

    @property
    def liste_groupes(self) -> list[str]:
        return liste_groupes(self.groupes)


def liste_groupes(texte: str) -> list[str]:
    """« famille, amis » → ["famille", "amis"] (sans doublons, dans l'ordre)."""
    vus: list[str] = []
    for g in re.split(r"[,\n]", texte or ""):
        g = g.strip()
        if g and g not in vus:
            vus.append(g)
    return vus


def site_visible(ligne, v) -> bool:
    """Le hub ne montre à chacun que les sites qu'Authentik le laissera ouvrir."""
    if v.admin:
        return True
    if ligne["acces"] == "admins":
        return False
    groupes = liste_groupes(ligne["groupes"])
    return not groupes or any(g in v.groupes for g in groupes)


def normaliser_cible(texte: str) -> str | None:
    """« 192.168.1.20:8080 » → « http://192.168.1.20:8080 ». None si invalide."""
    texte = (texte or "").strip()
    m = RE_CIBLE.match(texte)
    if not m:
        return None
    schema, hote, port = m.group(1) or "http", m.group(2), int(m.group(3))
    if not 1 <= port <= 65535:
        return None
    return f"{schema}://{hote}:{port}"


def lire_formulaire(form, domaine: str) -> Site:
    """Valide le formulaire « Ajouter / modifier un site »."""
    s = Site(
        nom=(form.get("nom") or "").strip()[:60],
        sous_domaine=(form.get("sous_domaine") or "").strip().lower().removesuffix("." + domaine),
        cible=(form.get("cible") or "").strip(),
        lien=(form.get("lien") or "").strip(),
        description=(form.get("description") or "").strip()[:120],
        icone=(form.get("icone") or "compass").strip(),
        categorie=(form.get("categorie") or "").strip()[:40],
        acces="admins" if form.get("acces") == "admins" else "membres",
        groupes=(form.get("groupes") or "").strip(),
        chemins_publics=(form.get("chemins_publics") or "").strip(),
        type="lien" if form.get("type") == "lien" else "protege",
    )
    try:
        s.ordre = int(form.get("ordre") or 0)
    except ValueError:
        s.ordre = 0
    if not s.nom:
        s.erreurs["nom"] = "Donne un nom au site."
    if s.icone not in ICONES:
        s.icone = "compass"

    groupes = liste_groupes(s.groupes)
    if s.acces == "admins":
        groupes = []
    elif len(groupes) > 10 or any(len(g) > 60 or "|" in g for g in groupes):
        s.erreurs["groupes"] = "Jusqu'à 10 noms de groupes, séparés par des virgules (ex. : famille, amis)."
    s.groupes = ", ".join(groupes)

    if s.type == "protege":
        if not RE_SOUS_DOMAINE.match(s.sous_domaine):
            s.erreurs["sous_domaine"] = "Lettres minuscules, chiffres et tirets seulement (ex. : recettes)."
        elif s.sous_domaine in SOUS_DOMAINES_RESERVES:
            s.erreurs["sous_domaine"] = f"« {s.sous_domaine} » est réservé, choisis un autre nom."
        cible = normaliser_cible(s.cible)
        if not cible:
            s.erreurs["cible"] = "Écris l'adresse IP et le port du site, par exemple 192.168.1.20:8080."
        else:
            s.cible = cible
            hote = cible.split("://", 1)[1].rsplit(":", 1)[0]
            if hote in ("server", "hub", "postgresql", "worker", "localhost", "127.0.0.1"):
                s.erreurs["cible"] = "Cette adresse pointe vers la passerelle elle-même : mets l'IP du LXC du site."
        s.lien = f"https://{s.sous_domaine}.{domaine}" if not s.erreurs.get("sous_domaine") else ""
        chemins = []
        for ligne in s.chemins_publics.splitlines():
            ligne = ligne.strip()
            if not ligne:
                continue
            if not ligne.startswith("/") or " " in ligne or "$" in ligne[:-1]:
                s.erreurs["chemins_publics"] = "Un chemin par ligne, qui commence par « / » (ex. : /api/notify, ou /api/mcp$ pour ce chemin seul)."
                break
            chemins.append(ligne)
        s.chemins_publics = "\n".join(chemins)
    else:
        s.cible, s.chemins_publics, s.acces, s.groupes = "", "", "membres", ""
        s.erreurs.pop("groupes", None)
        if not re.match(r"^https?://[^\s]+$", s.lien):
            s.erreurs["lien"] = "Écris une adresse complète, qui commence par https://"
        s.sous_domaine = s.sous_domaine or ""
    return s


def regex_chemins(chemins_publics: str) -> str:
    """« /api/notify » → « ^/api/notify(/.*)?$ » (le chemin et tout ce qui est dessous).

    « /api/mcp$ » → « ^/api/mcp$ » (ce chemin seulement).
    """
    lignes = []
    for ch in (chemins_publics or "").splitlines():
        ch = ch.strip()
        exact = ch.endswith("$")
        ch = ch.rstrip("$").rstrip("/")
        if ch:
            lignes.append("^" + re.escape(ch) + ("$" if exact else "(/.*)?$"))
    return "\n".join(lignes)


def est_ip_privee(cible: str) -> bool:
    try:
        hote = cible.split("://", 1)[1].rsplit(":", 1)[0].strip("[]")
        return ipaddress.ip_address(hote).is_private
    except ValueError:
        return False


# ---------------------------------------------------------------- Authentik
MAPPING_SECRET = "Passerelle : en-tête secret"


def mappings_proxy(ak: Authentik) -> list[str]:
    """openid, email, profile + notre « ak_proxy » (qui ajoute X-Passerelle-Cle).

    Notre mapping remplace celui d'Authentik pour « ak_proxy » (il en reprend le contenu).
    """
    pks = []
    secret = ak.premier("/propertymappings/provider/scope/", name=MAPPING_SECRET)
    for managed in MANAGED_PROXY:
        if secret and managed.endswith("/scope-proxy"):
            continue
        m = ak.premier("/propertymappings/provider/scope/", managed=managed)
        if m:
            pks.append(m["pk"])
    if secret:
        pks.append(secret["pk"])
    return pks


def slug_app(sous_domaine: str) -> str:
    return f"site-{sous_domaine}"


class Synchro:
    """Crée, modifie et supprime un site dans Authentik."""

    def __init__(self, ak: Authentik, domaine: str, groupe_admins: str, duree_session: str):
        self.ak = ak
        self.domaine = domaine
        self.groupe_admins = groupe_admins
        self.duree_session = duree_session

    def _provider_donnees(self, s: Site) -> dict:
        return {
            "name": f"Site : {s.nom} ({s.sous_domaine})",
            "mode": "proxy",
            "external_host": f"https://{s.sous_domaine}.{self.domaine}",
            "internal_host": s.cible,
            "internal_host_ssl_validation": False,
            "skip_path_regex": regex_chemins(s.chemins_publics),
            "access_token_validity": self.duree_session,
            "intercept_header_auth": True,
            "property_mappings": mappings_proxy(self.ak),
        }

    def _app_donnees(self, s: Site, provider_pk: int) -> dict:
        return {
            "name": s.nom,
            "slug": slug_app(s.sous_domaine),
            "provider": provider_pk,
            "meta_launch_url": f"https://{s.sous_domaine}.{self.domaine}",
            "meta_description": s.description,
            "group": s.categorie,
            "policy_engine_mode": "any",
            "open_in_new_tab": False,
        }

    def _regles(self, app_pk: str, s: Site) -> None:
        """Qui peut ouvrir le site, en règles de groupe sur l'application Authentik.

        Aucune règle de groupe = tout compte connecté entre. « admins » = le groupe des admins.
        Des groupes = ces groupes ET les admins. Les groupes doivent déjà exister dans
        Authentik (le compte « hub » n'a pas le droit d'en créer). Les règles sur une
        personne précise, posées à la main dans Authentik, ne sont pas touchées.
        """
        voulus = [self.groupe_admins] if s.acces == "admins" else (
            [self.groupe_admins, *s.liste_groupes] if s.liste_groupes else [])
        pks = []
        for nom in voulus:
            g = self.ak.groupe(nom)
            if not g:
                raise AuthentikErreur(f"Groupe « {nom} » introuvable dans Authentik : crée-le d'abord "
                                      "(Authentik → Annuaire → Groupes), avec exactement ce nom.", 422)
            pks.append(g["pk"])
        liens = [b for b in self.ak.liste("/policies/bindings/", target=app_pk) if b["target"] == app_pk]
        presents = {b["group"] for b in liens if b.get("group")}
        for ordre, pk in enumerate(pks):
            if pk not in presents:
                self.ak.post("/policies/bindings/", {
                    "target": app_pk, "group": pk, "order": ordre, "enabled": True,
                    "negate": False, "timeout": 30,
                })
        for b in liens:
            if b.get("group") and b["group"] not in pks:
                self.ak.delete(f"/policies/bindings/{b['pk']}/")

    def creer(self, s: Site) -> tuple[int, str]:
        provider = None
        try:
            donnees = self._provider_donnees(s) | {
                "authorization_flow": self.ak.flux_pk("default-provider-authorization-implicit-consent"),
                "invalidation_flow": self.ak.flux_pk("default-provider-invalidation-flow"),
            }
            provider = self.ak.post("/providers/proxy/", donnees)
            app = self.ak.post("/core/applications/", self._app_donnees(s, provider["pk"]))
            self._regles(app["pk"], s)
            self.ak.avant_poste_ajouter(provider["pk"])
            return provider["pk"], app["slug"]
        except AuthentikErreur:
            # On défait ce qui a été créé à moitié.
            if provider:
                self._supprimer_tout(provider["pk"], slug_app(s.sous_domaine))
            raise

    def modifier(self, ancien_provider: int | None, ancien_slug: str | None, s: Site) -> tuple[int, str]:
        if not ancien_provider or not self.existe(ancien_provider):
            if ancien_provider or ancien_slug:
                self._supprimer_tout(ancien_provider, ancien_slug)
            return self.creer(s)
        self.ak.patch(f"/providers/proxy/{ancien_provider}/", self._provider_donnees(s))
        nouveau_slug = slug_app(s.sous_domaine)
        app = None
        if ancien_slug:
            try:
                app = self.ak.patch(f"/core/applications/{ancien_slug}/", self._app_donnees(s, ancien_provider))
            except AuthentikErreur as exc:
                if exc.statut != 404:
                    raise
        if app is None:
            app = self.ak.post("/core/applications/", self._app_donnees(s, ancien_provider))
        self._regles(app["pk"], s)
        self.ak.avant_poste_ajouter(ancien_provider)
        return ancien_provider, nouveau_slug

    def supprimer(self, provider_pk: int | None, app_slug: str | None) -> None:
        self._supprimer_tout(provider_pk, app_slug)

    def _supprimer_tout(self, provider_pk, app_slug) -> None:
        if provider_pk:
            try:
                self.ak.avant_poste_retirer(provider_pk)
            except AuthentikErreur:
                pass
        if app_slug:
            try:
                self.ak.delete(f"/core/applications/{app_slug}/")
            except AuthentikErreur as exc:
                if exc.statut != 404:
                    raise
        if provider_pk:
            try:
                self.ak.delete(f"/providers/proxy/{provider_pk}/")
            except AuthentikErreur as exc:
                if exc.statut != 404:
                    raise

    def existe(self, provider_pk: int) -> bool:
        try:
            self.ak.get(f"/providers/proxy/{provider_pk}/")
            return True
        except AuthentikErreur as exc:
            if exc.statut == 404:
                return False
            raise

    def etat(self) -> dict[int, bool]:
        """provider_pk → présent dans l'avant-poste."""
        op = self.ak.avant_poste_integre()
        return {pk: True for pk in op.get("providers", [])}
