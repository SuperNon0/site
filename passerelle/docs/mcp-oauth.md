# Brancher Claude sur un site par OAuth (MCP)

> Document de référence pour **tous les projets derrière la passerelle**
> (`*.super-nono.cc`). Donne-le à Claude (ou à qui développe) dès qu'un projet a,
> ou doit avoir, un serveur MCP. Il reprend le modèle de RecipeLog, adapté à la
> passerelle Authentik.

## 1. Le principe en une minute

Dans Claude (claude.ai, l'appli, Claude Code), on ajoute un connecteur avec
l'adresse `https://<site>.super-nono.cc/api/mcp`, on laisse **Client ID et secret
vides**, on clique « Se connecter ». Claude découvre tout seul le reste :

1. Claude appelle `/api/mcp` sans jeton → le site répond **401** avec
   `WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource/api/mcp"`.
2. Claude lit cette fiche, puis la fiche du guichet (`/.well-known/oauth-authorization-server`).
3. Claude s'inscrit (`/api/mcp/oauth/inscription`, DCR) ou se présente avec sa
   propre fiche (CIMD : son `client_id` est une adresse https).
4. Claude ouvre `/api/mcp/oauth/autoriser` dans le navigateur. **Authentik
   connecte la personne** (Google ou mot de passe, comme pour le reste du site),
   puis le site affiche « Autoriser Claude ? ».
5. « Autoriser » → le site renvoie un code à Claude, Claude l'échange contre un
   jeton (`/api/mcp/oauth/jeton`, PKCE obligatoire).
6. Chaque appel MCP porte `Authorization: Bearer ra_…`. Le site vérifie la
   signature, la date et le compteur « génération ».

**Le site est son propre guichet OAuth.** Rien à créer dans Authentik, aucune
base de données : clients, codes et jetons sont des valeurs **signées HMAC** par
le site. « Tout déconnecter » = augmenter un compteur, ce qui rend caducs tous
les jetons déjà donnés.

## 2. Ce qui change par rapport à RecipeLog (la passerelle)

| RecipeLog (recipelog.cc) | Derrière la passerelle |
|---|---|
| Traefik / Cloudflare laissent passer les chemins OAuth | L'outpost Authentik protège **tout** le site, sauf les « chemins ouverts sans connexion » déclarés dans le hub |
| La page « Autoriser ? » lit la session du site (cookie, `?retour=`) | La page « Autoriser ? » reste **derrière Authentik** : la personne y arrive déjà connectée, on lit `utilisateur()` |
| Code OAuth écrit à la main dans le projet | Code fourni par le kit : `passerelle_auth.py` / `passerelle-auth.ts` (à copier tel quel) |

RecipeLog sur recipelog.cc garde sa propre version : ce document vaut pour les
sites `*.super-nono.cc`.

### Chemins publics (hub → Sites → le site → « Chemins ouverts sans connexion »)

Ce sont les valeurs par défaut de tout nouveau site dans le hub :

```
/api/notify
/api/mcp$
/api/mcp/oauth/inscription
/api/mcp/oauth/jeton
/.well-known/oauth-protected-resource
/.well-known/oauth-authorization-server
```

- Un `$` à la fin veut dire « ce chemin exactement, sans ce qui est dessous ».
  `/api/mcp$` ouvre `/api/mcp` mais **pas** `/api/mcp/oauth/autoriser`.
- **`/api/mcp/oauth/autoriser` ne doit jamais être public** : c'est là
  qu'Authentik connecte la personne. `/api/mcp/oauth/deconnecter` non plus.
- Un site déjà créé avec les anciens chemins (`/mcp`, `/.well-known`, `/oauth`) :
  remplacer la liste par celle-ci dans le hub.

## 3. Les adresses du guichet

| Adresse | Méthode | Public ? | Rôle |
|---|---|---|---|
| `/api/mcp` | POST (GET) | oui | le serveur MCP ; 401 + `WWW-Authenticate` sans jeton |
| `/.well-known/oauth-protected-resource[/api/mcp]` | GET | oui | fiche de la ressource (RFC 9728) |
| `/.well-known/oauth-authorization-server` | GET | oui | fiche du guichet (RFC 8414), annonce CIMD et PKCE S256 |
| `/api/mcp/oauth/inscription` | POST | oui | inscription d'un client (DCR, RFC 7591) |
| `/api/mcp/oauth/autoriser` | GET / POST | **non** (Authentik) | page « Autoriser Claude ? » |
| `/api/mcp/oauth/jeton` | POST | oui | code → jetons, renouvellement |
| `/api/mcp/oauth/deconnecter` | POST | **non** (admins) | « Déconnecter Claude » partout |

Règles tenues par le kit (ne pas les affaiblir) :

- **PKCE S256 obligatoire**. Code à usage unique, valable **5 min**.
- Jeton d'accès (`ra_…`) **1 h**, jeton de renouvellement (`rr_…`) **60 jours**.
- Adresses de retour : https, ou `http://localhost` / `127.0.0.1` / `[::1]`
  (port libre, RFC 8252). Tout autre `http` est refusé.
- CIMD : fiche lue seulement en https port 443, jamais vers une adresse privée
  (protection SSRF), sans redirection, 5 s, 20 Ko, gardée 10 min.
- Les jetons sont liés au site (clé = HMAC du secret et de l'adresse du site) :
  un jeton d'un site ne marche pas sur un autre.
- Page « Autoriser ? » : `X-Frame-Options: DENY`, CSP `form-action` limitée au
  site et à l'origine de retour, e-mails entourés de `<!--email_off-->`
  (sinon Cloudflare les masque).
- Réponses du guichet en `Cache-Control: no-store`, CORS ouvert sur les adresses publiques.

## 4. Qui peut brancher Claude

- `PASSERELLE_MCP_POUR=admins` (défaut) : seulement le groupe « authentik Admins ».
- `PASSERELLE_MCP_POUR=tous` : toute personne qui a accès au site.
- `PASSERELLE_MCP_POUR=<groupe>` : un groupe Authentik précis.
- Règle de l'application en plus, vérifiée **à chaque appel** (ex. compte encore
  actif dans ta base) : `passerelle_auth.regle_mcp = lambda email: …` en Python,
  `definirRegleMcp(async (email) => …)` en TypeScript.

## 5. Variables `.env` du site

```
PASSERELLE_CLE=…                      # hub → « Brancher un projet »
PASSERELLE_URL_SITE=https://recettes.super-nono.cc
PASSERELLE_MCP_SECRET=…               # 32 caractères ou plus, propre à CE site (openssl rand -hex 32)
PASSERELLE_MCP_POUR=admins            # admins | tous | <groupe>
PASSERELLE_MCP_FICHIER=data/mcp-oauth.json   # compteur « tout déconnecter », dans un dossier sauvegardé
SERVICE_JETONS=homeassistant:…        # facultatif : machines qui appellent /api/mcp sans OAuth
```

`PASSERELLE_URL_SITE` est obligatoire : derrière la passerelle, l'adresse vue par
l'app (`http://IP:port`) n'est pas celle que Claude doit utiliser. Changer
`PASSERELLE_MCP_SECRET` déconnecte aussi tout le monde.

## 6. Intégrer dans le code

### Python / Flask

```python
import passerelle_auth as pa

app = Flask(__name__)
pa.brancher_mcp(app, nom="Recettes")      # fiches, inscription, autoriser, jeton, deconnecter

@app.route("/api/mcp", methods=["GET", "POST", "OPTIONS"])
@pa.mcp_requis                            # jeton OAuth du guichet OU jeton de machine
def mcp():
    u = pa.utilisateur()                  # la personne (None si c'est une machine)
    qui = u.email if u else pa.machine()
    ...                                   # ton serveur MCP (JSON-RPC streamable HTTP)
```

Hors Flask : les fonctions `fiche_ressource`, `fiche_serveur`, `inscrire`,
`autoriser_get`, `autoriser_post`, `jeton`, `verifier_jeton_mcp`, `refus_mcp`
renvoient une `Reponse(statut, corps, entetes)` à recopier dans ton framework.

### TypeScript (Next.js, Hono, Remix, Express…)

```ts
import { guichetMcp, accesMcp } from "@/lib/passerelle-auth";

// middleware.ts (Next.js) : tout le guichet en une ligne
export async function middleware(req: Request) {
  return (await guichetMcp(req, "Recettes")) ?? NextResponse.next();
}
export const config = { matcher: ["/.well-known/oauth-:path*", "/api/mcp/oauth/:path*"], runtime: "nodejs" };

// app/api/mcp/route.ts
export async function POST(req: Request) {
  const a = await accesMcp(req);
  if (a instanceof Response) return a;    // 401 + WWW-Authenticate
  const qui = a.utilisateur?.email ?? a.machine;
  ...                                     // ton serveur MCP
}
```

Express : voir l'adaptateur en bas de `passerelle-auth.ts`.

### Le bouton « Déconnecter Claude »

Dans la page d'admin (ou de paramètres) du site, un bouton qui fait
`POST /api/mcp/oauth/deconnecter` (réservé aux admins). Il augmente le compteur :
tous les connecteurs Claude de ce site devront refaire « Se connecter ».

```html
<form method="post" action="/api/mcp/oauth/deconnecter"
      onsubmit="return confirm('Déconnecter Claude partout sur ce site ?')">
  <button class="btn">Déconnecter Claude</button>
</form>
```

## 7. Côté Claude

- **claude.ai / appli** : Paramètres → Connecteurs → Ajouter un connecteur
  personnalisé → adresse `https://<site>.super-nono.cc/api/mcp`, **Client ID et
  secret vides** → « Se connecter » → connexion Authentik → « Autoriser ».
- **Claude Code** : `claude mcp add --transport http recettes https://recettes.super-nono.cc/api/mcp`,
  puis `/mcp` → « Authenticate ».
- **Une machine** (Home Assistant, script) n'utilise pas OAuth : `Authorization:
  Bearer <jeton de SERVICE_JETONS>` sur `/api/mcp`, par l'adresse publique ou
  l'adresse locale.

## 8. Tester

Depuis le LXC de la passerelle, le script du kit fait tout :

```bash
./verifier-site.sh recettes 192.168.1.60:3000 --page-privee /api/moi
```

Il vérifie : 401 + `WWW-Authenticate` sur `/api/mcp`, les deux fiches en JSON,
l'inscription (201), et que « Autoriser ? » reste derrière la connexion.

À la main :

```bash
S=https://recettes.super-nono.cc
curl -si -X POST $S/api/mcp | grep -i www-authenticate       # 401 + resource_metadata
curl -s $S/.well-known/oauth-protected-resource/api/mcp       # JSON, resource = $S/api/mcp
curl -s $S/.well-known/oauth-authorization-server             # JSON, S256, CIMD true
curl -s -X POST $S/api/mcp/oauth/inscription -H 'Content-Type: application/json' \
     -d '{"redirect_uris":["https://claude.ai/api/mcp/auth_callback"]}'   # 201, client_id cl_…
curl -si $S/api/mcp/oauth/autoriser | head -1                 # 302 vers auth.super-nono.cc
```

## 9. Pièges connus

- **« Autoriser ? » ouvert au public** : n'importe qui obtiendrait un code au nom
  de personne. Jamais `/api/mcp/oauth` entier dans les chemins publics : un par un.
- **`/api/mcp` sans le `$`** : ouvre aussi `/api/mcp/oauth/autoriser`. Le hub
  refuse un `$` ailleurs qu'à la fin.
- **`PASSERELLE_URL_SITE` oublié** : les fiches annoncent `http://IP:port`,
  Claude échoue à « Se connecter ».
- **Le hub met ~20 s** à appliquer de nouveaux chemins publics (rechargement de
  l'outpost).
- **Client ID rempli dans Claude** : laisser vide, sinon Claude n'utilise pas
  l'inscription.
- **E-mail masqué** sur la page « Autoriser ? » (Cloudflare) : garder
  `<!--email_off-->`.
- **Compteur perdu** (`PASSERELLE_MCP_FICHIER` dans un dossier effacé à chaque
  déploiement) : le mettre dans le dossier de données sauvegardé.
- **Ne rien inventer d'autre** : pas de clé dans l'URL, pas d'en-tête maison,
  pas d'application OAuth dans Authentik pour le MCP.

## 10. Texte à coller dans le CLAUDE.md d'un projet

Le bloc complet (passerelle + MCP) est dans le kit :
`hub/hub/static/kit/CLAUDE-passerelle.md` (aussi téléchargeable sur la page
« Brancher un projet » du hub). La partie MCP :

```markdown
### MCP (connecteur Claude)
- Le site est son propre guichet OAuth (modèle RecipeLog) : suivre
  `passerelle/docs/mcp-oauth.md` du dépôt SuperNon0/site. Rien dans Authentik.
- Code : `passerelle_auth.py` (`brancher_mcp(app, nom=…)` + `@mcp_requis` sur
  `/api/mcp`) ou `passerelle-auth.ts` (`guichetMcp` + `accesMcp`). Copié tel
  quel, ne pas réécrire l'OAuth.
- Serveur MCP sur `/api/mcp` uniquement. Chemins publics dans le hub :
  `/api/mcp$`, `/api/mcp/oauth/inscription`, `/api/mcp/oauth/jeton`,
  `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`.
  `/api/mcp/oauth/autoriser` reste derrière Authentik.
- `.env` : `PASSERELLE_URL_SITE`, `PASSERELLE_MCP_SECRET` (32+ caractères,
  propre au site), `PASSERELLE_MCP_POUR`, `PASSERELLE_MCP_FICHIER` (dossier sauvegardé).
- Bouton « Déconnecter Claude » (admins) : `POST /api/mcp/oauth/deconnecter`.
- Avant de livrer : `verifier-site.sh <site> <ip:port>` tout vert, puis test
  réel « Se connecter » depuis claude.ai.
```

## 11. Consigne à donner à Claude Code pour un projet

Pour **modifier un projet existant** (qui a un MCP avec clé, en-tête ou
Cloudflare) :

> Ce projet passe derrière la passerelle Authentik de super-nono.cc. Lis
> `passerelle/docs/mcp-oauth.md` et `passerelle/docs/brancher-un-projet.md` du
> dépôt SuperNon0/site. Copie `passerelle_auth.py` (ou `passerelle-auth.ts`)
> depuis `passerelle/hub/hub/static/kit/` sans le modifier. Mets le serveur MCP
> sur `/api/mcp` avec `@mcp_requis` (ou `accesMcp`), branche le guichet
> (`brancher_mcp` ou `guichetMcp`), retire l'ancienne vérification (clé dans
> l'URL, en-tête, JWT Cloudflare), ajoute le bouton « Déconnecter Claude » dans
> l'admin, ajoute les variables au `.env.example` et le bloc MCP de
> `CLAUDE-passerelle.md` au CLAUDE.md. Dis-moi la liste des chemins publics à
> mettre dans le hub et l'adresse du connecteur à remettre dans Claude.

Pour **un nouveau projet** :

> Ce projet sera publié derrière la passerelle Authentik de super-nono.cc et
> aura un connecteur MCP pour Claude. Suis `passerelle/docs/brancher-un-projet.md`
> et `passerelle/docs/mcp-oauth.md` du dépôt SuperNon0/site, avec le kit
> `passerelle/hub/hub/static/kit/`. Pas de page de connexion dans l'app, pas
> d'OAuth écrit à la main.
