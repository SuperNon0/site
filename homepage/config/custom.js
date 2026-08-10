// custom.js — garde la navigation DANS la PWA (plein écran) sur iOS,
// au lieu d'ouvrir le mini-navigateur avec la croix.
// Reproduit l'astuce classique des web-apps iOS en mode "standalone".

(function () {
  // Uniquement quand l'app tourne en plein écran depuis l'écran d'accueil iOS
  var isStandalone =
    ("standalone" in window.navigator && window.navigator.standalone) ||
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);

  if (!isStandalone) return;

  document.addEventListener(
    "click",
    function (e) {
      var a = e.target && e.target.closest ? e.target.closest("a") : null;
      if (!a) return;

      var href = a.getAttribute("href");
      if (!href || href.charAt(0) === "#") return;      // ancre interne : on ignore
      if (a.target === "_blank") return;                 // volontairement nouvel onglet
      if (/^(mailto:|tel:|javascript:)/i.test(href)) return;

      // Force la navigation dans la vue courante (reste dans la PWA)
      e.preventDefault();
      window.location.href = a.href;
    },
    false
  );
})();
