# Prompt : mettre un site existant à niveau

Clique sur l'icône « copier » en haut à droite du bloc, puis colle le tout dans Claude Code, dans le dépôt du site à mettre à jour. Remplace d'abord la ligne entre crochets.

```text
Mets ce site à niveau avec le kit de base de Noë : nouveau design « Encre & Verre » et passerelle Authentik de super-nono.cc (comptes, groupes, MCP par OAuth). Sous-domaine : [nom].super-nono.cc

RÉFÉRENCES, dans le dépôt GitHub SuperNon0/site, branche main (ou claude/passerelle-brancher-projets si passerelle/docs/mcp-oauth.md n'est pas encore sur main). Si tu n'as pas accès à ce dépôt, arrête-toi et dis-le-moi : je te donnerai les fichiers.
- passerelle/docs/brancher-un-projet.md et passerelle/docs/migrer-un-site.md : à lire en entier avant de commencer.
- passerelle/docs/mcp-oauth.md : à lire en entier si le site a un MCP.
- passerelle/hub/hub/static/kit/ : passerelle_auth.py, passerelle-auth.ts, exemple_comptes.py, CLAUDE-passerelle.md, verifier-site.sh.
- passerelle/hub/hub/static/encre/ : le kit de design complet à jour, version 1.1 (LISEZMOI.md, demo.html, modele.html, polices.css, polices/, encre.css, verre.css, encre.js, icones.svg, tailwind/). L'ancien kit 1.0 du dépôt Biblioth-que-de-filme n'a pas le style Verre : ne l'utilise plus.
- passerelle/hub/hub/templates/ (_head.html, _base.html, reglages.html) et passerelle/hub/hub/static/hub.css : un site qui applique déjà tout ça, à prendre comme modèle.

ÉTAPE 0 : FAIS L'ÉTAT DES LIEUX AVANT DE TOUCHER À QUOI QUE CE SOIT
Liste-moi dans la pull request : comment le site connecte les gens aujourd'hui (Cloudflare Access, JWT, mot de passe, page de login…), sa table de comptes s'il en a une, ses chemins appelés par des machines ou ouverts à tous, son MCP s'il en a un (adresse, clé, en-tête), et la version du design en place (ancien thème, encre sans verre.css…).

DESIGN (seulement l'apparence : aucune fonctionnalité ni donnée ne change)
1. Remplace (ou crée) le dossier design-encre/ par le contenu de passerelle/hub/hub/static/encre/, sans le modifier. Si un ancien design-encre/ existe, compare et garde seulement ce que le site a ajouté dans sa propre feuille. Lis design-encre/LISEZMOI.md en entier (surtout les sections 2, 6 bis, 7, 10 et 11), puis regarde demo.html et modele.html. Pour un site Next.js + Tailwind, suis la partie « Next.js + Tailwind » de la section 10.
2. Ce qui a changé depuis le kit 1.0 (section 6 bis du LISEZMOI) : verre.css (style « Verre », actif avec data-style="verre" sur <html>) à charger après encre.css ; le script du <head> qui pose style, accent et thème avant l'affichage et pose toujours data-theme, même en automatique (copie celui de _head.html, clé localStorage « encre-apparence ») ; data-accent-defaut et data-style-defaut sur <html> ; dans Réglages → Apparence, le choix du style Encre ou Verre en plus de l'accent et du thème.
3. Ordre dans le <head> : script d'apparence, polices.css, encre.css, verre.css, la feuille propre au site, encre.js en defer.
4. Applique le design à toutes les pages comme _base.html : menu de gauche sur ordinateur, barre d'onglets flottante (.tabbar) sur téléphone, feuille « Plus », recherche rapide. Coche la liste de la section 11 du LISEZMOI. Toutes les couleurs par les jetons de :root (n'en invente aucune), variantes :root[data-style="verre"] dans la feuille du site quand il faut, jamais d'alert(), confirm() ou prompt() du navigateur.

CONNEXION, COMPTES, ACCÈS
5. Retire la connexion propre au site : vérification du JWT Cloudflare, page de login, mot de passe, Cloudflare Access. Copie passerelle_auth.py (ou passerelle-auth.ts) tel quel et lis l'identité seulement avec utilisateur() / lireUtilisateur().
6. Comptes : garde la table existante et ses données. Retrouve le compte par e-mail (en minuscules) à chaque visite et crée-le s'il manque, comme exemple_comptes.py. Ne supprime aucun compte ni aucune donnée. Si des comptes n'ont pas d'e-mail, liste-les et demande-moi.
7. Rôles : garde ceux du site, ou tire-les des groupes Authentik si c'est plus simple ; dis-moi lequel tu as choisi. Qui peut ENTRER sur le site se règle dans le hub (« Réservé à certains groupes »).
8. Chemins appelés par des machines ou ouverts à tous (API, webhooks, QR code, WebSocket…) : garde-les, protège-les par jeton SERVICE_JETONS, signature, ou rien pour les pages vraiment publiques (aucune lecture d'identité, aucune action d'admin dessus). Liste-les-moi : je les déclarerai dans le hub.
9. Notifications : par BotPanel (POST {BOTPANEL_URL}/api/notify), jamais Discord en direct.
10. MCP : remplace l'ancienne vérification (clé dans l'URL, en-tête, JWT) par le guichet OAuth de mcp-oauth.md (brancher_mcp + @mcp_requis, ou guichetMcp + accesMcp), serveur sur /api/mcp, bouton « Déconnecter Claude » dans l'admin. Dis-moi la nouvelle adresse du connecteur à remettre dans Claude.
11. .env.example : ajoute PASSERELLE_CLE, PASSERELLE_URL_SITE=https://[nom].super-nono.cc, SERVICE_JETONS et, avec un MCP, PASSERELLE_MCP_SECRET, PASSERELLE_MCP_POUR, PASSERELLE_MCP_FICHIER ; retire les variables Cloudflare devenues inutiles.
12. Mets le CLAUDE.md du projet à jour avec CLAUDE-passerelle.md, plus une partie « Design » qui reprend les règles 1 à 4.

VÉRIFIER ET LIVRER
13. Teste : une personne, une machine avec jeton, de faux en-têtes X-Authentik-* sans la clé (refusés), les pages publiques sans connexion, et le MCP de bout en bout s'il y en a un.
14. Vérifie le rendu sur téléphone (390 px) et sur ordinateur, en clair et en sombre, en style Encre et en style Verre.
15. Travaille sur une branche et ouvre une pull request avec des captures avant/après. Dans la description, donne-moi :
    - ce que je dois régler dans le hub : accès (groupes), chemins ouverts sans connexion ;
    - les règles de pare-feu Proxmox (la passerelle et les machines qui appellent le site en direct) ;
    - la commande verifier-site.sh à lancer sur la passerelle ;
    - ce que je dois retirer chez Cloudflare (application Access du site).
16. Attends mon accord avant de fusionner.
```
