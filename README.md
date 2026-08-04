# Homepage

Déploiement **1-commande** de [Homepage](https://gethomepage.dev) — un dashboard
self-hosted — dans un **conteneur LXC** sur Proxmox VE.

Tu lances une seule commande sur ton node, elle crée le conteneur, installe Docker
+ Homepage dedans, et t'affiche l'URL et les identifiants. Pas de VM, pas d'étapes
manuelles.

---

## 🚀 Installation (à lancer sur le shell du node Proxmox)

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/SuperNon0/site/main/install.sh)"
```

> Si le dépôt est **privé**, `curl` ne pourra pas lire le fichier. Deux options :
> rendre le dépôt public, ou copier `install.sh` sur le node (`scp`, copier-coller)
> puis `bash install.sh`.

À la fin, le script affiche :

```
  Accès web        : http://<IP>:3000
  Conteneur LXC    : CTID 120 (hostname : homepage)
  Login SSH/console : utilisateur root
  Mot de passe root : xxxxxxxx (généré — note-le !)
```

---

## ⚙️ Options

Toutes se surchargent en variables d'environnement **avant** la commande :

```bash
CTID=120 DISTRO=ubuntu RAM_MB=512 CT_STORAGE=local-zfs \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/SuperNon0/site/main/install.sh)"
```

| Variable | Défaut | Description |
|---|---|---|
| `CTID` | *auto* | ID du conteneur (vide = prochain libre) |
| `HOSTNAME_CT` | `homepage` | nom d'hôte |
| `DISTRO` | `debian` | `debian` (léger, recommandé) ou `ubuntu` |
| `RAM_MB` | `1024` | RAM en Mo |
| `CORES` | `2` | nombre de cœurs |
| `DISK_GB` | `6` | taille disque en Go |
| `BRIDGE` | `vmbr0` | pont réseau Proxmox |
| `CT_STORAGE` | `local-lvm` | stockage du rootfs (⚠️ à adapter, ex. `local-zfs`) |
| `TEMPLATE_STORAGE` | `local` | stockage des templates LXC |
| `CT_IP` | `dhcp` | IP statique possible, ex. `192.168.1.50/24` (avec `CT_GW`) |
| `CT_GW` | — | passerelle si IP statique |
| `CT_PASSWORD` | *généré* | mot de passe root du conteneur |
| `HP_PORT` | `3000` | port web de Homepage |

**Exemple avec IP statique :**

```bash
CT_IP=192.168.1.50/24 CT_GW=192.168.1.1 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/SuperNon0/site/main/install.sh)"
```

---

## 🧩 Après l'installation

### Éditer la configuration

La config vit dans le conteneur, dans `/opt/homepage/config/` (fichiers YAML) :

```bash
pct enter <CTID>                       # entrer dans le conteneur
nano /opt/homepage/config/services.yaml
```

Homepage **recharge la config automatiquement** — pas besoin de redémarrer.

| Fichier | Rôle |
|---|---|
| `settings.yaml` | thème, disposition, langue |
| `services.yaml` | les tuiles + widgets live (Proxmox, Jellyfin, *arr…) |
| `widgets.yaml` | bandeau du haut (système, météo, recherche) |
| `bookmarks.yaml` | raccourcis simples |
| `docker.yaml` | auto-découverte des conteneurs Docker |

### Auto-découverte de tes conteneurs Docker

Comme tes conteneurs sont créés automatiquement, étiquette-les avec des labels
`homepage.*` : ils apparaîtront tout seuls dans le dashboard.

```yaml
# extrait d'un docker-compose.yml d'un de tes services
labels:
  - homepage.group=Médias
  - homepage.name=Jellyfin
  - homepage.icon=jellyfin.png
  - homepage.href=https://jellyfin.local
  - homepage.description=Serveur multimédia
```

### Widgets « live » (statut/stats en direct)

Chaque tuile peut afficher des infos tirées de l'API de l'appli. Exemple Proxmox :

```yaml
- Infrastructure:
    - Proxmox:
        icon: proxmox.png
        href: https://proxmox.local:8006/
        widget:
          type: proxmox
          url: https://proxmox.local:8006
          username: api-user@pam!homepage   # token API Proxmox
          password: xxxxxxxx-xxxx-xxxx-xxxx
          node: pve
```

> Il y a **plus de 100 widgets** : voir la liste sur
> <https://gethomepage.dev/widgets/>

---

## 🔒 Sécurité

Homepage **n'a pas d'authentification par défaut** sur son interface web. Place-le
derrière ton reverse proxy / VPN, ou active une auth. Voir
<https://gethomepage.dev/configs/authentication/>.

---

## 📦 Ce que fait le script, étape par étape

1. Vérifie qu'il tourne bien sur un node Proxmox (commande `pct`).
2. Trouve le prochain ID de conteneur libre.
3. Télécharge le template Debian/Ubuntu si absent.
4. Crée un LXC **non privilégié** avec `nesting=1,keyctl=1` (nécessaire pour Docker).
5. Le démarre, attend l'obtention d'une IP.
6. Installe Docker (script officiel) puis déploie Homepage via `docker compose`.
7. Affiche l'URL et les identifiants.

---

## Renommer le dépôt GitHub

Ce dépôt s'appelle encore `site`. Pour le renommer en `homepage` :
**Settings → General → Repository name → Rename**. (À faire côté GitHub, c'est la
seule étape que le script ne peut pas faire à ta place.)
