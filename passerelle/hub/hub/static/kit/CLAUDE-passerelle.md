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
  Assistant, webhook, `/mcp`) arrive **sans** Authentik : vérifier un jeton
  (`SERVICE_JETONS`, `Authorization: Bearer`) ou une signature HMAC
  (`signature_valide`). Jamais de chemin public qui modifie des données sans vérification.
- MCP : connexion OAuth native avec le compte Authentik (client `claude-mcp`
  créé par la passerelle). En Flask : `brancher_mcp(app)` puis `@mcp_requis` sur
  `/mcp` ; en TypeScript : `mcpRessource`, `mcpServeur`, `mcpEnregistrer`,
  `mcpVerifier`, `mcpEnteteRefus`. Ne pas inventer d'autre système (pas de clé
  dans l'URL, pas d'OAuth maison). Chemins publics : `/mcp`, `/.well-known`, `/oauth`.
  `.env` : `PASSERELLE_URL_SITE=https://<site>.super-nono.cc`.
- Lien « Se déconnecter » : `/outpost.goauthentik.io/sign_out`.
  Lien « Mon compte » : `https://auth.super-nono.cc/if/user/`.
- Tout nouveau chemin public doit être ajouté dans le hub (Sites → le site →
  Chemins ouverts sans connexion) et signalé à Noë.
- Notifications : via BotPanel (`POST {BOTPANEL_URL}/api/notify`, adresse locale), jamais Discord en direct.
- Dev local : `PASSERELLE_CLE` vide + `PASSERELLE_DEV_EMAIL=...` (+ `PASSERELLE_DEV_ADMIN=1`).
- Variables `.env` : `PASSERELLE_CLE`, `SERVICE_JETONS` (format `nom:jeton,nom2:jeton2`, jetons d'au moins 24 caractères).

Guide complet : `passerelle/docs/brancher-un-projet.md` du dépôt SuperNon0/site.
