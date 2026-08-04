#!/usr/bin/env bash
#
# install.sh — Déploiement 1-commande de Homepage (https://gethomepage.dev)
# dans un conteneur LXC sur un node Proxmox VE.
#
# À LANCER SUR LE SHELL DU NODE PROXMOX (pas dans un conteneur).
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/SuperNon0/site/main/install.sh)"
#
# Toutes les options ci-dessous se surchargent en variables d'environnement :
#   CTID=120 DISTRO=ubuntu RAM_MB=512 CT_STORAGE=local-zfs bash -c "$(curl -fsSL ...)"
#
set -euo pipefail

# ────────────────────────────── OPTIONS ──────────────────────────────
CTID="${CTID:-}"                       # ID du conteneur (vide = prochain libre auto)
HOSTNAME_CT="${HOSTNAME_CT:-homepage}" # nom d'hôte du conteneur
DISTRO="${DISTRO:-debian}"             # debian | ubuntu
RAM_MB="${RAM_MB:-1024}"               # RAM en Mo
CORES="${CORES:-2}"                    # nombre de cœurs
DISK_GB="${DISK_GB:-6}"                # taille disque en Go
BRIDGE="${BRIDGE:-vmbr0}"              # pont réseau
CT_STORAGE="${CT_STORAGE:-local-lvm}"       # stockage du rootfs (contenu "rootdir")
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}" # stockage des templates (contenu "vztmpl")
UNPRIVILEGED="${UNPRIVILEGED:-1}"      # 1 = conteneur non privilégié (recommandé)

# Réseau : DHCP par défaut. Pour une IP statique, renseigne CT_IP (avec /CIDR) et CT_GW :
#   CT_IP=192.168.1.50/24 CT_GW=192.168.1.1
CT_IP="${CT_IP:-dhcp}"
CT_GW="${CT_GW:-}"

# Mot de passe root du conteneur (vide = généré aléatoirement)
CT_PASSWORD="${CT_PASSWORD:-}"

# Port web de Homepage
HP_PORT="${HP_PORT:-3000}"
# ──────────────────────────────────────────────────────────────────────

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[34m'; BLD=$'\e[1m'; RST=$'\e[0m'
info() { echo "${BLU}${BLD}[i]${RST} $*"; }
ok()   { echo "${GRN}${BLD}[✓]${RST} $*"; }
warn() { echo "${YLW}${BLD}[!]${RST} $*"; }
die()  { echo "${RED}${BLD}[✗]${RST} $*" >&2; exit 1; }

# ── Préflight ──────────────────────────────────────────────────────────
command -v pct >/dev/null 2>&1 || die "Commande 'pct' introuvable. Lance ce script SUR LE NODE PROXMOX."
[ "$(id -u)" -eq 0 ] || die "Ce script doit être lancé en root."

case "$DISTRO" in
  debian) TEMPLATE_PREFIX="debian-12-standard" ;;
  ubuntu) TEMPLATE_PREFIX="ubuntu-24.04-standard" ;;
  *) die "DISTRO doit valoir 'debian' ou 'ubuntu' (reçu: $DISTRO)" ;;
esac

# ── ID du conteneur ────────────────────────────────────────────────────
if [ -z "$CTID" ]; then
  CTID="$(pvesh get /cluster/nextid)"
fi
pct status "$CTID" >/dev/null 2>&1 && die "Le conteneur $CTID existe déjà. Choisis un autre CTID."
info "Conteneur cible : ${BLD}CTID=$CTID${RST}  hostname=$HOSTNAME_CT  distro=$DISTRO"

# ── Template ───────────────────────────────────────────────────────────
info "Vérification du template $TEMPLATE_PREFIX…"
pveam update >/dev/null 2>&1 || true
TEMPLATE_FILE="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{print $1}' | grep "/${TEMPLATE_PREFIX}" | head -n1 || true)"
if [ -z "$TEMPLATE_FILE" ]; then
  AVAIL="$(pveam available --section system | awk '{print $2}' | grep "^${TEMPLATE_PREFIX}" | sort | tail -n1 || true)"
  [ -n "$AVAIL" ] || die "Aucun template $TEMPLATE_PREFIX disponible sur $TEMPLATE_STORAGE."
  info "Téléchargement du template $AVAIL sur $TEMPLATE_STORAGE…"
  pveam download "$TEMPLATE_STORAGE" "$AVAIL"
  TEMPLATE_FILE="${TEMPLATE_STORAGE}:vztmpl/${AVAIL}"
fi
ok "Template : $TEMPLATE_FILE"

# ── Mot de passe ───────────────────────────────────────────────────────
if [ -z "$CT_PASSWORD" ]; then
  CT_PASSWORD="$(openssl rand -base64 12 2>/dev/null || head -c 12 /dev/urandom | base64)"
  GENERATED_PW=1
fi

# ── Création du conteneur ──────────────────────────────────────────────
if [ "$CT_IP" = "dhcp" ]; then
  NETCFG="name=eth0,bridge=${BRIDGE},ip=dhcp"
else
  [ -n "$CT_GW" ] || die "IP statique demandée (CT_IP=$CT_IP) mais CT_GW (passerelle) est vide."
  NETCFG="name=eth0,bridge=${BRIDGE},ip=${CT_IP},gw=${CT_GW}"
fi

info "Création du conteneur LXC…"
pct create "$CTID" "$TEMPLATE_FILE" \
  --hostname "$HOSTNAME_CT" \
  --cores "$CORES" \
  --memory "$RAM_MB" \
  --swap "$RAM_MB" \
  --rootfs "${CT_STORAGE}:${DISK_GB}" \
  --net0 "$NETCFG" \
  --unprivileged "$UNPRIVILEGED" \
  --features "nesting=1,keyctl=1" \
  --password "$CT_PASSWORD" \
  --onboot 1 \
  --description "Homepage dashboard — déployé automatiquement"
ok "Conteneur $CTID créé."

info "Démarrage du conteneur…"
pct start "$CTID"

# ── Attente réseau ─────────────────────────────────────────────────────
info "Attente d'une IP…"
CT_IP_ADDR=""
for _ in $(seq 1 30); do
  CT_IP_ADDR="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)"
  [ -n "$CT_IP_ADDR" ] && break
  sleep 2
done
[ -n "$CT_IP_ADDR" ] || die "Le conteneur n'a pas obtenu d'IP. Vérifie le pont/réseau ($BRIDGE)."
ok "IP du conteneur : $CT_IP_ADDR"

# ── Installation à l'intérieur du conteneur ────────────────────────────
info "Installation de Docker + Homepage dans le conteneur (peut prendre 1-2 min)…"

INNER_SETUP="$(cat <<INNER
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl >/dev/null

# Docker (script officiel)
curl -fsSL https://get.docker.com | sh >/dev/null 2>&1
systemctl enable --now docker >/dev/null 2>&1

mkdir -p /opt/homepage/config

# docker-compose.yml
cat > /opt/homepage/docker-compose.yml <<'COMPOSE'
services:
  homepage:
    image: ghcr.io/gethomepage/homepage:latest
    container_name: homepage
    restart: unless-stopped
    ports:
      - "${HP_PORT}:3000"
    volumes:
      - /opt/homepage/config:/app/config
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      - HOMEPAGE_ALLOWED_HOSTS=${CT_IP_ADDR}:${HP_PORT},localhost:${HP_PORT}
COMPOSE

# Config par défaut (uniquement si le dossier est vide)
if [ -z "\$(ls -A /opt/homepage/config 2>/dev/null)" ]; then
  cat > /opt/homepage/config/settings.yaml <<'SETTINGS'
title: Mon Homelab
theme: dark
color: slate
language: fr
headerStyle: boxed
hideVersion: true
layout:
  Infrastructure:
    style: row
    columns: 3
  Services:
    style: row
    columns: 4
SETTINGS

  cat > /opt/homepage/config/widgets.yaml <<'WIDGETS'
- resources:
    label: Système
    cpu: true
    memory: true
    disk: /
- search:
    provider: duckduckgo
    target: _blank
- datetime:
    text_size: xl
    format:
      dateStyle: long
      timeStyle: short
      hourCycle: h23
WIDGETS

  cat > /opt/homepage/config/services.yaml <<'SERVICES'
# Ajoute tes services ici, ou étiquette tes conteneurs Docker avec
# des labels homepage.* pour qu'ils apparaissent automatiquement.
# Doc : https://gethomepage.dev/configs/services/
- Infrastructure:
    - Proxmox:
        icon: proxmox.png
        href: https://proxmox.local:8006/
        description: Hyperviseur
SERVICES

  cat > /opt/homepage/config/bookmarks.yaml <<'BOOKMARKS'
- Liens:
    - Documentation Homepage:
        - abbr: HP
          href: https://gethomepage.dev/
BOOKMARKS

  cat > /opt/homepage/config/docker.yaml <<'DOCKER'
# Auto-découverte des conteneurs Docker locaux via leurs labels homepage.*
my-docker:
  socket: /var/run/docker.sock
DOCKER
fi

cd /opt/homepage
docker compose up -d >/dev/null 2>&1
INNER
)"

# On passe HP_PORT et CT_IP_ADDR dans l'environnement du shell interne
pct exec "$CTID" -- bash -c "HP_PORT='$HP_PORT' CT_IP_ADDR='$CT_IP_ADDR' bash -s" <<< "$INNER_SETUP"
ok "Homepage installé et démarré."

# ── Récapitulatif ──────────────────────────────────────────────────────
echo
echo "${GRN}${BLD}══════════════════════════════════════════════════════════════${RST}"
echo "${GRN}${BLD}  Homepage est prêt ! 🎉${RST}"
echo "${GRN}${BLD}══════════════════════════════════════════════════════════════${RST}"
echo
echo "  ${BLD}Accès web${RST}      : ${BLU}http://${CT_IP_ADDR}:${HP_PORT}${RST}"
echo "  ${BLD}Conteneur LXC${RST}  : CTID ${CTID}  (hostname : ${HOSTNAME_CT})"
echo "  ${BLD}Distribution${RST}   : ${DISTRO}"
echo "  ${BLD}Ressources${RST}     : ${CORES} cœurs / ${RAM_MB} Mo RAM / ${DISK_GB} Go disque"
echo "  ${BLD}Login SSH/console${RST} : utilisateur ${BLD}root${RST}"
if [ "${GENERATED_PW:-0}" = "1" ]; then
  echo "  ${BLD}Mot de passe root${RST} : ${YLW}${CT_PASSWORD}${RST}   ${YLW}(généré — note-le !)${RST}"
else
  echo "  ${BLD}Mot de passe root${RST} : (celui que tu as fourni)"
fi
echo
echo "  ${BLD}Config Homepage${RST} : dans le conteneur → /opt/homepage/config/*.yaml"
echo "  ${BLD}Éditer${RST}          : pct enter ${CTID}   puis   nano /opt/homepage/config/services.yaml"
echo "  ${BLD}Appliquer${RST}       : Homepage recharge la config tout seul (pas de redémarrage)."
echo
echo "  Homepage n'a ${BLD}pas de mot de passe par défaut${RST} sur l'interface web."
echo "  Mets-le derrière ton reverse proxy / VPN, ou active une auth."
echo "  Doc : https://gethomepage.dev/"
echo
