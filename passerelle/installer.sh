#!/usr/bin/env bash
# Installe (ou réinstalle sans rien casser) la passerelle dans ce LXC :
#   Docker, le fichier .env (secrets aléatoires), Authentik + hub + tunnel, puis la configuration d'Authentik.
# Usage :  sudo ./installer.sh
set -euo pipefail
cd "$(dirname "$0")"

vert() { printf '\033[32m%s\033[0m\n' "$*"; }
jaune() { printf '\033[33m%s\033[0m\n' "$*"; }
rouge() { printf '\033[31m%s\033[0m\n' "$*" >&2; }
etape() { printf '\n\033[1m→ %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { rouge "Lance ce script en root (sudo ./installer.sh)."; exit 1; }

# ------------------------------------------------------------------ Docker
etape "Docker"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl openssl >/dev/null
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || { rouge "« docker compose » introuvable."; exit 1; }
vert "  ✓ $(docker --version)"

# ------------------------------------------------------------------ .env
etape "Réglages (.env)"
[ -f .env ] || { cp .env.example .env; vert "  ✓ .env créé à partir de .env.example"; }
chmod 600 .env

lire() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }
ecrire() {  # ecrire CLE valeur (remplace la ligne, ou l'ajoute)
  local cle="$1" val="$2"
  if grep -qE "^$cle=" .env; then
    python3 - "$cle" "$val" <<'PY'
import sys, re
cle, val = sys.argv[1], sys.argv[2]
t = open(".env", encoding="utf-8").read()
t = re.sub(rf"^{re.escape(cle)}=.*$", lambda m: f"{cle}={val}", t, flags=re.M)
open(".env", "w", encoding="utf-8").write(t)
PY
  else
    printf '%s=%s\n' "$cle" "$val" >> .env
  fi
}
aleatoire() { openssl rand -base64 "$1" | tr -d '\n/+=' | cut -c1-"$2"; }

for cle in PG_PASS:36:32 AUTHENTIK_SECRET_KEY:60:60 AUTHENTIK_BOOTSTRAP_TOKEN:48:48 \
           AUTHENTIK_BOOTSTRAP_PASSWORD:36:24 PASSERELLE_CLE:48:48 HUB_SECRET:48:48; do
  nom="${cle%%:*}"; reste="${cle#*:}"; octets="${reste%%:*}"; long="${reste#*:}"
  if [ -z "$(lire "$nom")" ]; then ecrire "$nom" "$(aleatoire "$octets" "$long")"; vert "  ✓ $nom généré"; fi
done

demander() {  # demander CLE "question"
  local cle="$1" question="$2" val
  if [ -z "$(lire "$cle")" ] && [ -t 0 ]; then
    read -r -p "  $question : " val
    [ -n "$val" ] && ecrire "$cle" "$val"
  fi
}
demander ADMIN_EMAIL "Ton adresse e-mail (ton compte administrateur)"
demander CLOUDFLARE_TUNNEL_TOKEN "Jeton du tunnel Cloudflare"
demander SMTP_USER "Brevo : identifiant SMTP (laisser vide pour plus tard)"
demander SMTP_PASSWORD "Brevo : clé SMTP (laisser vide pour plus tard)"

[ -n "$(lire AUTHENTIK_BOOTSTRAP_EMAIL)" ] || ecrire AUTHENTIK_BOOTSTRAP_EMAIL "akadmin@localhost"
if [ -z "$(lire CLOUDFLARE_TUNNEL_TOKEN)" ]; then
  rouge "CLOUDFLARE_TUNNEL_TOKEN est vide dans .env : remplis-le (nano .env) puis relance ./installer.sh"
  exit 1
fi
[ -n "$(lire SMTP_PASSWORD)" ] || jaune "  · Pas de réglages Brevo : les e-mails ne partiront pas (à remplir plus tard dans .env)."
[ -n "$(lire GOOGLE_CLIENT_ID)" ] || jaune "  · Pas de Google : connexion par e-mail + mot de passe seulement (à remplir plus tard dans .env)."

# ------------------------------------------------------------------ dossiers
etape "Dossiers de données"
mkdir -p data/postgres data/authentik data/hub authentik/templates
chown -R 1000:1000 data/authentik data/hub authentik/templates
vert "  ✓ data/ prêt (sauvegarde ce dossier + .env)"

# ------------------------------------------------------------------ démarrage
etape "Démarrage (le premier téléchargement prend quelques minutes)"
docker compose pull --quiet postgresql server worker cloudflared
docker compose build --quiet hub
docker compose up -d

etape "Attente d'Authentik"
for i in $(seq 1 90); do
  if docker compose exec -T hub python -c "import urllib.request as u; u.urlopen('http://server:9000/-/health/ready/', timeout=3)" >/dev/null 2>&1; then
    vert "  ✓ Authentik répond"; break
  fi
  [ "$i" -eq 90 ] && { rouge "Authentik ne répond toujours pas. Regarde : docker compose logs server"; exit 1; }
  sleep 5
done

# ------------------------------------------------------------------ configuration
etape "Configuration d'Authentik"
docker compose run --rm hub python -m hub.configurer
docker compose restart hub >/dev/null

DOMAINE="$(lire DOMAINE)"
vert "
Terminé. Ouvre https://${DOMAINE:-ton-domaine} sur ton téléphone et connecte-toi.
Pour mettre à jour plus tard : ./mettre-a-jour.sh"
