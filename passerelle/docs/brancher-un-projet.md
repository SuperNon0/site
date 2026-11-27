# Brancher un projet à la passerelle

Ce guide vaut pour tes projets actuels (à migrer) comme pour les prochains.
Le kit prêt à copier est dans [`hub/hub/static/kit/`](../hub/hub/static/kit/) et
se télécharge aussi depuis le hub : **Plus → Brancher un projet**.

| Fichier | Rôle |
|---|---|
| `passerelle_auth.py` | Python (Flask ou autre) : qui est connecté, jetons des machines, signatures de webhooks |
| `passerelle-auth.ts` | la même chose pour Node / TypeScript (Next.js, Express…) |
| `verifier-site.sh` | vérifie un site branché, depuis le LXC de la passerelle |
| `CLAUDE-passerelle.md` | à coller dans le `CLAUDE.md` d'un projet pour que Claude le développe correctement |

## Les quatre façons d'entrer dans un site

| Qui | Par où | Qui vérifie | Dans le code |
|---|---|---|---|
| **Une personne**, dans un navigateur | `https://recettes.super-nono.cc` | Authentik (connexion), puis l'app (en-tête secret) | `utilisateur()` |
| **Un outil de ton réseau** (autre site, Home Assistant, script) | `http://192.168.1.60:3000` en direct | le pare-feu (IP autorisée), puis l'app (jeton) | `machine()` |
| **Un service d'Internet** (API, webhook, raccourci iPhone) | `https://recettes.super-nono.cc/api/...` sur un chemin public | l'app seule (jeton ou signature) | `machine()`, `signature_valide()` |
| **Claude, par MCP** | `https://recettes.super-nono.cc/api/mcp`, chemin public | le guichet OAuth du site (connexion Authentik à « Autoriser ? »), puis l'app (jeton signé) | `brancher_mcp`, `@mcp_requis` |

Retiens une seule règle : **Authentik ne protège d'office que les personnes dans
un navigateur.** Tout ce qui passe par l'adresse locale ou par un chemin public
arrive sans vérification, et c'est ton application qui décide (pour le MCP, le
kit vérifie le jeton OAuth du site à ta place).

---

## 1. Les personnes (navigateur)

Authentik connecte la personne une fois pour tous les sites, puis ajoute à chaque
requête :

| En-tête | Contenu |
|---|---|
| `X-Authentik-Email` | e-mail (c'est l'identifiant à utiliser dans ta base) |
| `X-Authentik-Name` | nom complet |
| `X-Authentik-Username` | identifiant Authentik |
| `X-Authentik-Uid` | identifiant interne stable |
| `X-Authentik-Groups` | groupes, séparés par `\|` (« authentik Admins », « Membres »…) |
| `X-Passerelle-Cle` | le secret `PASSERELLE_CLE` de la passerelle |

**Ne crois jamais `X-Authentik-*` sans `X-Passerelle-Cle`.** Sur un chemin
public ou par l'adresse locale, n'importe qui peut inventer ces en-têtes ; il ne
connaît pas la clé. `passerelle_auth` fait cette vérification pour toi.

```python
from passerelle_auth import utilisateur, connexion_requise, admin_requis, LIEN_DECONNEXION

@app.get("/")
@connexion_requise
def accueil():
    u = utilisateur()          # u.email, u.nom, u.prenom, u.groupes, u.admin
    return render_template("accueil.html", u=u, deconnexion=LIEN_DECONNEXION)
```

- **Plus de page de connexion dans l'app**, plus de mot de passe, plus de
  Cloudflare Access : c'est Authentik qui s'en charge.
- **Rôles** : `u.admin` est vrai pour le groupe « authentik Admins ». Pour des
  rôles propres à l'app, garde une table `comptes` indexée par e-mail.
- **Qui a accès au site** : se règle dans le hub (Membres ou Admins seulement).
- **Se déconnecter** : lien vers `/outpost.goauthentik.io/sign_out` (déconnecte de tous les sites).
- **Mon compte** (mot de passe, Google) : `https://auth.super-nono.cc/if/user/`.

---

## 2. Les outils qui se parlent sur l'adresse locale

**L'adresse locale marche toujours.** La passerelle ne change rien au réseau :
`http://192.168.1.50:8080/api/notify` répond comme avant. Ce qui change, c'est le
**pare-feu Proxmox** de chaque site, qui ne laisse entrer que ceux que tu as listés.

Pour chaque site **appelé** en direct, autorise sur son port :

1. l'IP de la passerelle (toujours) ;
2. l'IP de chaque machine qui l'appelle en direct.

Exemple : BotPanel reçoit `/api/notify` de presque tous tes sites. Sur son LXC :

| Sens | Action | Source | Port |
|---|---|---|---|
| Entrée | ACCEPT | IP de la passerelle | 8080 |
| Entrée | ACCEPT | IPSet `+appelants` (IP de tes autres LXC, Home Assistant…) | 8080 |
| Entrée | ACCEPT | `192.168.1.0/24` | 22 |

Astuce : crée l'IPSet une seule fois dans **Centre de données → Pare-feu → IPSet**
et réutilise-le sur chaque site. Le plus simple au début : autoriser tout ton
réseau local (`192.168.1.0/24`) sur le port de BotPanel ; le plus strict : la liste exacte.

**Ces appels ne passent pas par Authentik**, donc il n'y a ni personne connectée
ni `X-Authentik-*`. Si l'appel fait plus que déclencher une notification (lire
des données, écrire, supprimer), protège-le par un **jeton** :

```bash
# Dans le .env du site APPELÉ : un jeton par appelant (le hub en génère un : Brancher un projet)
SERVICE_JETONS=homeassistant:V4n...,recipelog:Qx7...
```

```python
# Dans le site appelé
from passerelle_auth import machine_requise, machine

@app.post("/api/stock")
@machine_requise
def stock():
    appelant = machine()        # "homeassistant", "recipelog"…
```

```python
# Dans le site appelant
requests.post("http://192.168.1.60:3000/api/stock", json=..., timeout=10,
              headers={"Authorization": f"Bearer {os.environ['RECETTES_JETON']}"})
```

À éviter : appeler `https://autre-site.super-nono.cc/...` sur un chemin **protégé**
depuis une machine. Authentik répond par une redirection vers la page de
connexion. Une machine utilise soit l'adresse locale, soit un chemin public.

---

## 3. Les accès depuis Internet (API, webhooks, raccourcis)

Pour qu'un service extérieur (GitHub, un raccourci iPhone en 4G, Home Assistant
Cloud, BotPanel quand il appelle un bouton webhook) atteigne ton app :

1. Hub → Sites → le site → **Chemins ouverts sans connexion** : ajoute le chemin
   (`/api/notify` ouvre aussi tout ce qui est dessous, comme `/api/notify/test`).
2. Dans l'app, ce chemin **doit** vérifier quelque chose :
   - un **jeton** : `Authorization: Bearer <jeton>` (`@machine_requise`) ;
   - ou une **signature** pour un webhook :

```python
from passerelle_auth import signature_valide

@app.post("/api/botpanel/action")
def action():
    if not signature_valide(request.get_data(), request.headers.get("X-BotPanel-Timestamp", ""),
                            request.headers.get("X-BotPanel-Signature", ""), os.environ["BOTPANEL_WEBHOOK_SECRET"]):
        return {"erreur": "signature"}, 401
```

GitHub signe autrement (`X-Hub-Signature-256: sha256=HMAC(secret, corps)`, sans
horodatage) : compare avec `hmac.compare_digest`.

Un chemin public sans aucune vérification n'est acceptable que pour quelque
chose d'inoffensif (un `/sante` qui répond « ok »).

---

## 4. Les connexions MCP : « Se connecter » dans Claude

Tout est détaillé dans **[`mcp-oauth.md`](mcp-oauth.md)** (le document à donner
à Claude quand il développe un MCP). En bref :

- **Dans Claude** : Paramètres → Connecteurs → Ajouter un connecteur personnalisé
  → `https://recettes.super-nono.cc/api/mcp`, **Client ID et secret vides** →
  Se connecter → connexion Authentik (rien si tu l'es déjà) → « Autoriser ».
  **Claude Code** : `claude mcp add --transport http recettes https://recettes.super-nono.cc/api/mcp`, puis `/mcp`.
- **Le site est son propre guichet OAuth** (modèle RecipeLog) : fiches de
  découverte, inscription (DCR et CIMD), page « Autoriser ? », jetons signés
  (1 h, renouvelés 60 jours). Rien à créer dans Authentik.
- **La page « Autoriser ? » reste derrière Authentik** : c'est là que tu te
  connectes. Le reste du guichet passe par les chemins publics.
- **Bouton « Déconnecter Claude »** : coupe d'un coup tous les connecteurs du site.
- Les jetons `SERVICE_JETONS` restent acceptés sur `/api/mcp`, pour un script.

```
Claude ──POST /api/mcp──────────────────────────────▶ site : 401 + où se connecter
Claude ──/.well-known/… puis /api/mcp/oauth/inscription▶ site : fiches, identifiant cl_…
Toi    ──/api/mcp/oauth/autoriser (Authentik, puis « Autoriser ? »)──▶ code pour Claude
Claude ──/api/mcp/oauth/jeton (PKCE)────────────────▶ site : jeton ra_… signé
Claude ──POST /api/mcp + Authorization: Bearer ra_…──▶ site : vérifie la signature → c'est Noë
```

### Ce que le site doit faire

1. Chemins publics dans le hub (valeurs par défaut des nouveaux sites) :
   `/api/mcp$`, `/api/mcp/oauth/inscription`, `/api/mcp/oauth/jeton`,
   `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`.
   Le `$` final veut dire « ce chemin exactement » : `/api/mcp/oauth/autoriser` reste protégé.
2. Code :

```python
import passerelle_auth as pa

pa.brancher_mcp(app, nom="Recettes")   # tout le guichet OAuth

@app.route("/api/mcp", methods=["GET", "POST", "OPTIONS"])
@pa.mcp_requis                          # 401 + « Se connecter » si pas de jeton valide
def mcp():
    u = pa.utilisateur()                # la personne connectée dans Claude (None pour une machine)
    ...
```

   En TypeScript : `guichetMcp(req, "Recettes")` en tête du routeur et
   `accesMcp(req)` dans la route `/api/mcp` (exemples Next.js et Express en bas du fichier).
3. `.env` : `PASSERELLE_URL_SITE=https://recettes.super-nono.cc`,
   `PASSERELLE_MCP_SECRET=` (`openssl rand -hex 32`), et si besoin
   `PASSERELLE_MCP_POUR=tous` (par défaut seuls les admins peuvent brancher Claude).

## À savoir : comptes, groupes et pages ouvertes à tous

### Plusieurs comptes dans un site

Oui, chaque personne a son compte. Authentik connecte la personne, le site la
reconnaît par son **e-mail** (`utilisateur().email`) et crée son compte local à
la première visite. Rien à inscrire à la main, pas de mot de passe dans le site.

Exemple prêt à copier : [`exemple_comptes.py`](../hub/hub/static/kit/exemple_comptes.py)
(Flask + SQLite) :

- table `comptes` (e-mail, nom, rôle, actif) créée ou mise à jour à chaque visite ;
- `@compte_requis` et `@role_requis("animateur")` sur les routes ;
- le rôle vient des groupes Authentik (`ROLES_PAR_GROUPE`, ex. groupe
  `jeu-animateurs` → rôle `animateur` ; « authentik Admins » → `admin`) : changer
  quelqu'un de groupe dans Authentik suffit. Si tu préfères gérer les rôles dans
  le site, enlève la mise à jour du rôle et garde celui de la table ;
- `actif = 0` suspend quelqu'un **dans ce site seulement** ;
- Claude (MCP) agit au nom du compte qui a cliqué « Autoriser », avec les mêmes
  droits, et est coupé si le compte est suspendu (`regle_mcp`).

En TypeScript, même idée :

```ts
const u = lireUtilisateur(req.headers);            // ou a.utilisateur avec accesMcp
if (!u) return new Response("Connexion requise", { status: 401 });
const compte = await db.compte.upsert({
  where: { email: u.email.toLowerCase() },
  create: { email: u.email.toLowerCase(), nom: u.nom, role: u.admin ? "admin" : "membre" },
  update: { nom: u.nom, vuLe: new Date() },
});
if (!compte.actif) return new Response("Compte suspendu", { status: 403 });
```

Les données de chacun se rangent avec `compte_id`. Bibliothèque **partagée** ou
**séparée par compte** : à décider projet par projet.

### Un compte qui n'entre que sur certains sites

Dans le hub : Sites → le site → **« Réservé à certains groupes »** (ex.
`famille, amis`). Le hub pose la règle dans Authentik : seuls ces groupes (et
les admins) peuvent ouvrir le site, et le hub ne montre à chacun que les sites
qu'il peut ouvrir. Il faut d'abord créer le groupe dans Authentik (Annuaire →
Groupes) et y mettre les personnes.

- Champ vide = **tout compte accepté** entre. Pour un site sensible, mets un
  groupe ou « Réservé aux administrateurs ».
- Une règle sur une personne précise, posée à la main dans Authentik (Applications
  → le site → Policy / Group / User Bindings), est gardée par le hub.
- Le site peut en plus refuser quelqu'un lui-même (compte suspendu).

### Un site avec des pages ouvertes à tous (ex. jeu avec salons par QR code)

Ça marche. Exemple : un site de jeu, panel admin protégé, salons rejoints par
QR code sans compte.

1. Dans le hub, chemins ouverts sans connexion du site jeu :
   ```
   /salon
   /ws
   /static
   ```
   (adapte aux vrais chemins : les pages du salon, le WebSocket, par exemple
   `/socket.io`, et les fichiers CSS/JS/images que ces pages chargent).
2. Le QR code pointe vers `https://jeu.super-nono.cc/salon/<code>` : les
   joueurs entrent directement, sans page de connexion.
3. Tout le reste (`/`, `/admin`, la création de salons) passe par Authentik.
   Dans le code : `@admin_requis` (ou `@role_requis`) sur le panel, et **aucune**
   vérification de compte sur `/salon` et `/ws`.

Les **WebSockets** passent par la passerelle (testé : connexion acceptée sur un
chemin public, renvoyée vers la connexion sur un chemin protégé). Sur un chemin
protégé, le WebSocket marche pour une personne connectée, car le navigateur
envoie son cookie.

Sur un chemin public, ne fais jamais confiance aux en-têtes `X-Authentik-*`
(n'importe qui peut les inventer) : le kit les ignore sans la clé. Un joueur
anonyme se reconnaît par ce que le site lui donne (code du salon, pseudo, jeton
de joueur en cookie), pas par la passerelle. Pense à limiter ce qu'un salon
public peut faire (pas d'action d'admin, taille des messages, nombre de joueurs).

## 5. Un nouveau projet, du début à la fin

1. L'app écoute sur `0.0.0.0:<port>` dans son LXC. Pas de Cloudflare, pas de page de connexion.
2. Copie `passerelle_auth.py` (ou `.ts`) et mets dans son `.env` :
   `PASSERELLE_CLE=...` (hub → Brancher un projet) et, si besoin, `SERVICE_JETONS=...`.
3. Pages : `@connexion_requise` (ou `@admin_requis`). API : `@personne_ou_machine`.
   MCP : `brancher_mcp(app)` + `@mcp_requis` sur `/api/mcp` ([`mcp-oauth.md`](mcp-oauth.md)). Webhooks : signature.
4. Hub → Sites → **Ajouter** : sous-domaine, IP:port, accès, chemins publics
   (les valeurs par défaut couvrent `/api/notify` et le guichet MCP).
5. Pare-feu Proxmox du LXC : la passerelle + les appelants locaux.
6. Sur la passerelle : `hub/hub/static/kit/verifier-site.sh recettes 192.168.1.60:3000 --page-privee /une/page`.
7. Colle `CLAUDE-passerelle.md` dans le `CLAUDE.md` du projet.

## 6. Développer sur ton PC

Sans passerelle, laisse `PASSERELLE_CLE` vide et mets `PASSERELLE_DEV_EMAIL=toi@exemple.fr`
(et `PASSERELLE_DEV_ADMIN=1`) : l'app fait comme si tu étais connecté. Dès que
`PASSERELLE_CLE` est rempli (en production), ce mode est ignoré.

## Si la clé change

`PASSERELLE_CLE` est générée une fois par `installer.sh`. Si tu la changes un
jour, mets la nouvelle valeur dans le `.env` de chaque site, relance-les, puis
lance `./mettre-a-jour.sh` sur la passerelle.

Pour migrer un site existant (et retirer Cloudflare Access), voir aussi
[`migrer-un-site.md`](migrer-un-site.md).
