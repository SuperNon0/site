/* Fonds animés du hub (canvas léger, palette RecipeLog).
   Exposé via window.HubBG.set(mode). Modes animés : aurore, particules,
   constellation. Autres modes (sobre, dore, grille) → canvas effacé, le CSS gère.
   Respecte prefers-reduced-motion (rend une image fixe au lieu d'animer). */
(function () {
  "use strict";
  var ANIM = { aurore: 1, particules: 1, constellation: 1 };
  var COLORS = { gold: "232,197,71", mint: "79,195,161", violet: "167,139,250" };
  var canvas, ctx, raf = 0, mode = null, W = 0, H = 0, DPR = 1;
  var parts = [], blobs = [], running = false;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function ensure() {
    if (canvas) return;
    canvas = document.createElement("canvas");
    canvas.id = "hub-bg-canvas";
    var s = canvas.style;
    s.position = "fixed"; s.left = "0"; s.top = "0"; s.width = "100%"; s.height = "100%";
    s.zIndex = "-1"; s.pointerEvents = "none";
    document.body.insertBefore(canvas, document.body.firstChild);
    ctx = canvas.getContext("2d");
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) pause();
      else if (mode && ANIM[mode] && !reduce) start();
    });
    resize();
  }

  function resize() {
    if (!canvas) return;
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = Math.max(1, W * DPR); canvas.height = Math.max(1, H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    init();
    if (reduce) staticFrame();
  }

  function mkPart() {
    return {
      x: Math.random() * W, y: Math.random() * H,
      vx: (Math.random() - 0.5) * 0.25, vy: -(0.12 + Math.random() * 0.28),
      r: 0.6 + Math.random() * 1.8,
      c: Math.random() < 0.7 ? COLORS.gold : COLORS.mint,
      a: 0.22 + Math.random() * 0.42
    };
  }

  function init() {
    if (!W || !H) return;
    if (mode === "particules" || mode === "constellation") {
      var n = Math.min(mode === "constellation" ? 95 : 80, Math.round(W * H / 15000));
      parts = []; for (var i = 0; i < n; i++) parts.push(mkPart());
    } else if (mode === "aurore") {
      blobs = [
        { c: COLORS.gold, x: 0.22, y: 0.20, r: 0.6, sx: 0.6, sy: 0.4, ph: Math.random() * 6 },
        { c: COLORS.mint, x: 0.80, y: 0.28, r: 0.52, sx: 0.5, sy: 0.65, ph: Math.random() * 6 },
        { c: COLORS.violet, x: 0.52, y: 0.86, r: 0.62, sx: 0.7, sy: 0.5, ph: Math.random() * 6 }
      ];
    }
  }

  function drawParticles() {
    var i, j, p;
    for (i = 0; i < parts.length; i++) {
      p = parts[i]; p.x += p.vx; p.y += p.vy;
      if (p.y < -6) { p.y = H + 6; p.x = Math.random() * W; }
      if (p.x < -6) p.x = W + 6; else if (p.x > W + 6) p.x = -6;
    }
    if (mode === "constellation") {
      ctx.lineWidth = 1;
      for (i = 0; i < parts.length; i++) {
        for (j = i + 1; j < parts.length; j++) {
          var a = parts[i], b = parts[j], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
          if (d2 < 14400) {
            var al = (1 - Math.sqrt(d2) / 120) * 0.16;
            ctx.strokeStyle = "rgba(" + COLORS.gold + "," + al + ")";
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          }
        }
      }
    }
    for (i = 0; i < parts.length; i++) {
      p = parts[i];
      ctx.fillStyle = "rgba(" + p.c + "," + p.a + ")";
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 7); ctx.fill();
    }
  }

  function drawAurore(t) {
    var ts = t * 0.00006;
    ctx.globalCompositeOperation = "lighter";
    for (var i = 0; i < blobs.length; i++) {
      var b = blobs[i];
      var cx = (b.x + Math.sin(ts * b.sx + b.ph) * 0.12) * W;
      var cy = (b.y + Math.cos(ts * b.sy + b.ph) * 0.12) * H;
      var rad = b.r * Math.max(W, H) * 0.6;
      var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, rad);
      g.addColorStop(0, "rgba(" + b.c + ",0.16)");
      g.addColorStop(1, "rgba(" + b.c + ",0)");
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, rad, 0, 7); ctx.fill();
    }
    ctx.globalCompositeOperation = "source-over";
  }

  function frame(t) {
    ctx.clearRect(0, 0, W, H);
    if (mode === "aurore") drawAurore(t); else drawParticles();
    raf = requestAnimationFrame(frame);
  }

  function staticFrame() {
    if (!ctx) return;
    ctx.clearRect(0, 0, W, H);
    if (mode === "aurore") drawAurore(1000);
    else if (mode === "particules" || mode === "constellation") drawParticles();
  }

  function start() { if (running || reduce) return; running = true; raf = requestAnimationFrame(frame); }
  function pause() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }

  window.HubBG = {
    set: function (m) {
      ensure();
      mode = m; pause(); init();
      if (ANIM[m]) { if (reduce) staticFrame(); else start(); }
      else if (ctx) ctx.clearRect(0, 0, W, H);
    }
  };
})();
