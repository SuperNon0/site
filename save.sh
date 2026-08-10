#!/usr/bin/env bash
#
# save.sh — sauvegarde la config LIVE du conteneur VERS GitHub (sens inverse
# de update.sh). À lancer dans le conteneur, ou automatiquement via cron.
#
#   homepage-save
#
# Nécessite un token GitHub (droit d'écriture "Contents") dans :
#   /opt/homepage/git.token   (fichier local, jamais poussé)
#
set -euo pipefail

REPO_HOST="github.com/SuperNon0/site.git"
BRANCH="${BRANCH:-claude/homepage-project-eval-x5skbs}"
SITE_DIR="/opt/homepage/site"
CONFIG_SRC="/opt/homepage/config"
DEST="$SITE_DIR/homepage/config"
TOKEN_FILE="/opt/homepage/git.token"

GRN=$'\e[32m'; YLW=$'\e[33m'; RST=$'\e[0m'

[ -f "$TOKEN_FILE" ] || { echo "${YLW}Token manquant : crée $TOKEN_FILE${RST}"; exit 1; }
TOKEN="$(tr -d ' \n\r' < "$TOKEN_FILE")"

command -v git >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq git; }
git config --global --add safe.directory "$SITE_DIR" 2>/dev/null || true

# clone si absent
if [ ! -d "$SITE_DIR/.git" ]; then
  git clone --branch "$BRANCH" "https://${TOKEN}@${REPO_HOST}" "$SITE_DIR"
fi

cd "$SITE_DIR"
git remote set-url origin "https://x-access-token:${TOKEN}@${REPO_HOST}"
git config user.name  "Homelab Auto-Save"
git config user.email "homelab@super-nono.cc"

# aligne sur le distant, puis applique la config LIVE par-dessus
git fetch --quiet origin "$BRANCH"
git checkout --quiet "$BRANCH" 2>/dev/null || git checkout --quiet -b "$BRANCH" "origin/$BRANCH"
git reset --hard --quiet "origin/$BRANCH"

# copie config live -> repo (jamais .env / logs / sauvegardes)
mkdir -p "$DEST/icons"
cp -f "$CONFIG_SRC"/*.yaml "$DEST"/            2>/dev/null || true
cp -f "$CONFIG_SRC"/*.css  "$DEST"/            2>/dev/null || true
cp -f "$CONFIG_SRC"/*.js   "$DEST"/            2>/dev/null || true
[ -d "$CONFIG_SRC/icons" ] && cp -f "$CONFIG_SRC"/icons/* "$DEST"/icons/ 2>/dev/null || true

git add homepage/config
if git diff --cached --quiet; then
  echo "Rien de neuf à sauvegarder."
  exit 0
fi

git commit --quiet -m "Sauvegarde config Homepage — $(date '+%F %H:%M')"
git push --quiet origin "$BRANCH"
echo "${GRN}✅ Config sauvegardée sur GitHub ($BRANCH).${RST}"
