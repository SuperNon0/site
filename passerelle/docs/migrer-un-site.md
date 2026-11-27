# Faire passer un site derrière la passerelle

Une fois le site ajouté dans le hub (**Sites → Ajouter**), Authentik se place devant
lui : seules les personnes connectées (et autorisées) arrivent jusqu'au site.
Authentik ajoute alors à chaque requête :

| En-tête | Contenu |
|---|---|
| `X-Authentik-Email` | l'e-mail de la personne connectée |
| `X-Authentik-Name` | son nom complet |
| `X-Authentik-Username` | son identifiant |
| `X-Authentik-Groups` | ses groupes, séparés par `\|` |
| `X-Passerelle-Cle` | le secret `PASSERELLE_CLE` du `.env` de la passerelle |

> Sur les chemins publics (`/api/notify`, `/api/mcp`…), Authentik laisse passer la
> requête **telle quelle** : n'importe qui peut alors inventer un en-tête
> `X-Authentik-Email`. Un site ne doit donc croire `X-Authentik-Email` **que si**
> `X-Passerelle-Cle` est présent et correct.

## 1. Pare-feu Proxmox (le plus important)

Sur le LXC du site : **Pare-feu → Options → Pare-feu : Oui**, politique d'entrée
**DROP**, puis une règle **ACCEPT** depuis l'IP de la passerelle vers le port du
site (et SSH depuis ton réseau si besoin). Ainsi, plus personne ne peut contourner
Authentik en appelant l'IP du site.

## 2. Lire l'identité dans l'application

Mets `PASSERELLE_CLE` (la même valeur que la passerelle) dans le `.env` du site.

### Python (Flask, ex. BotPanel `cloudflare_access.py`)

```python
import hmac, os
from flask import request

CLE = os.environ.get("PASSERELLE_CLE", "")

def email_connecte() -> str | None:
    """L'e-mail de la personne connectée par Authentik, ou None."""
    recu = request.headers.get("X-Passerelle-Cle", "")
    if not CLE or not hmac.compare_digest(recu, CLE):
        return None
    email = request.headers.get("X-Authentik-Email", "").strip().lower()
    return email or None
```

Remplace l'appel qui vérifiait le JWT Cloudflare (`Cf-Access-Jwt-Assertion`) par
`email_connecte()`. La suite (retrouver le compte par e-mail, rôles…) ne change pas.

### TypeScript / Node (ex. RecipeLogs `cloudflare.ts`)

```ts
import { timingSafeEqual } from "node:crypto";

const CLE = process.env.PASSERELLE_CLE ?? "";

export function emailConnecte(headers: Headers): string | null {
  const recu = headers.get("x-passerelle-cle") ?? "";
  if (!CLE || recu.length !== CLE.length) return null;
  if (!timingSafeEqual(Buffer.from(recu), Buffer.from(CLE))) return null;
  const email = (headers.get("x-authentik-email") ?? "").trim().toLowerCase();
  return email || null;
}
```

### Déconnexion

Le lien « Se déconnecter » d'un site doit pointer vers
`/outpost.goauthentik.io/sign_out` (même domaine que le site).

## 3. Retirer l'ancien système Cloudflare (dans cet ordre)

1. Ouvre `https://<sous-domaine>.DOMAINE` : la page de connexion de la passerelle
   apparaît, puis le site. Vérifie que tu es bien reconnu.
2. Déploie la version du site qui lit `X-Authentik-Email` (étape 2).
3. Cloudflare Zero Trust → **Access → Applications** : supprime l'application du site.
4. Cloudflare → **DNS** : supprime l'enregistrement CNAME propre au site (le
   joker `*` de la passerelle prend le relais).
5. Si le site avait son propre tunnel : supprime ses routes, puis arrête et
   désinstalle `cloudflared` dans son LXC (`systemctl disable --now cloudflared`).

Ne touche jamais au tunnel de la **passerelle**.
