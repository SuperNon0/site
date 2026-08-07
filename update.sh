#!/usr/bin/env bash
#
# update.sh — met à jour la config Homepage depuis ce dépôt GitHub.
# À lancer DANS le conteneur Homepage (root@homepage:~#).
#
# Usage direct (une commande) :
#   curl -fsSL https://raw.githubusercontent.com/SuperNon0/site/claude/homepage-project-eval-x5skbs/update.sh | bash
#
# Ou, après le bootstrap, simplement :  homepage-update
#
set -euo pipefail

REPO_URL="https://github.com/SuperNon0/site.git"
BRANCH="${BRANCH:-claude/homepage-project-eval-x5skbs}"
SITE_DIR="/opt/homepage/site"
CONFIG_DST="/opt/homepage/config"

GRN=$'\e[32m'; BLU=$'\e[34m'; RST=$'\e[0m'

# git dispo ?
command -v git >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq git; }

# clone la première fois, sinon met à jour
if [ ! -d "$SITE_DIR/.git" ]; then
  echo "${BLU}Clone du dépôt…${RST}"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$SITE_DIR"
else
  echo "${BLU}Récupération des dernières modifs…${RST}"
  git -C "$SITE_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$SITE_DIR" reset --hard "origin/$BRANCH"
fi

# copie la config (yaml + css) — n'écrase JAMAIS ton .env local
mkdir -p "$CONFIG_DST"
cp -f "$SITE_DIR"/homepage/config/*.yaml "$CONFIG_DST"/ 2>/dev/null || true
cp -f "$SITE_DIR"/homepage/config/*.css  "$CONFIG_DST"/ 2>/dev/null || true

# recharge Homepage
docker restart homepage >/dev/null 2>&1 || true

echo "${GRN}✅ Homepage mis à jour depuis GitHub (branche $BRANCH). Rafraîchis la page (Ctrl+F5).${RST}"
