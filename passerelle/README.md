# Passerelle

Une seule connexion (Authentik) pour tous les sites `*.DOMAINE`, et un hub sur le
domaine principal pour les ouvrir, les ajouter, accepter les demandes d'accès et
gérer les membres. Tout tourne dans un LXC Proxmox, derrière un tunnel Cloudflare.

```
Internet → Cloudflare → tunnel → cloudflared → Authentik (server:9000) ─┬→ hub (DOMAINE)
                                                                       └→ chaque site (IP:port)
```

| Dossier | Rôle |
|---|---|
| `compose.yml` | Postgres, Authentik (server + worker), hub, cloudflared |
| `.env.example` | tous les réglages, commentés |
| `installer.sh` | installe Docker, crée `.env` avec des secrets aléatoires, démarre et configure Authentik |
| `mettre-a-jour.sh` | sauvegarde la base, met à jour, reconfigure |
| `hub/` | l'application Flask du hub (style « Encre & Verre », pensée pour le téléphone) |
| `hub/hub/configurer.py` | configuration idempotente d'Authentik par son API |
| `docs/brancher-un-projet.md` | brancher un projet : personnes, adresse locale, API, webhooks, MCP |
| `docs/mcp-oauth.md` | MCP : le guichet OAuth de chaque site (modèle RecipeLog), à donner à Claude |
| `hub/hub/static/kit/` | le code prêt à copier (Python, TypeScript), le script de vérification, l'extrait de CLAUDE.md |
| `docs/migrer-un-site.md` | brancher un site existant et retirer l'ancien Cloudflare Access |

Installation : `sudo ./installer.sh` (le guide pas à pas complet est donné à part).

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

donne un lien de connexion valable une heure (ajoute devant `https://auth.DOMAINE`).
