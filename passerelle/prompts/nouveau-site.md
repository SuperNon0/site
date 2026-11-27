# Prompt : créer un nouveau site

Clique sur l'icône « copier » en haut à droite du bloc, puis colle le tout dans Claude Code, dans le dépôt du nouveau projet. Remplace d'abord les trois lignes entre crochets.

```text
Crée ce nouveau site avec le kit de base de Noë : design « Encre & Verre » et passerelle Authentik de super-nono.cc.

Le site : [ce que fait le site, en deux ou trois phrases]
Sous-domaine : [nom].super-nono.cc
Langage : [Python/Flask, ou TypeScript/Next.js, ou laisse-moi choisir]

RÉFÉRENCES, dans le dépôt GitHub SuperNon0/site, branche main (ou claude/passerelle-brancher-projets si passerelle/docs/mcp-oauth.md n'est pas encore sur main). Si tu n'as pas accès à ce dépôt, arrête-toi et dis-le-moi : je te donnerai les fichiers.
- passerelle/docs/brancher-un-projet.md : à lire en entier avant de commencer (personnes, adresse locale, API, comptes, groupes, pages publiques).
- passerelle/docs/mcp-oauth.md : à lire en entier si le site a un MCP.
- passerelle/hub/hub/static/kit/ : passerelle_auth.py, passerelle-auth.ts, exemple_comptes.py, CLAUDE-passerelle.md, verifier-site.sh.
- passerelle/hub/hub/static/encre/ : le kit de design complet, version 1.1 (LISEZMOI.md, demo.html, modele.html, polices.css, polices/, encre.css, verre.css, encre.js, icones.svg, tailwind/).
- passerelle/hub/hub/templates/ (_head.html, _base.html, reglages.html) et passerelle/hub/hub/static/hub.css : un site qui applique déjà tout ça, à prendre comme modèle.

DESIGN
1. Copie passerelle/hub/hub/static/encre/ dans un dossier design-encre/ du projet, sans le modifier. Lis design-encre/LISEZMOI.md en entier (surtout les sections 2, 6 bis, 7, 10 et 11), puis regarde demo.html et modele.html. Pour un site Next.js + Tailwind, suis la partie « Next.js + Tailwind » de la section 10 avec tailwind/encre-tailwind.css.
2. Dans le <head> de chaque page, dans cet ordre : le petit script qui pose style, accent et thème avant l'affichage (copie celui de _head.html, clé localStorage « encre-apparence »), puis polices.css, encre.css, verre.css, la feuille propre au site, et encre.js en defer. Sur <html> : lang="fr", data-accent-defaut="lavande", data-style-defaut="encre".
3. Structure comme _base.html : menu de gauche sur ordinateur, barre d'onglets flottante (.tabbar) sur téléphone, feuille « Plus », recherche rapide (Ctrl K).
4. Une page Réglages → Apparence comme reglages.html : style Encre ou Verre, cinq couleurs d'accent, thème Automatique, Clair ou Sombre.
5. Coche la liste de la section 11 du LISEZMOI. Toutes les couleurs passent par les jetons de :root du kit : n'en invente aucune. Le CSS propre au site va dans sa propre feuille, avec les variantes :root[data-style="verre"] quand il faut. Jamais d'alert(), confirm() ou prompt() du navigateur : utilise les feuilles et confirmations d'encre.js.

CONNEXION ET COMPTES
6. Pas de page de connexion, pas de mot de passe, pas de Cloudflare Access : Authentik connecte les personnes avant le site. N'utilise pas l'ancien « site de base » (JWT Cloudflare, panel/).
7. Copie passerelle_auth.py (ou passerelle-auth.ts) tel quel, sans le réécrire. L'identité se lit seulement avec utilisateur() / lireUtilisateur(), jamais en lisant X-Authentik-* à la main.
8. Comptes : un compte local par e-mail, créé à la première visite, comme exemple_comptes.py (rôles depuis les groupes Authentik, compte suspendu). Avant de coder des données qui appartiennent à des personnes, demande-moi : bibliothèque partagée entre tous, ou séparée par compte ?
9. Pages : @connexion_requise, @admin_requis ou @role_requis. Qui peut ENTRER sur le site se règle dans le hub (« Réservé à certains groupes »), pas dans le code.
10. Pages ouvertes à tous (lien partagé, QR code, WebSocket…) : chemins publics, aucune lecture d'identité Authentik dessus, actions limitées, jamais d'action d'admin.
11. Machines (autre site sur l'adresse locale, Home Assistant, script) : jeton SERVICE_JETONS en Authorization: Bearer (@machine_requise ou @personne_ou_machine). Webhooks : signature_valide().
12. Notifications : par BotPanel (POST {BOTPANEL_URL}/api/notify sur son adresse locale), jamais Discord en direct.
13. MCP pour Claude, si le site en a un : suis mcp-oauth.md à la lettre (brancher_mcp + @mcp_requis, ou guichetMcp + accesMcp), serveur sur /api/mcp, bouton « Déconnecter Claude » dans l'admin.

PROJET
14. L'app écoute sur 0.0.0.0:<port> (LXC Proxmox). .env.example avec : PASSERELLE_CLE, PASSERELLE_URL_SITE=https://[nom].super-nono.cc, SERVICE_JETONS, BOTPANEL_URL et, avec un MCP, PASSERELLE_MCP_SECRET, PASSERELLE_MCP_POUR, PASSERELLE_MCP_FICHIER. Les données dans un dossier data/ sauvegardé.
15. Dev local : PASSERELLE_CLE vide + PASSERELLE_DEV_EMAIL (+ PASSERELLE_DEV_ADMIN=1).
16. Crée le CLAUDE.md du projet avec le contenu de CLAUDE-passerelle.md, plus une partie « Design » qui reprend les règles 1 à 5.

VÉRIFIER ET LIVRER
17. Teste : une personne, une machine avec jeton, de faux en-têtes X-Authentik-* sans la clé (refusés), et le MCP de bout en bout s'il y en a un.
18. Vérifie le rendu sur téléphone (390 px) et sur ordinateur, en clair et en sombre, en style Encre et en style Verre.
19. Travaille sur une branche et ouvre une pull request avec des captures. Dans la description, donne-moi :
    - ce que je dois mettre dans le hub : sous-domaine, IP:port, accès (groupes), chemins ouverts sans connexion ;
    - les règles de pare-feu Proxmox (la passerelle et les machines qui appellent le site en direct) ;
    - la commande verifier-site.sh à lancer sur la passerelle.
20. Attends mon accord avant de fusionner.
```
