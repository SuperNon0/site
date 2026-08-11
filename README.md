# super-nono — hub central

Le tableau de bord personnel de `super-nono.cc` : un serveur **Node.js + Express**
qui affiche tes applications rangées en **Catégories → Sections**, avec un mode
admin pour tout gérer depuis le navigateur.

## ✨ Fonctionnalités

- **Hiérarchie** Catégories → Sections → Applications.
- **2 types de tuiles** : *apps* (grande carte icône + titre + description) et
  *raccourcis* (chip compact icône + nom).
- **Icônes au choix** : emoji, **logo custom uploadé**, ou icône **Material Design** (`mdi-…`).
- **Couleur d'accent** par tuile (color picker).
- **Widgets** : horloge + date, météo (Open-Meteo, sans clé — Le Grau-du-Roi).
- **Mode admin protégé par mot de passe** : ajouter / éditer / supprimer /
  réordonner catégories, sections et tuiles. La consultation reste libre.
- **PWA** : s'installe sur l'écran d'accueil et **s'ouvre en plein écran** ;
  les liens s'ouvrent *dans* l'app (pas de mini-navigateur iOS).

## 🚀 Installation (1 commande, sur le node Proxmox)

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/SuperNon0/site/claude/homepage-project-eval-x5skbs/install.sh)"
```

Crée un LXC `hub` (Debian), installe Node + le hub, et affiche à la fin l'URL
et le **mot de passe admin**. Options : `CTID`, `CT_STORAGE`, `ADMIN_PASSWORD`,
`HUB_PORT`… (voir en tête de `install.sh`).

Ensuite, pointe ton reverse proxy `super-nono.cc` vers `IP_DU_CONTENEUR:3000`.

## 🗂️ Structure

```
app/
├── server.js               # serveur Express + API + auth + upload
├── package.json            # express, multer, @mdi/font
├── data/
│   └── default-config.json # contenu initial (tes 9 apps)
└── public/
    ├── index.html          # toute l'interface (thème RecipeLog)
    ├── manifest.json        # PWA
    └── icon-192/512.png
install.sh                   # installeur LXC 1-commande
```

Au premier lancement, `data/config.json` est créé à partir de
`default-config.json`, puis c'est lui qui est modifié via l'interface (il n'est
pas versionné). Les logos uploadés vont dans `data/icons/`.

## 🔧 Développement / mise à jour

Dans le conteneur :

```bash
cd /opt/hub && git pull && cd app && npm install --omit=dev && systemctl restart hub
```

## 🔐 Sécurité

- L'édition exige le **mot de passe admin** (défini à l'installation, stocké dans
  `/opt/hub/hub.env`, jamais sur GitHub).
- Le dépôt étant public, aucun secret n'est committé (`.gitignore`).
