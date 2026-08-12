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
- **Changement du mot de passe admin** depuis l'interface (haché en scrypt).
- **Mise à jour en 1 clic** depuis l'interface (git pull + install + redémarrage).
- **PWA** : s'installe sur l'écran d'accueil et **s'ouvre en plein écran** ;
  les liens s'ouvrent *dans* l'app (pas de mini-navigateur iOS).

## 🚀 Installation (1 commande, sur le node Proxmox)

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/SuperNon0/site/main/install.sh)"
```

Crée un LXC `hub` (Debian), installe Node + le hub, et affiche à la fin l'URL
et le **mot de passe admin**. Options : `CTID`, `CT_STORAGE`, `ADMIN_PASSWORD`,
`HUB_PORT`… (voir en tête de `install.sh`).

Ensuite, pointe ton reverse proxy `super-nono.cc` vers `IP_DU_CONTENEUR:3000`.

## 🛠️ Mode admin

Clique **« Admin »** en haut, entre le mot de passe → la barre affiche :

| Bouton | Rôle |
|---|---|
| **Gérer** | Active l'édition : `+ Catégorie`, `+ Section`, `+App`, `+Raccourci`, renommer (✎), supprimer (✕), réordonner (↑↓). |
| **↻ MàJ** | Récupère la dernière version depuis GitHub (`git pull`) + `npm install` + redémarre le service. Dit « Déjà à jour » s'il n'y a rien de neuf. |
| **Mot de passe** | Change le mot de passe admin (haché, stocké dans `data/auth.json`, jamais sur GitHub). Il remplace celui de l'installation. |
| **Déconnexion** | Ferme la session admin. |

**Ajouter une app / un raccourci** : en mode Gérer, `+App` ou `+Raccourci` sur une
section → choisis l'icône (**Emoji / Logo uploadé / MDI**), le nom, le lien, la
couleur. Le raccourci est une tuile compacte sans description.

## 🔄 Le workflow de mise à jour

1. Une amélioration est poussée sur GitHub.
2. Dans le hub (connecté admin), tu cliques **↻ MàJ** → le conteneur se met à jour
   et redémarre tout seul. Aucune ligne de commande.

*(Alternative en console : `cd /opt/hub && git pull && cd app && npm install --omit=dev && systemctl restart hub`.)*

## 🗂️ Structure

```
app/
├── server.js               # serveur Express + API (config, auth, upload, update)
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
`default-config.json`, puis modifié via l'interface. Les logos uploadés vont dans
`data/icons/`, le mot de passe dans `data/auth.json`. Ces fichiers runtime **ne
sont pas versionnés** (`.gitignore`).

## 🔌 API

| Méthode | Route | Auth | Rôle |
|---|---|---|---|
| GET | `/api/config` | non | Récupère la configuration (affichage). |
| GET | `/api/status` | non | Indique si un mot de passe est configuré. |
| POST | `/api/login` | non | Connexion admin → renvoie un jeton. |
| PUT | `/api/config` | oui | Enregistre la configuration. |
| POST | `/api/upload` | oui | Upload d'un logo custom. |
| POST | `/api/change-password` | oui | Change le mot de passe admin. |
| POST | `/api/update` | oui | Met à jour et redémarre le hub. |

## 🔐 Sécurité

- L'édition exige le **mot de passe admin** (défini à l'installation, puis
  modifiable dans l'UI ; stocké haché dans le conteneur, jamais sur GitHub).
- Le dépôt étant public, aucun secret n'est committé (`.gitignore`).
