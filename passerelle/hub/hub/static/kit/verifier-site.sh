#!/usr/bin/env bash
# Vérifie qu'un site est bien branché derrière la passerelle.
#
#   ./verifier-site.sh <sous-domaine> <ip:port> [options]
#
# Options :
#   --domaine super-nono.cc        (sinon lu dans le .env de la passerelle, sinon super-nono.cc)
#   --chemins "/api/notify /mcp"   chemins publics à tester (défaut : ces deux-là)
#   --page-privee /api/moi         une adresse de TON app qui doit refuser un inconnu (401/403/302)
#
# À lancer depuis le LXC de la passerelle (les appels directs à l'IP y sont autorisés).
# Exemple :  ./verifier-site.sh recettes 192.168.1.60:3000 --page-privee /api/moi
set -uo pipefail

[ $# -ge 2 ] || { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
SD="$1"; CIBLE="$2"; shift 2
DOMAINE=""; CHEMINS="/api/notify /mcp"; PRIVEE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domaine) DOMAINE="$2"; shift 2 ;;
    --chemins) CHEMINS="$2"; shift 2 ;;
    --page-privee) PRIVEE="$2"; shift 2 ;;
    *) echo "Option inconnue : $1"; exit 2 ;;
  esac
done
if [ -z "$DOMAINE" ]; then
  for f in ./.env /opt/site/passerelle/.env "$(dirname "$0")/../../../../.env"; do
    [ -f "$f" ] && DOMAINE="$(grep -E '^DOMAINE=' "$f" | tail -n1 | cut -d= -f2-)" && [ -n "$DOMAINE" ] && break
  done
fi
DOMAINE="${DOMAINE:-super-nono.cc}"
URL="https://$SD.$DOMAINE"
OK=0; KO=0
bon() { printf '  \033[32m✓\033[0m %s\n' "$*"; OK=$((OK + 1)); }
mauvais() { printf '  \033[31m✗\033[0m %s\n' "$*"; KO=$((KO + 1)); }
info() { printf '  · %s\n' "$*"; }
code_de() { curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 10 "$@" 2>/dev/null || echo "000 "; }

echo "Site : $URL  →  http://$CIBLE"

echo; echo "1. Par Internet, sans être connecté"
read -r code redir <<<"$(code_de "$URL/")"
if [[ "$code" =~ ^30[0-9]$ && ( "$redir" == *"auth.$DOMAINE"* || "$redir" == *"/outpost.goauthentik.io/"* ) ]]; then
  bon "la page d'accueil renvoie vers la connexion ($code)"
elif [ "$code" = "000" ]; then
  mauvais "pas de réponse : le site est-il ajouté dans le hub et le DNS en place ?"
else
  mauvais "réponse $code sans passer par la connexion : le site n'est pas protégé (ou encore derrière l'ancien Cloudflare)"
fi

for chemin in $CHEMINS; do
  read -r code redir <<<"$(code_de "$URL$chemin")"
  if [[ "$redir" == *"auth.$DOMAINE"* || "$redir" == *"/outpost.goauthentik.io/"* ]]; then
    mauvais "$chemin renvoie vers la connexion : ajoute-le aux chemins publics du site dans le hub"
  elif [ "$code" = "000" ]; then
    mauvais "$chemin : pas de réponse"
  else
    bon "$chemin est public (réponse $code de ton app : c'est elle qui doit vérifier le jeton)"
  fi
done

echo; echo "2. En direct sur l'adresse locale"
read -r code redir <<<"$(code_de "http://$CIBLE/")"
if [ "$code" = "000" ]; then
  mauvais "http://$CIBLE ne répond pas depuis la passerelle : vérifie l'IP, le port et la règle du pare-feu Proxmox"
else
  bon "la passerelle joint le site ($code)"
fi
if [ -n "$PRIVEE" ]; then
  read -r code redir <<<"$(code_de -H "X-Authentik-Email: pirate@exemple.fr" -H "X-Authentik-Groups: authentik Admins" "http://$CIBLE$PRIVEE")"
  case "$code" in
    401|403|30?) bon "$PRIVEE refuse de faux en-têtes sans la clé ($code)" ;;
    000) mauvais "$PRIVEE ne répond pas" ;;
    *) mauvais "$PRIVEE accepte de faux en-têtes ($code) : l'app doit vérifier X-Passerelle-Cle" ;;
  esac
else
  info "ajoute --page-privee /une/page/privee pour vérifier que l'app refuse les faux en-têtes"
fi

echo; echo "3. À vérifier toi-même depuis ton PC (pas depuis la passerelle)"
info "curl -m 5 http://$CIBLE/  doit échouer (délai dépassé) si le pare-feu du site est actif"

echo; [ "$KO" -eq 0 ] && printf '\033[32mTout est bon (%s vérifications).\033[0m\n' "$OK" || printf '\033[31m%s problème(s), %s vérification(s) bonne(s).\033[0m\n' "$KO" "$OK"
[ "$KO" -eq 0 ]
