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
| **Un client MCP** (Claude) | `https://recettes.super-nono.cc/mcp`, chemin public | l'app seule (OAuth ou jeton) | `machine()` |

Retiens une seule règle : **Authentik ne protège que les personnes dans un
navigateur.** Tout ce qui passe par l'adresse locale ou par un chemin public
arrive sans vérification, et c'est ton application qui décide.

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

## 4. Les connexions MCP

**Claude (claude.ai, appli téléphone ou bureau)** appelle ton serveur MCP depuis
les serveurs d'Anthropic, par Internet. Il ne peut pas passer la page de
connexion d'Authentik, donc :

1. `/mcp` doit être dans les chemins publics du site (c'est le réglage par défaut du hub) ;
2. l'adresse à donner au connecteur est `https://recettes.super-nono.cc/mcp` ;
3. le serveur MCP se protège lui-même, au choix :
   - **OAuth** : si ton app le gère déjà, rien à changer, Claude affiche « Se connecter » ;
   - **jeton dans l'adresse** : `https://recettes.super-nono.cc/mcp?cle=<jeton>`.
     Les connecteurs de claude.ai ne savent pas envoyer d'en-tête, c'est donc la
     façon simple. `machine()` lit `?cle=`. Garde cette adresse comme un mot de passe.

**Claude Code** (sur ton PC ou dans le cloud) sait envoyer un en-tête : préfère-le.

```bash
claude mcp add --transport http recettes https://recettes.super-nono.cc/mcp \
  --header "Authorization: Bearer <jeton>"
```

Sur ton réseau, tu peux aussi viser l'adresse locale (`http://192.168.1.60:3000/mcp`)
si le pare-feu du site autorise ton PC.

Les chemins de connexion OAuth d'un serveur MCP (souvent `/.well-known/...`,
`/authorize`, `/token`, `/register`) doivent aussi être publics : ajoute-les
dans le hub si ton app en a.

---

## 5. Un nouveau projet, du début à la fin

1. L'app écoute sur `0.0.0.0:<port>` dans son LXC. Pas de Cloudflare, pas de page de connexion.
2. Copie `passerelle_auth.py` (ou `.ts`) et mets dans son `.env` :
   `PASSERELLE_CLE=...` (hub → Brancher un projet) et, si besoin, `SERVICE_JETONS=...`.
3. Pages : `@connexion_requise` (ou `@admin_requis`). API : `@personne_ou_machine`.
   `/mcp` et webhooks : jeton, OAuth ou signature.
4. Hub → Sites → **Ajouter** : sous-domaine, IP:port, accès, chemins publics.
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
