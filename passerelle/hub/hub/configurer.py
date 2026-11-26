"""Configuration d'Authentik pour le hub, en une commande.

    docker compose run --rm hub python -m hub.configurer

On peut le relancer autant de fois qu'on veut : chaque étape vérifie ce qui
existe déjà et ne fait que ce qui manque (ou remet le bon réglage).

Ce qu'il met en place :
  1. le groupe « Membres » ;
  2. le flux d'invitation (la personne acceptée choisit son mot de passe) ;
  3. le flux « Mot de passe oublié » (lien par e-mail) ;
  4. le flux « compte Google inconnu » → renvoie vers le formulaire de demande ;
  5. la page de connexion : e-mail + mot de passe sur la même page, Google,
     lien « Mot de passe oublié ? », lien « Demander un accès », style Encre ;
  6. la connexion Google (si GOOGLE_CLIENT_ID est rempli) ;
  7. le compte de service « hub » avec des droits limités, et son jeton ;
  8. l'application « Hub » sur le domaine principal ;
  9. ton compte administrateur (ADMIN_EMAIL). « akadmin » garde un mot de passe
     aléatoire que personne ne connaît : il sert seulement à relancer ce script.

Il lit le jeton de démarrage AUTHENTIK_BOOTSTRAP_TOKEN (administrateur). Le
jeton du hub est écrit dans /data/authentik-token (lu par le hub).
"""

from __future__ import annotations

import os
import secrets
import string
import sys
from pathlib import Path

from .authentik import Authentik, AuthentikErreur
from .config import Config
from .sites import MAPPING_SECRET, SKIP_HUB, mappings_proxy

# Droits du compte de service « hub » : rien d'autre.
PERMISSIONS_HUB = [
    # Sites : fournisseur proxy + application + règle d'accès par groupe
    "authentik_providers_proxy.add_proxyprovider",
    "authentik_providers_proxy.change_proxyprovider",
    "authentik_providers_proxy.delete_proxyprovider",
    "authentik_providers_proxy.view_proxyprovider",
    "authentik_core.add_application",
    "authentik_core.change_application",
    "authentik_core.delete_application",
    "authentik_core.view_application",
    "authentik_policies.add_policybinding",
    "authentik_policies.change_policybinding",
    "authentik_policies.delete_policybinding",
    "authentik_policies.view_policybinding",
    "authentik_outposts.view_outpost",
    "authentik_outposts.change_outpost",
    "authentik_flows.view_flow",
    "authentik_providers_oauth2.view_scopemapping",
    # Demandes d'accès : invitations, lecture des comptes et des groupes
    "authentik_stages_invitation.add_invitation",
    "authentik_stages_invitation.view_invitation",
    "authentik_stages_invitation.delete_invitation",
    "authentik_core.view_user",
    "authentik_core.change_user",
    "authentik_core.view_group",
]

NOM_ROLE = "Hub (compte de service)"
NOM_SA = "hub"
JETON_ID = "hub-api"

SLUG_INVITATION = "hub-invitation"
SLUG_RECUPERATION = "hub-recuperation"
SLUG_GOOGLE_INCONNU = "hub-google-sans-compte"



def etape(texte: str) -> None:
    print(f"\n→ {texte}", flush=True)


def ok(texte: str) -> None:
    print(f"  ✓ {texte}", flush=True)


class Configurateur:
    def __init__(self, ak: Authentik, cfg: Config):
        self.ak = ak
        self.cfg = cfg

    # ------------------------------------------------------------ utilitaires
    def assurer(self, chemin: str, filtre: dict, donnees: dict, cle: str = "pk") -> dict:
        """Crée l'objet s'il n'existe pas, sinon remet ses réglages."""
        existant = self.ak.premier(chemin, **filtre)
        if existant:
            return self.ak.patch(f"{chemin}{existant[cle]}/", donnees)
        return self.ak.post(chemin, donnees)

    def lier(self, flux_pk: str, etape_pk: str, ordre: int, **extra) -> None:
        """Place une étape dans un flux (une seule fois)."""
        liens = [b for b in self.ak.liste("/flows/bindings/", target=flux_pk) if b["target"] == flux_pk]
        for lien in liens:
            if lien["stage"] == etape_pk:
                if lien["order"] != ordre:
                    self.ak.patch(f"/flows/bindings/{lien['pk']}/", {"order": ordre})
                return
        self.ak.post("/flows/bindings/", {"target": flux_pk, "stage": etape_pk, "order": ordre, **extra})

    def flux(self, slug: str, nom: str, titre: str, designation: str, **extra) -> dict:
        return self.assurer(
            "/flows/instances/", {"slug": slug},
            {"slug": slug, "name": nom, "title": titre, "designation": designation,
             "policy_engine_mode": "any", **extra},
            cle="slug",
        )

    def prompt(self, nom: str, **donnees) -> str:
        return self.assurer("/stages/prompt/prompts/", {"name": nom}, {"name": nom, **donnees})["pk"]

    # ------------------------------------------------------------ étapes
    def groupes(self) -> None:
        etape("Groupes")
        membres = self.ak.groupe(self.cfg.GROUPE_MEMBRES)
        if not membres:
            membres = self.ak.post("/core/groups/", {"name": self.cfg.GROUPE_MEMBRES})
        self.membres_pk = membres["pk"]
        admins = self.ak.groupe(self.cfg.GROUPE_ADMINS)
        if not admins:
            admins = self.ak.post("/core/groups/", {"name": self.cfg.GROUPE_ADMINS, "is_superuser": True})
        self.admins_pk = admins["pk"]
        ok(f"« {self.cfg.GROUPE_MEMBRES} » et « {self.cfg.GROUPE_ADMINS} »")

    def champs_mot_de_passe(self) -> list[str]:
        mdp = self.prompt(
            "hub-mot-de-passe", field_key="password", label="Mot de passe", type="password",
            required=True, placeholder="Au moins 8 caractères", order=20,
        )
        mdp2 = self.prompt(
            "hub-mot-de-passe-repete", field_key="password_repeat", label="Encore une fois",
            type="password", required=True, placeholder="Le même mot de passe", order=30,
        )
        return [mdp, mdp2]

    def politique_mdp(self) -> str:
        pol = self.ak.premier("/policies/password/", name="hub-mot-de-passe-solide")
        donnees = {
            "name": "hub-mot-de-passe-solide", "password_field": "password",
            "length_min": 8, "amount_digits": 0, "amount_uppercase": 0,
            "amount_lowercase": 0, "amount_symbols": 0,
            "check_static_rules": True, "check_have_i_been_pwned": False,
            "hibp_allowed_count": 0, "check_zxcvbn": False,
            "error_message": "Choisis un mot de passe d'au moins 8 caractères.",
        }
        if pol:
            return self.ak.patch(f"/policies/password/{pol['pk']}/", donnees)["pk"]
        return self.ak.post("/policies/password/", donnees)["pk"]

    def flux_invitation(self) -> None:
        etape("Flux d'invitation (la personne acceptée choisit son mot de passe)")
        f = self.flux(SLUG_INVITATION, "Hub : invitation", "Bienvenue ! Choisis ton mot de passe",
                      "enrollment", authentication="none", layout="stacked")
        inv = self.assurer("/stages/invitation/stages/", {"name": "hub-invitation-verif"},
                           {"name": "hub-invitation-verif", "continue_flow_without_invitation": False})
        email = self.prompt(
            "hub-invitation-email", field_key="hub_email_affiche", label="Ton adresse e-mail",
            type="text_read_only", required=False, order=10,
            initial_value='return prompt_context.get("email", "")', initial_value_expression=True,
            placeholder="", placeholder_expression=False,
        )
        champs = [email, *self.champs_mot_de_passe()]
        p = self.assurer("/stages/prompt/stages/", {"name": "hub-invitation-mot-de-passe"},
                         {"name": "hub-invitation-mot-de-passe", "fields": champs,
                          "validation_policies": [self.mdp_pol]})
        w = self.assurer("/stages/user_write/", {"name": "hub-invitation-creation"},
                         {"name": "hub-invitation-creation", "user_creation_mode": "always_create",
                          "create_users_as_inactive": False, "create_users_group": self.membres_pk,
                          "user_type": "internal", "user_path_template": "users"})
        lg = self.assurer("/stages/user_login/", {"name": "hub-connexion"},
                          {"name": "hub-connexion", "session_duration": self.cfg.DUREE_SESSION})
        vers_hub = self.assurer("/stages/redirect/", {"name": "hub-vers-accueil"},
                                {"name": "hub-vers-accueil", "mode": "static", "keep_context": False,
                                 "target_static": f"{self.cfg.HUB_URL}/"})
        self.vers_hub = vers_hub
        for ordre, st in ((10, inv), (20, p), (30, w), (40, lg), (50, vers_hub)):
            self.lier(f["pk"], st["pk"], ordre)
        self.flux_invitation_pk = f["pk"]
        ok(f"flux « {SLUG_INVITATION} »")

    def flux_recuperation(self) -> None:
        etape("Flux « Mot de passe oublié »")
        f = self.flux(SLUG_RECUPERATION, "Hub : mot de passe oublié", "Mot de passe oublié",
                      "recovery", authentication="none", layout="stacked")
        ident = self.assurer("/stages/identification/", {"name": "hub-recuperation-identification"},
                             {"name": "hub-recuperation-identification", "user_fields": ["email", "username"],
                              "case_insensitive_matching": True, "pretend_user_exists": True,
                              "show_matched_user": False})
        mail = self.assurer("/stages/email/", {"name": "hub-recuperation-email"},
                            {"name": "hub-recuperation-email", "use_global_settings": True,
                             "template": "email/password_reset.html",
                             "subject": f"{self.cfg.NOM_SITE} : changer ton mot de passe",
                             "token_expiry": "minutes=30", "activate_user_on_success": False})
        p = self.assurer("/stages/prompt/stages/", {"name": "hub-recuperation-mot-de-passe"},
                         {"name": "hub-recuperation-mot-de-passe", "fields": self.champs_mot_de_passe(),
                          "validation_policies": [self.mdp_pol]})
        w = self.assurer("/stages/user_write/", {"name": "hub-recuperation-ecriture"},
                         {"name": "hub-recuperation-ecriture", "user_creation_mode": "never_create"})
        lg = self.ak.premier("/stages/user_login/", name="hub-connexion")
        for ordre, st in ((10, ident), (20, mail), (30, p), (40, w), (50, lg), (60, self.vers_hub)):
            self.lier(f["pk"], st["pk"], ordre)
        self.flux_recuperation_pk = f["pk"]
        ok(f"flux « {SLUG_RECUPERATION} »")

    def flux_google_inconnu(self) -> None:
        etape("Flux « compte Google sans accès » → formulaire de demande")
        f = self.flux(SLUG_GOOGLE_INCONNU, "Hub : compte Google sans accès", "Pas encore d'accès",
                      "enrollment", authentication="none")
        r = self.assurer("/stages/redirect/", {"name": "hub-vers-demande"},
                         {"name": "hub-vers-demande", "mode": "static", "keep_context": False,
                          "target_static": f"{self.cfg.HUB_URL}/demande?depuis=google"})
        self.lier(f["pk"], r["pk"], 10)
        self.flux_google_pk = f["pk"]
        ok("une personne inconnue qui passe par Google arrive sur « Demander un accès »")

    def google(self) -> list[str]:
        etape("Connexion Google")
        if not (self.cfg.GOOGLE_CLIENT_ID and self.cfg.GOOGLE_CLIENT_SECRET):
            src = self.ak.premier("/sources/oauth/", slug="google")
            print("  · GOOGLE_CLIENT_ID vide : connexion Google pas configurée (tu pourras relancer plus tard).")
            return [src["pk"]] if src else []
        auth_flux = self.ak.flux_pk("default-source-authentication")
        src = self.assurer("/sources/oauth/", {"slug": "google"}, {
            "name": "Google", "slug": "google", "provider_type": "google", "enabled": True,
            "consumer_key": self.cfg.GOOGLE_CLIENT_ID, "consumer_secret": self.cfg.GOOGLE_CLIENT_SECRET,
            "authentication_flow": auth_flux, "enrollment_flow": self.flux_google_pk,
            "user_matching_mode": "email_link", "policy_engine_mode": "any",
        }, cle="slug")
        ok("source « Google » (seuls les comptes déjà acceptés peuvent entrer)")
        ok(f"adresse de retour à mettre chez Google : https://{self.cfg.AUTH_HOTE}/source/oauth/callback/google/")
        return [src["pk"]]

    def page_connexion(self, sources: list[str]) -> None:
        etape("Page de connexion")
        ident = self.ak.premier("/stages/identification/", name="default-authentication-identification")
        mdp = self.ak.premier("/stages/password/", name="default-authentication-password")
        self.ak.patch(f"/stages/identification/{ident['pk']}/", {
            "user_fields": ["email", "username"], "password_stage": mdp["pk"],
            "case_insensitive_matching": True, "show_matched_user": True,
            "sources": sources, "show_source_labels": True,
            "enrollment_flow": None, "recovery_flow": self.flux_recuperation_pk,
            "pretend_user_exists": True,
        })
        login = self.ak.premier("/stages/user_login/", name="default-authentication-login")
        self.ak.patch(f"/stages/user_login/{login['pk']}/", {"session_duration": self.cfg.DUREE_SESSION})
        self.ak.patch("/flows/instances/default-authentication-flow/",
                      {"title": "Connexion"})
        ok("e-mail + mot de passe sur la même page, Google, « Mot de passe oublié ? »")

        css = ""
        chemin_css = Path(__file__).with_name("authentik.css")
        if chemin_css.exists():
            css = chemin_css.read_text(encoding="utf-8").replace("{{HUB_URL}}", self.cfg.HUB_URL)
        brand = self.ak.premier("/core/brands/", default=True) or self.ak.liste("/core/brands/")[0]
        self.ak.patch(f"/core/brands/{brand['brand_uuid']}/", {
            "branding_title": self.cfg.NOM_SITE,
            "branding_logo": f"{self.cfg.HUB_URL}/static/logo-texte.png",
            "branding_favicon": f"{self.cfg.HUB_URL}/static/icone-192.png",
            "branding_custom_css": css,
            "branding_default_flow_background": f"{self.cfg.HUB_URL}/static/fond-connexion.jpg",
            "flow_recovery": self.flux_recuperation_pk,
        })
        reglages = self.ak.get("/admin/settings/")
        reglages["footer_links"] = [{"name": "Demander un accès", "href": f"{self.cfg.HUB_URL}/demande"}]
        reglages["default_user_change_email"] = False
        reglages["default_user_change_username"] = False
        self.ak.put("/admin/settings/", reglages)
        ok("nom, logo, style Encre et lien « Demander un accès » sous le formulaire")

    def compte_service(self) -> str:
        etape("Compte de service « hub » (droits limités)")
        role = self.ak.premier("/rbac/roles/", name=NOM_ROLE)
        if not role:
            role = self.ak.post("/rbac/roles/", {"name": NOM_ROLE})
        self.ak.post(f"/rbac/permissions/assigned_by_roles/{role['pk']}/assign/",
                     {"permissions": PERMISSIONS_HUB})
        sa = self.ak.premier("/core/users/", username=NOM_SA)
        if not sa:
            self.ak.post("/core/users/service_account/",
                         {"name": NOM_SA, "create_group": False, "expiring": False})
            sa = self.ak.premier("/core/users/", username=NOM_SA)
        self.ak.post(f"/rbac/roles/{role['pk']}/add_user/", {"pk": sa["pk"]})
        jeton = self.ak.premier("/core/tokens/", identifier=JETON_ID)
        if not jeton:
            self.ak.post("/core/tokens/", {"identifier": JETON_ID, "intent": "api", "user": sa["pk"],
                                           "expiring": False,
                                           "description": "Jeton du hub (sites, invitations)"})
        cle = self.ak.get(f"/core/tokens/{JETON_ID}/view_key/")["key"]
        fichier = Path(self.cfg.DATA_DIR) / "authentik-token"
        fichier.write_text(cle, encoding="utf-8")
        os.chmod(fichier, 0o600)
        ok(f"{len(PERMISSIONS_HUB)} droits, jeton écrit dans {fichier}")
        return cle

    def entete_secret(self) -> None:
        etape("En-tête secret ajouté par Authentik (X-Passerelle-Cle)")
        # Authentik ajoute cet en-tête à chaque requête connectée, vers le hub et vers chaque site.
        # Sur les chemins publics (/api/notify…), il n'est pas là : un faux X-authentik-email ne vaut rien.
        expr = (
            "attrs = dict(request.user.group_attributes(request))\n"
            "entetes = dict(attrs.get(\"additionalHeaders\") or {})\n"
            f"entetes[\"X-Passerelle-Cle\"] = \"{self.cfg.PASSERELLE_CLE}\"\n"
            "attrs[\"additionalHeaders\"] = entetes\n"
            "return {\"ak_proxy\": {\"user_attributes\": attrs, "
            "\"is_superuser\": request.user.is_superuser}}\n"
        )
        self.assurer("/propertymappings/provider/scope/", {"name": MAPPING_SECRET},
                               {"name": MAPPING_SECRET, "scope_name": "ak_proxy",
                                "description": "Prouve au hub et aux sites que la personne est connectée par Authentik",
                                "expression": expr})
        # Les sites déjà ajoutés par le hub reçoivent aussi l'en-tête.
        mappings = mappings_proxy(self.ak)
        n = 0
        for prov in self.ak.liste("/providers/proxy/"):
            if prov["name"].startswith("Site : ") and sorted(prov.get("property_mappings", [])) != sorted(mappings):
                self.ak.patch(f"/providers/proxy/{prov['pk']}/", {"property_mappings": mappings, "mode": prov["mode"],
                                                              "internal_host": prov["internal_host"]})
                n += 1
        ok("le hub et les sites peuvent vérifier que la personne est vraiment connectée"
           + (f" ({n} site(s) mis à jour)" if n else ""))

    def application_hub(self) -> None:
        etape(f"Application « Hub » sur https://{self.cfg.DOMAINE}")
        mappings = mappings_proxy(self.ak)
        prov = self.assurer("/providers/proxy/", {"name": "Hub"}, {
            "name": "Hub", "mode": "proxy",
            "authorization_flow": self.ak.flux_pk("default-provider-authorization-implicit-consent"),
            "invalidation_flow": self.ak.flux_pk("default-provider-invalidation-flow"),
            "external_host": self.cfg.HUB_URL, "internal_host": self.cfg.HUB_INTERNE,
            "internal_host_ssl_validation": False, "skip_path_regex": SKIP_HUB,
            "property_mappings": mappings, "access_token_validity": self.cfg.DUREE_SESSION,
        })
        app = self.assurer("/core/applications/", {"slug": "hub"}, {
            "name": "Hub", "slug": "hub", "provider": prov["pk"], "meta_launch_url": self.cfg.HUB_URL,
            "meta_description": "Tous les sites", "policy_engine_mode": "any",
        }, cle="slug")
        self.ak.avant_poste_ajouter(prov["pk"])
        op = self.ak.avant_poste_integre()
        config = dict(op.get("config", {}))
        config["authentik_host"] = f"{self.cfg.AUTH_URL}/"
        config["authentik_host_browser"] = ""
        self.ak.patch(f"/outposts/instances/{op['pk']}/", {"config": config})
        ok(f"application « {app['name']} », avant-poste intégré → {self.cfg.AUTH_URL}/")

    def compte_admin(self) -> str | None:
        etape("Ton compte administrateur")
        email = (self.cfg.ADMIN_EMAIL or "").strip().lower()
        if not email:
            print("  · ADMIN_EMAIL vide : on garde « akadmin » comme administrateur.")
            return None
        user = self.ak.premier("/core/users/", email=email) or self.ak.premier("/core/users/", username=email)
        mot_de_passe = None
        if not user:
            user = self.ak.post("/core/users/", {
                "username": email, "email": email, "name": self.cfg.ADMIN_NOM or email,
                "is_active": True, "type": "internal", "path": "users",
                "groups": [self.admins_pk, self.membres_pk],
            })
            alphabet = string.ascii_letters + string.digits
            mot_de_passe = "".join(secrets.choice(alphabet) for _ in range(16))
            self.ak.post(f"/core/users/{user['pk']}/set_password/", {"password": mot_de_passe})
            ok(f"compte {email} créé (administrateur)")
        else:
            groupes = set(user.get("groups", [])) | {self.admins_pk, self.membres_pk}
            self.ak.patch(f"/core/users/{user['pk']}/", {"groups": list(groupes), "is_active": True})
            ok(f"compte {email} déjà là, mis dans les administrateurs")
        return mot_de_passe

    # ------------------------------------------------------------ tout
    def tout(self) -> None:
        self.groupes()
        self.mdp_pol = self.politique_mdp()
        self.flux_invitation()
        self.flux_recuperation()
        self.flux_google_inconnu()
        sources = self.google()
        self.page_connexion(sources)
        self.compte_service()
        self.entete_secret()
        self.application_hub()
        mdp = self.compte_admin()
        print("\n✅ Authentik est prêt pour le hub.")
        if mdp:
            print("\n  ┌──────────────────────────────────────────────────────────")
            print(f"  │ Ton compte : {self.cfg.ADMIN_EMAIL}")
            print(f"  │ Mot de passe provisoire : {mdp}")
            print("  │ Note-le, il ne sera plus affiché. Change-le ensuite dans")
            print(f"  │ « Mon compte » : {self.cfg.AUTH_URL}/if/user/")
            print("  └──────────────────────────────────────────────────────────")


def main() -> int:
    cfg = Config()
    jeton = os.environ.get("AUTHENTIK_BOOTSTRAP_TOKEN", "").strip()
    if not jeton:
        print("AUTHENTIK_BOOTSTRAP_TOKEN est vide : impossible de configurer Authentik.\n"
              "Remets-le dans .env (n'importe quelle longue valeur) AVANT le tout premier démarrage,\n"
              "ou crée un jeton API administrateur dans Authentik et lance :\n"
              "  AUTHENTIK_BOOTSTRAP_TOKEN=<jeton> docker compose run --rm hub python -m hub.configurer")
        return 1
    ak = Authentik(cfg.AUTHENTIK_URL_INTERNE, jeton, timeout=60)
    if not ak.joignable():
        print(f"Authentik ne répond pas sur {cfg.AUTHENTIK_URL_INTERNE}. Attends une minute et relance.")
        return 1
    try:
        Configurateur(ak, cfg).tout()
    except AuthentikErreur as exc:
        print(f"\n✗ {exc}")
        if exc.details:
            print(f"  Détails : {exc.details}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
