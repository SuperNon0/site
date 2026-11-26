#!/usr/bin/env bash
# Met à jour la passerelle : nouveau code du hub, nouvelles images, puis reconfigure Authentik
# (sans rien effacer : la configuration est idempotente).
# Usage :  sudo ./mettre-a-jour.sh
set -euo pipefail
cd "$(dirname "$0")"

if git -C .. rev-parse >/dev/null 2>&1; then
  echo "→ Récupération du code"
  git -C .. pull --ff-only
fi
echo "→ Sauvegarde rapide de la base Authentik (data/sauvegardes/)"
mkdir -p data/sauvegardes
docker compose exec -T postgresql pg_dump -U authentik authentik | gzip > "data/sauvegardes/authentik-$(date +%F-%H%M).sql.gz" || true
ls -1t data/sauvegardes/*.sql.gz 2>/dev/null | tail -n +8 | xargs -r rm -f

echo "→ Images et hub"
docker compose pull --quiet postgresql server worker cloudflared
docker compose build --quiet hub
docker compose up -d --remove-orphans

echo "→ Attente d'Authentik"
for i in $(seq 1 90); do
  docker compose exec -T hub python -c "import urllib.request as u; u.urlopen('http://server:9000/-/health/ready/', timeout=3)" >/dev/null 2>&1 && break
  sleep 5
done
docker compose run --rm hub python -m hub.configurer
docker compose restart hub >/dev/null
echo "✅ À jour."
