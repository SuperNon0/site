# Passerelle super-nono.cc

**Une seule connexion pour tous tes sites.** Authentik connecte les gens une fois
(Google ou e-mail + mot de passe), puis ils ouvrent n'importe quel site
`*.super-nono.cc` sans se reconnecter. Un **hub**, sur `super-nono.cc`, montre à
chacun ses sites, et sert aux admins à ajouter un site, accepter les demandes
d'accès et gérer les membres.

Tout tourne dans **un seul LXC Proxmox** (la « passerelle »), derrière un tunnel
Cloudflare. Les sites restent dans leurs LXC, inchangés : seul le pare-feu
Proxmox change (seule la passerelle entre).

```
Internet → Cloudflare → tunnel → cloudflared → Authentik (server:9000) ─┬→ hub (super-nono.cc)
                                                                       └→ chaque site (IP:port de son LXC)
```

---

## Par où commencer

| Tu veux… | Ouvre |
|---|---|
| **Installer la passerelle**, pas à pas | le guide : [`docs/guide-installation.html`](docs/guide-installation.html) (aussi en page en ligne privée, lien dans le fil du projet) |
| **Créer un nouveau site** avec le design et la connexion | le prompt à copier : [`prompts/nouveau-site.md`](prompts/nouveau-site.md) |
| **Mettre un site existant à niveau** | le prompt à copier : [`prompts/mettre-a-niveau.md`](prompts/mettre-a-niveau.md) |
| Comprendre comment un site se branche (personnes, adresse locale, API, comptes, groupes, pages publiques) | [`docs/brancher-un-projet.md`](docs/brancher-un-projet.md) |
| Connecter Claude à un site (MCP) | [`docs/mcp-oauth.md`](docs/mcp-oauth.md) |
| Passer un site de Cloudflare Access à la passerelle | [`docs/migrer-un-site.md`](docs/migrer-un-site.md) |

## Ce que ça fait

- **Connexion unique** : Authentik, avec Google et e-mail + mot de passe.
- **Inscription sur demande** : formulaire (e-mail, nom, prénom) → notification
  Discord par BotPanel avec **Accepter / Refuser** → la personne reçoit un
  e-mail (Brevo) pour choisir son mot de passe, ou un message de refus.
- **Hub** pensé pour le téléphone, au design « Encre & Verre » : boutons vers
  les sites, ajout / modification / suppression d'un site (nom, sous-domaine,
  IP:port), demandes d'accès, membres, journal, réglages d'apparence.
- **Accès par site** : tous les membres, les admins seulement, ou **certains
  groupes** (ex. `famille`). Le hub ne montre à chacun que les sites qu'il peut
  ouvrir.
- **Chemins ouverts sans connexion** par site : notifications, webhooks, MCP,
  pages publiques (salons de jeu par QR code, WebSockets compris).
- **Kit pour les projets** : le code prêt à copier pour qu'un site lise
  l'identité, gère ses comptes, accepte des machines et un MCP par OAuth.

## Ordre d'installation (résumé du guide)

1. **Brevo** : authentifier `super-nono.cc`, créer une clé SMTP (expéditeur `acces@super-nono.cc`).
2. **Google** (facultatif) : identifiants OAuth pour « Se connecter avec Google ».
3. **Cloudflare** : un tunnel, route `*.super-nono.cc` et `super-nono.cc` → `http://server:9000`.
4. **Proxmox** : un LXC Debian 13 (nesting + keyctl, 2 cœurs, 4 Go, 16 Go).
5. **Installer** : `sudo ./installer.sh` (crée `.env` avec des secrets, démarre, configure Authentik).
6. **Premier essai** : connexion au hub avec ton compte admin.
7. **BotPanel** : notification `hub_demande` avec boutons Accepter / Refuser.
8. **Tes sites** : les ajouter dans le hub, puis fermer leur pare-feu (seule la passerelle entre).
9. **Ménage** : retirer l'ancien Cloudflare Access, site par site.

Ensuite, à chaque changement : `./mettre-a-jour.sh`.

## Ce qu'il y a dans ce dossier

| Chemin | Rôle |
|---|---|
| `compose.yml` | Postgres, Authentik 2026.8 (server + worker), hub, cloudflared |
| `.env.example` | tous les réglages, commentés |
| `installer.sh` | installe Docker, crée `.env`, démarre et configure Authentik |
| `mettre-a-jour.sh` | sauvegarde la base, met à jour, reconfigure |
| `hub/` | l'application Flask du hub |
| `hub/hub/configurer.py` | configuration d'Authentik par son API (relançable sans risque) |
| `hub/hub/static/kit/` | **le kit des projets** : `passerelle_auth.py`, `passerelle-auth.ts`, `exemple_comptes.py`, `verifier-site.sh`, `CLAUDE-passerelle.md` |
| `hub/hub/static/encre/` | **le design Encre & Verre 1.1** complet : `LISEZMOI.md`, `demo.html`, `modele.html`, `encre.css`, `verre.css`, `encre.js`, `icones.svg`, polices, Tailwind |
| `docs/` | le guide d'installation et les documents ci-dessus |
| `prompts/` | les deux prompts à copier dans Claude Code |

Le kit et le design se téléchargent aussi depuis le hub : **Plus → Brancher un projet**.

## Les règles à retenir

- Un site ne croit les en-têtes `X-Authentik-*` **que** si `X-Passerelle-Cle`
  vaut `PASSERELLE_CLE` : sur un chemin public, n'importe qui peut les inventer.
  Le kit s'en charge, ne lis jamais ces en-têtes à la main.
- Pas de page de connexion ni de mot de passe dans les sites.
- Les machines (BotPanel, Home Assistant, autres sites) passent par l'adresse
  locale avec un jeton `SERVICE_JETONS` ; le pare-feu doit les laisser passer.
- Les notifications passent par BotPanel, jamais par Discord en direct.
- Le MCP de chaque site est son propre guichet OAuth : rien à créer dans Authentik.

## Développement

```bash
cd hub && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
HUB_DEV=1 DATA_DIR=/tmp/hub flask --app "hub:create_app()" run --port 8000
```

`HUB_DEV=1` simule un visiteur connecté (admin) sans Authentik : jamais en production.

## Sauvegarde

Tout l'état est dans `data/` et `.env`. Sauvegarde le LXC avec Proxmox (vzdump),
ou au minimum ces deux éléments. `mettre-a-jour.sh` garde aussi les 7 derniers
dumps de la base Authentik dans `data/sauvegardes/`.

## Récupérer l'accès administrateur

```bash
docker compose exec worker ak create_recovery_key 60 akadmin
```

Donne un lien de connexion valable une heure (ajoute `https://auth.super-nono.cc` devant).
