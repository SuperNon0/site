#!/usr/bin/env bash
#
# install.sh — Déploiement 1-commande du hub super-nono.cc dans un LXC Proxmox.
# À LANCER SUR LE SHELL DU NODE PROXMOX.
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/SuperNon0/site/main/install.sh)"
#
# Options surchargeables :  CTID=130 CT_STORAGE=local-zfs bash -c "$(curl ...)"
#
set -euo pipefail

REPO_URL="https://github.com/SuperNon0/site.git"
BRANCH="${BRANCH:-main}"

CTID="${CTID:-}"
HOSTNAME_CT="${HOSTNAME_CT:-hub}"
DISTRO_TEMPLATE_PREFIX="debian-12-standard"
RAM_MB="${RAM_MB:-1024}"
CORES="${CORES:-2}"
DISK_GB="${DISK_GB:-6}"
BRIDGE="${BRIDGE:-vmbr0}"
CT_STORAGE="${CT_STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
CT_IP="${CT_IP:-dhcp}"
CT_GW="${CT_GW:-}"
CT_PASSWORD="${CT_PASSWORD:-}"
HUB_PORT="${HUB_PORT:-3000}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

RED=$'\e[31m';GRN=$'\e[32m';YLW=$'\e[33m';BLU=$'\e[34m';BLD=$'\e[1m';RST=$'\e[0m'
info(){ echo "${BLU}${BLD}[i]${RST} $*"; }
ok(){ echo "${GRN}${BLD}[✓]${RST} $*"; }
die(){ echo "${RED}${BLD}[✗]${RST} $*" >&2; exit 1; }

command -v pct >/dev/null 2>&1 || die "'pct' introuvable. Lance ce script SUR LE NODE PROXMOX."
[ "$(id -u)" -eq 0 ] || die "À lancer en root."

[ -z "$CTID" ] && CTID="$(pvesh get /cluster/nextid)"
pct status "$CTID" >/dev/null 2>&1 && die "Le conteneur $CTID existe déjà."
info "Conteneur cible : CTID=$CTID  hostname=$HOSTNAME_CT"

# Template
pveam update >/dev/null 2>&1 || true
TEMPLATE_FILE="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{print $1}' | grep "/${DISTRO_TEMPLATE_PREFIX}" | head -n1 || true)"
if [ -z "$TEMPLATE_FILE" ]; then
  AVAIL="$(pveam available --section system | awk '{print $2}' | grep "^${DISTRO_TEMPLATE_PREFIX}" | sort | tail -n1)"
  [ -n "$AVAIL" ] || die "Template Debian 12 introuvable."
  info "Téléchargement du template $AVAIL…"; pveam download "$TEMPLATE_STORAGE" "$AVAIL"
  TEMPLATE_FILE="${TEMPLATE_STORAGE}:vztmpl/${AVAIL}"
fi
ok "Template : $TEMPLATE_FILE"

[ -z "$CT_PASSWORD" ] && { CT_PASSWORD="$(openssl rand -base64 12)"; GEN_ROOT=1; }
[ -z "$ADMIN_PASSWORD" ] && { ADMIN_PASSWORD="$(openssl rand -base64 9 | tr -d '/+=')"; GEN_ADMIN=1; }

if [ "$CT_IP" = "dhcp" ]; then NETCFG="name=eth0,bridge=${BRIDGE},ip=dhcp";
else [ -n "$CT_GW" ] || die "IP statique sans passerelle (CT_GW)."; NETCFG="name=eth0,bridge=${BRIDGE},ip=${CT_IP},gw=${CT_GW}"; fi

info "Création du conteneur LXC…"
pct create "$CTID" "$TEMPLATE_FILE" \
  --hostname "$HOSTNAME_CT" --cores "$CORES" --memory "$RAM_MB" --swap "$RAM_MB" \
  --rootfs "${CT_STORAGE}:${DISK_GB}" --net0 "$NETCFG" --unprivileged 1 \
  --features "nesting=1" --password "$CT_PASSWORD" --onboot 1 \
  --description "Hub super-nono.cc"
ok "Conteneur $CTID créé."
pct start "$CTID"

info "Attente d'une IP…"
CT_IP_ADDR=""
for _ in $(seq 1 30); do CT_IP_ADDR="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)"; [ -n "$CT_IP_ADDR" ] && break; sleep 2; done
[ -n "$CT_IP_ADDR" ] || die "Pas d'IP obtenue."
ok "IP : $CT_IP_ADDR"

info "Installation de Node + du hub (1-3 min)…"
INNER="$(cat <<INNER
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nodejs npm git >/dev/null
git clone --depth 1 --branch "$BRANCH" "$REPO_URL" /opt/hub >/dev/null 2>&1
cd /opt/hub/app
npm install --omit=dev --no-audit --no-fund >/dev/null 2>&1

cat > /opt/hub/hub.env <<ENV
HUB_ADMIN_PASSWORD=${ADMIN_PASSWORD}
PORT=${HUB_PORT}
ENV
chmod 600 /opt/hub/hub.env

cat > /etc/systemd/system/hub.service <<UNIT
[Unit]
Description=Hub super-nono.cc
After=network.target
[Service]
WorkingDirectory=/opt/hub/app
EnvironmentFile=/opt/hub/hub.env
ExecStart=/usr/bin/node server.js
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now hub >/dev/null 2>&1
INNER
)"
pct exec "$CTID" -- bash -c "$INNER"
ok "Hub installé et démarré."

echo
echo "${GRN}${BLD}══════════════════════════════════════════════════════════════${RST}"
echo "${GRN}${BLD}  Hub super-nono.cc prêt ! 🎉${RST}"
echo "${GRN}${BLD}══════════════════════════════════════════════════════════════${RST}"
echo
echo "  ${BLD}Accès web${RST}       : ${BLU}http://${CT_IP_ADDR}:${HUB_PORT}${RST}"
echo "  ${BLD}Conteneur LXC${RST}   : CTID ${CTID}  (hostname : ${HOSTNAME_CT})"
echo "  ${BLD}Login SSH/console${RST}: root${GEN_ROOT:+  /  mot de passe : ${YLW}${CT_PASSWORD}${RST}}"
echo "  ${BLD}Mot de passe ADMIN du hub${RST} : ${YLW}${ADMIN_PASSWORD}${RST}   ${YLW}(pour éditer le hub — note-le !)${RST}"
echo
echo "  Pointe ton reverse proxy (super-nono.cc) vers ${CT_IP_ADDR}:${HUB_PORT}"
echo "  Mise à jour ultérieure : pct enter ${CTID}  puis  cd /opt/hub && git pull && cd app && npm install --omit=dev && systemctl restart hub"
echo
