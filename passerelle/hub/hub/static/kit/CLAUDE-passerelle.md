## Connexion : passerelle Authentik (super-nono.cc)

Ce site est publié derrière la passerelle de Noë (Authentik). Règles à respecter :

- **Pas de page de connexion, pas de mot de passe, pas de Cloudflare Access** dans
  l'app. Authentik connecte les personnes avant qu'elles n'arrivent ici.
- L'identité se lit **uniquement** avec `passerelle_auth.py` / `passerelle-auth.ts`
  (copié tel quel dans le projet, ne pas le réécrire) : `utilisateur()` /
  `lireUtilisateur()`. Ne jamais lire `X-Authentik-*` directement : ils ne valent
  que si `X-Passerelle-Cle` correspond à `PASSERELLE_CLE`.
- Une personne est identifiée par son **e-mail** (`X-Authentik-Email`), en minuscules.
  Admin = groupe « authentik Admins » (`u.admin`).
- Pages : `@connexion_requise` ou `@admin_requis`. API appelées aussi par des
  machines : `@personne_ou_machine`. Appels de machines seulement : `@machine_requise`.
- Tout ce qui est appelé par une machine (autre site sur l'IP locale, Home
  Assistant, webhook) arrive **sans** Authentik : vérifier un jeton
  (`SERVICE_JETONS`, `Authorization: Bearer`) ou une signature HMAC
  (`signature_valide`). Jamais de chemin public qui modifie des données sans vérification.
- **MCP (connecteur Claude)** : le site est son propre guichet OAuth (modèle
  RecipeLog), voir `passerelle/docs/mcp-oauth.md`. Rien à créer dans Authentik.
  - Flask : `brancher_mcp(app, nom="…")` puis `@mcp_requis` sur `/api/mcp`.
    TypeScript : `guichetMcp(req, "…")` en tête du routeur, `accesMcp(req)` dans `/api/mcp`.
    Ne pas réécrire l'OAuth, ne pas inventer de clé dans l'URL ni d'en-tête.
  - Serveur MCP sur `/api/mcp` uniquement. Chemins publics dans le hub :
    `/api/mcp$`, `/api/mcp/oauth/inscription`, `/api/mcp/oauth/jeton`,
    `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`.
    `/api/mcp/oauth/autoriser` et `/deconnecter` restent derrière Authentik.
  - `.env` : `PASSERELLE_URL_SITE=https://<site>.super-nono.cc`,
    `PASSERELLE_MCP_SECRET` (32+ caractères, propre au site), `PASSERELLE_MCP_POUR`
    (`admins` par défaut, `tous` ou un groupe), `PASSERELLE_MCP_FICHIER` (dossier sauvegardé).
  - Bouton « Déconnecter Claude » dans l'admin : `POST /api/mcp/oauth/deconnecter`.
  - Dans Claude : adresse `https://<site>.super-nono.cc/api/mcp`, Client ID et secret vides.
- **Comptes** : un compte local par e-mail, créé à la première visite (modèle :
  `exemple_comptes.py` du kit). Rôles et suspension dans le site ; qui peut
  **entrer** se règle dans le hub (« Réservé à certains groupes »), pas dans l'app.
- **Pages ouvertes à tous** (ex. salon de jeu par QR code, WebSocket) : chemins
  publics déclarés dans le hub, aucune lecture d'identité Authentik dessus,
  actions limitées, jamais d'action d'admin.
- Lien « Se déconnecter » : `/outpost.goauthentik.io/sign_out`.
  Lien « Mon compte » : `https://auth.super-nono.cc/if/user/`.
- Tout nouveau chemin public doit être ajouté dans le hub (Sites → le site →
  Chemins ouverts sans connexion) et signalé à Noë.
- Notifications : via BotPanel (`POST {BOTPANEL_URL}/api/notify`, adresse locale), jamais Discord en direct.
- Dev local : `PASSERELLE_CLE` vide + `PASSERELLE_DEV_EMAIL=...` (+ `PASSERELLE_DEV_ADMIN=1`).
- Variables `.env` : `PASSERELLE_CLE`, `SERVICE_JETONS` (format `nom:jeton,nom2:jeton2`, jetons d'au moins 24 caractères).

Guides complets : `passerelle/docs/brancher-un-projet.md` et `passerelle/docs/mcp-oauth.md` du dépôt SuperNon0/site.
