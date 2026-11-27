/* hub.js : les comportements du hub, au-dessus de encre.js (objet global Encre). */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const sansAccents = (t) => (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

  /* Appel à l'API du hub. L'en-tête X-Hub prouve que l'appel vient du hub. */
  async function api(methode, url, corps) {
    const r = await fetch(url, {
      method: methode,
      headers: { "Content-Type": "application/json", "X-Hub": "1", Accept: "application/json" },
      body: corps === undefined ? undefined : JSON.stringify(corps),
      credentials: "same-origin",
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* page HTML (session expirée…) */ }
    if (!r.ok || data.ok === false) {
      const err = new Error(data.message || (r.status === 401 || r.status === 0
        ? "Ta session a expiré : recharge la page." : `Le hub a répondu ${r.status}.`));
      err.data = data;
      throw err;
    }
    return data;
  }

  /* Message à afficher après un rechargement de page. */
  const apres = (message) => { try { sessionStorage.setItem("hub-toast", message); } catch (e) {} };
  function toastEnAttente() {
    try {
      const m = sessionStorage.getItem("hub-toast");
      if (m) { sessionStorage.removeItem("hub-toast"); Encre.toast(m); }
    } catch (e) {}
  }

  /* ------------------------------------------------------------ recherche rapide */
  function recherche() {
    let tout = [];
    try { tout = JSON.parse($("#hub-raccourcis")?.textContent || "[]"); } catch (e) {}
    Encre.recherche.raccourcis = tout.filter((r) => !r.groupe);
    const sites = tout.filter((r) => r.groupe === "Sites");
    Encre.recherche.chercher = async (q) => {
      const nq = sansAccents(q);
      return sites.filter((s) => sansAccents(`${s.titre} ${s.sous}`).includes(nq)).slice(0, 8)
        .map((s) => ({ ...s, iconeGroupe: "grid" }));
    };
  }

  /* ------------------------------------------------------------ filtre simple */
  function filtre(champ, elements, cle, vide, groupes) {
    if (!champ) return;
    champ.addEventListener("input", () => {
      const q = sansAccents(champ.value.trim());
      let n = 0;
      elements.forEach((el) => {
        const ok = !q || sansAccents(el.dataset[cle]).includes(q);
        el.hidden = !ok;
        if (ok) n++;
      });
      (groupes || []).forEach((g) => { g.hidden = !$$("[data-" + cle + "]", g).some((el) => !el.hidden); });
      if (vide) vide.hidden = n > 0;
    });
  }

  /* ------------------------------------------------------------ réglages */
  function reglages() {
    const reg = $("#reglages");
    if (!reg) return;
    const panneaux = $$(".reg-panneau", reg).map((s) => s.dataset.panneau);
    const montrer = () => {
      const sous = decodeURIComponent(location.hash.slice(1));
      const choisi = panneaux.includes(sous) ? sous : null;
      reg.classList.toggle("detail", !!choisi);
      const montre = choisi || panneaux[0];
      $$(".reg-panneau", reg).forEach((s) => { s.hidden = s.dataset.panneau !== montre; });
      $$("[data-reg]", reg).forEach((a) => a.classList.toggle("on", a.dataset.reg === montre));
      if (choisi) scrollTo({ top: 0 });
    };
    addEventListener("hashchange", montrer);
    montrer();
    $(".reg-retour", reg)?.addEventListener("click", (e) => { e.preventDefault(); history.replaceState(null, "", location.pathname); montrer(); });

    // Installation sur l'écran d'accueil (Android / Chrome).
    let invite = null;
    const bouton = $("#installer-pwa");
    addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); invite = e; if (bouton) bouton.hidden = false; });
    bouton?.addEventListener("click", async () => { if (!invite) return; invite.prompt(); await invite.userChoice; invite = null; bouton.hidden = true; });
  }

  /* ------------------------------------------------------------ formulaire d'un site */
  function formulaireSite() {
    const form = $("#form-site");
    if (!form) return;
    const id = form.dataset.siteId;
    const type = form.elements.type;

    const majType = () => {
      $$(".hub-type [data-type]", form).forEach((b) => {
        const on = b.dataset.type === type.value;
        b.classList.toggle("on", on);
        b.setAttribute("aria-checked", String(on));
      });
      $$("[data-pour]", form).forEach((el) => { el.hidden = el.dataset.pour !== type.value; });
    };
    $$(".hub-type [data-type]", form).forEach((b) => b.addEventListener("click", () => { type.value = b.dataset.type; majType(); }));
    majType();

    $$(".hub-icone", form).forEach((b) => b.addEventListener("click", () => {
      $$(".hub-icone", form).forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", String(x === b)); });
      form.elements.icone.value = b.dataset.icone;
    }));

    // Proposer un sous-domaine à partir du nom (tant qu'on ne l'a pas touché).
    const sd = form.elements.sous_domaine;
    let sdTouche = !!sd.value;
    sd.addEventListener("input", () => { sdTouche = true; });
    form.elements.nom.addEventListener("input", () => {
      if (sdTouche || id) return;
      sd.value = sansAccents(form.elements.nom.value).replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
    });

    const statut = $("#statut-cible");
    $("#tester-cible")?.addEventListener("click", (e) => {
      Encre.occupe(e.currentTarget, async () => {
        statut.hidden = false;
        statut.className = "statut";
        statut.textContent = "Je teste…";
        try {
          const r = await api("POST", "/api/sites/tester", { cible: form.elements.cible.value });
          statut.className = "statut ok";
          statut.textContent = r.message;
        } catch (err) {
          statut.className = "statut err";
          statut.textContent = err.message;
        }
      });
    });

    const montrerErreurs = (erreurs = {}) => {
      $$(".hub-champ[data-champ]", form).forEach((c) => {
        const m = erreurs[c.dataset.champ];
        c.classList.toggle("erreur", !!m);
        const p = $(".hub-err", c);
        if (p) { p.hidden = !m; p.textContent = m || ""; }
      });
      const premier = Object.keys(erreurs)[0];
      if (premier) form.elements[premier]?.focus();
    };

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const bouton = $("button[type=submit]", form);
      const f = form.elements;
      const corps = {
        type: f.type.value, nom: f.nom.value, description: f.description.value,
        sous_domaine: f.sous_domaine.value, cible: f.cible.value, lien: f.lien.value,
        icone: f.icone.value, categorie: f.categorie.value, ordre: f.ordre.value,
        acces: f.acces_admins.checked ? "admins" : "membres", groupes: f.groupes.value, chemins_publics: f.chemins_publics.value,
      };
      Encre.occupe(bouton, async () => {
        try {
          const r = await api(id ? "PUT" : "POST", id ? `/api/sites/${id}` : "/api/sites", corps);
          apres(r.message);
          location.href = "/admin/sites";
        } catch (err) {
          montrerErreurs(err.data?.erreurs);
          Encre.toastErr(err);
        }
      });
    });

    $("#supprimer-site")?.addEventListener("click", (e) => supprimerSite(id, e.currentTarget.dataset.nom, true));
  }

  async function supprimerSite(id, nom, versListe) {
    const ok = await Encre.confirmer(`« ${nom} » disparaît de la page d'accueil et d'Authentik. Le site lui-même (son LXC) n'est pas touché.`,
      { ok: "Supprimer", danger: true, titre: "Supprimer ce site ?" });
    if (!ok) return;
    try {
      const r = await api("DELETE", `/api/sites/${id}`);
      if (versListe) { apres(r.message); location.href = "/admin/sites"; return; }
      apres(r.message);
      location.reload();
    } catch (err) { Encre.toastErr(err); }
  }

  /* ------------------------------------------------------------ liste des sites */
  function listeSites() {
    $$("[data-menu-site]").forEach((b) => b.addEventListener("click", () => {
      const li = b.closest("[data-site]");
      const id = li.dataset.site, nom = li.dataset.nom;
      const lien = $(".hub-li-lien", li)?.getAttribute("href");
      Encre.menu(b, [
        { icone: "edit", texte: "Modifier", action: () => { location.href = lien; } },
        { icone: "refresh", texte: "Remettre dans Authentik", action: () => reparer(id) },
        null,
        { icone: "trash", texte: "Supprimer", danger: true, action: () => supprimerSite(id, nom, false) },
      ], { titre: nom });
    }));
    $$("[data-reparer]").forEach((b) => b.addEventListener("click", () => reparer(b.dataset.reparer, b)));
  }

  async function reparer(id, bouton) {
    const faire = async () => {
      try { const r = await api("POST", `/api/sites/${id}/reparer`); apres(r.message); location.reload(); }
      catch (err) { Encre.toastErr(err); }
    };
    if (bouton) Encre.occupe(bouton, faire); else await faire();
  }

  /* ------------------------------------------------------------ demandes */
  function demandes() {
    const liste = $("#liste-demandes");
    if (!liste) return;
    const vide = $("#demandes-vide");
    const puces = $$("[data-filtre]");
    let courant = "en_attente";
    const appliquer = () => {
      let n = 0;
      $$(".hub-demande", liste).forEach((li) => { const ok = li.dataset.statut === courant; li.hidden = !ok; if (ok) n++; });
      liste.hidden = n === 0;
      vide.hidden = n > 0;
      puces.forEach((p) => p.classList.toggle("on", p.dataset.filtre === courant));
    };
    puces.forEach((p) => p.addEventListener("click", () => { courant = p.dataset.filtre; appliquer(); }));
    appliquer();

    const agir = async (li, action, bouton) => {
      const id = li.dataset.id, nom = li.dataset.nom;
      if (action === "refuser" && !(await Encre.confirmer(`${nom} verra « Accès refusé » et ne pourra pas redemander.`,
        { ok: "Refuser", danger: true, titre: "Refuser la demande ?" }))) return;
      if (action === "oublier" && !(await Encre.confirmer(`La demande de ${nom} est effacée : la personne pourra en refaire une.`,
        { ok: "Effacer", danger: true, titre: "Effacer la demande ?" }))) return;
      const faire = async () => {
        try {
          const r = await api("POST", `/api/demandes/${id}/${action}`);
          if (r.lien && /pas parti/.test(r.message)) {
            const f = Encre.feuille({ titre: "Invitation à envoyer toi-même", html:
              `<p class="access-text"></p><p class="petit" style="margin:10px 0">Envoie-lui ce lien (valable une fois) :</p>
               <div class="copie"><code></code><button type="button" class="btn sm" data-c>${Encre.ic("copy", "s16")}Copier</button></div>`,
              surFermer: () => location.reload() });
            f.corps.querySelector(".access-text").textContent = r.message.split(" Envoie-lui")[0];
            f.corps.querySelector(".copie code").textContent = r.lien;
            f.corps.querySelector("[data-c]").addEventListener("click", (ev) => Encre.copier(r.lien, ev.currentTarget));
            return;
          }
          apres(r.message);
          location.reload();
        } catch (err) { Encre.toastErr(err); }
      };
      if (bouton) Encre.occupe(bouton, faire); else await faire();
    };

    $$("[data-action]", liste).forEach((b) => b.addEventListener("click", () => agir(b.closest(".hub-demande"), b.dataset.action, b)));
    $$("[data-menu-demande]", liste).forEach((b) => b.addEventListener("click", () => {
      const li = b.closest(".hub-demande");
      const st = li.dataset.statut;
      const items = [];
      if (st === "acceptee") items.push({ icone: "mail", texte: "Renvoyer l'invitation", action: () => agir(li, "renvoyer") });
      if (st === "acceptee") items.push({ icone: "x", texte: "Annuler et refuser", danger: true, action: () => agir(li, "refuser") });
      if (st === "refusee") items.push({ icone: "check", texte: "Accepter quand même", action: () => agir(li, "accepter") });
      if (items.length) items.push(null);
      items.push({ icone: "trash", texte: "Effacer la demande", danger: true, action: () => agir(li, "oublier") });
      Encre.menu(b, items, { titre: li.dataset.nom });
    }));
  }

  /* ------------------------------------------------------------ membres */
  function membres() {
    filtre($("#filtre-membres"), $$(".hub-membre"), "cherche");
    $$("[data-menu-membre]").forEach((b) => b.addEventListener("click", () => {
      const li = b.closest(".hub-membre");
      const actif = b.dataset.actif === "oui";
      const nom = li.dataset.nom;
      const changer = async (valeur) => {
        if (!valeur && !(await Encre.confirmer(`${nom} ne pourra plus entrer sur aucun site, tout de suite. Tu pourras le débloquer plus tard.`,
          { ok: "Bloquer", danger: true, titre: "Bloquer ce membre ?" }))) return;
        try { const r = await api("POST", `/api/membres/${li.dataset.pk}/actif`, { actif: valeur }); apres(r.message); location.reload(); }
        catch (err) { Encre.toastErr(err); }
      };
      Encre.menu(b, [actif
        ? { icone: "lock", texte: "Bloquer", danger: true, action: () => changer(false) }
        : { icone: "check", texte: "Débloquer", action: () => changer(true) }], { titre: nom });
    }));
  }

  /* ------------------------------------------------------------ brancher un projet */
  function brancher() {
    const voir = $("#cle-voir");
    if (voir) {
      const texte = $("#cle-texte");
      voir.addEventListener("click", () => {
        const cache = texte.textContent === texte.dataset.cache;
        texte.textContent = cache ? voir.dataset.cle : texte.dataset.cache;
        voir.lastChild.textContent = cache ? "Cacher" : "Voir";
      });
    }
    const creer = $("#jeton-creer");
    if (!creer) return;
    const nom = $("#jeton-nom"), res = $("#jeton-res");
    const generer = () => {
      const n = (nom.value || "").toLowerCase().normalize("NFD").replace(/[^a-z0-9_-]+/g, "").slice(0, 30);
      if (!n) { Encre.toastErr("Écris d'abord qui va appeler (par exemple homeassistant)."); nom.focus(); return; }
      const octets = crypto.getRandomValues(new Uint8Array(32));
      const jeton = btoa(String.fromCharCode(...octets)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
      $("#jeton-env").textContent = `SERVICE_JETONS=${n}:${jeton}`;
      $("#jeton-entete").textContent = `Authorization: Bearer ${jeton}`;
      res.hidden = false;
    };
    creer.addEventListener("click", generer);
    nom.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); generer(); } });
    $("#jeton-copier-env").addEventListener("click", (e) => Encre.copier($("#jeton-env").textContent, e.currentTarget));
    $("#jeton-copier-entete").addEventListener("click", (e) => Encre.copier($("#jeton-entete").textContent, e.currentTarget));
  }

  /* ------------------------------------------------------------ démarrage */
  document.addEventListener("DOMContentLoaded", () => {
    if (!window.Encre) return;
    recherche();
    toastEnAttente();
    filtre($("#filtre-sites"), $$(".hub-site"), "nom", $("#filtre-vide"), $$("[data-groupe]"));
    reglages();
    formulaireSite();
    listeSites();
    demandes();
    membres();
    brancher();
    $$("[data-copier]").forEach((b) => b.addEventListener("click", () => Encre.copier(b.dataset.copier, b)));
    $$(".anim").forEach((el) => Encre.animerEntree(el));
    if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
})();
