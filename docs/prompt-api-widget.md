# Ajouter une mini-API « stats » pour mon dashboard (Homepage)

> Texte à transmettre au développeur de l'outil. Objectif : afficher quelques
> chiffres en direct de l'application sur mon tableau de bord Homepage, via son
> widget « Custom API ».

Bonjour,

J'affiche mes services sur un dashboard [Homepage](https://gethomepage.dev) et
j'aimerais y montrer **2 à 4 statistiques en direct** de l'application. Homepage
sait lire n'importe quelle API JSON — il te suffit d'exposer **un petit endpoint**.

## Ce qu'il faut créer

Une route **HTTP GET** qui renvoie un **objet JSON plat** avec quelques
valeurs simples (nombres de préférence).

- **Méthode** : `GET`
- **Chemin suggéré** : `/api/stats` (ou `/api/homepage`)
- **En-tête** : `Content-Type: application/json`
- **Réponse rapide** (c'est appelé toutes les ~10 s) et **légère**.
- **Pas de CORS à gérer** : Homepage appelle l'API côté serveur, pas depuis le navigateur.

### Exemple de réponse attendue

```json
{
  "utilisateurs": 128,
  "elements": 542,
  "actifs_24h": 17,
  "statut": "ok"
}
```

Un objet plat (clé → valeur) est l'idéal. Si c'est imbriqué, ce n'est pas
bloquant (Homepage sait descendre dans l'arbre), mais le plus simple est mieux.

## Authentification

Au choix, dans l'ordre de préférence :

1. **Aucune** si l'endpoint est déjà protégé en amont (reverse proxy /
   Cloudflare Zero Trust / réseau local). Le plus simple.
2. **Une clé statique** transmise dans un **en-tête HTTP** (ex.
   `X-API-Key: <clé>`) ou en paramètre d'URL (`?key=<clé>`). Homepage peut
   envoyer des en-têtes personnalisés — indique-moi juste le nom de l'en-tête.

Merci d'éviter une auth par session/cookie ou OAuth : Homepage fait un simple
appel GET, sans login interactif.

## Ce que je te demande en retour

Pour que je branche le widget, renvoie-moi :

1. **L'URL exacte** de l'endpoint (ex. `https://monapp.super-nono.cc/api/stats`).
2. **La méthode d'auth** (aucune / en-tête `X-API-Key` / paramètre `?key=`).
3. **La liste des champs** que tu exposes, avec pour chacun :
   - le **nom du champ** JSON (ex. `utilisateurs`),
   - un **libellé** lisible (ex. « Utilisateurs »),
   - le **type** (nombre, texte, pourcentage, octets, durée, date).

## Comment je vais l'utiliser (pour info)

Côté Homepage, ça donne une config de ce genre — tu n'as rien à faire dessus,
c'est juste pour que tu voies comment tes champs sont consommés :

```yaml
widget:
  type: customapi
  url: https://monapp.super-nono.cc/api/stats
  refreshInterval: 10000
  headers:
    X-API-Key: "<clé si nécessaire>"
  mappings:
    - { field: utilisateurs, label: Utilisateurs, format: number }
    - { field: elements,     label: Éléments,     format: number }
    - { field: actifs_24h,   label: Actifs 24h,    format: number }
```

Formats disponibles côté Homepage : `text`, `number`, `percent`, `bytes`,
`duration`, `date`. Donc renvoie des valeurs brutes (ex. un nombre `128`, pas
« 128 utilisateurs »), je m'occupe de la mise en forme.

Merci beaucoup !
