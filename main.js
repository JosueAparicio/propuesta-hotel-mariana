// ===================== Recorrido con el scroll =====================
// Cada tramo es una secuencia de fotogramas WebP pintada en un <canvas>
// (ver tools/build_frames.py): el scroll elige el fotograma, así el carro
// avanza o retrocede con el dedo. Dos capas por tramo:
//   lo = todo el tramo a media resolución, un solo paquete: llega enseguida
//   hi = paquetes de 24 fotogramas a resolución completa, pedidos empezando
//        por donde va el usuario; sustituyen a "lo" cuando llegan
(function () {
  "use strict";
  var FRAMES = "frames/";
  var TIER = window.innerHeight > window.innerWidth * 1.2 ? "p" : "d";
  document.documentElement.setAttribute("data-tier", TIER);
  var HI_CACHE = TIER === "p" ? 18 : 22, LO_CACHE = 30;
  var MAX_DECODES = 3, MAX_FETCHES = 3;
  var FOLLOW = 20;                 // qué tan pegada sigue la imagen al scroll
  var AHEAD = 120;                 // fotogramas en alta que se piden por delante
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var tramos = ["s1", "s2", "s3"].map(function (key, i) {
    var el = document.getElementById("scrub" + (i + 1));
    var canvas = el.querySelector("canvas");
    return {
      key: key, num: i + 1, el: el, canvas: canvas, ctx: canvas.getContext("2d", { alpha: false }),
      meta: null, hi: { blobs: [], cache: new Map(), pending: new Set(), max: HI_CACHE },
      lo: { blobs: [], cache: new Map(), pending: new Set(), max: LO_CACHE },
      decoding: 0, packs: new Map(), wanted: i === 0, target: 0, current: 0, dir: 1,
      drawn: "", crop: null, rect: null, pos: -1
    };
  });

  var overlays = [].slice.call(document.querySelectorAll(".ov")).map(function (el) {
    return { el: el, tramo: +el.dataset.tramo, from: +el.dataset.from, to: +el.dataset.to, last: -1, keep: el.hasAttribute("data-keep") };
  });

  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  // ---------- Decodificación ----------
  var useBitmap = "createImageBitmap" in window;
  function decodeImg(blob) {
    return new Promise(function (ok, ko) {
      var url = URL.createObjectURL(blob), img = new Image();
      img.onload = function () { URL.revokeObjectURL(url); ok(img); };
      img.onerror = function () { URL.revokeObjectURL(url); ko(); };
      img.src = url;
    });
  }
  function decode(blob) {
    if (!useBitmap) return decodeImg(blob);
    return createImageBitmap(blob).catch(function () { useBitmap = false; return decodeImg(blob); });
  }
  function nearest(has, i, n, radius) {
    if (radius === undefined) radius = n;
    for (var d = 0; d <= radius; d++) {
      if (i - d >= 0 && has(i - d)) return i - d;
      if (d && i + d < n && has(i + d)) return i + d;
    }
    return -1;
  }
  function requestDecode(t, layer, i) {
    if (i < 0 || t.decoding >= MAX_DECODES) return;
    if (layer.cache.has(i) || layer.pending.has(i) || !layer.blobs[i]) return;
    layer.pending.add(i); t.decoding++;
    decode(layer.blobs[i]).then(function (bmp) {
      layer.cache.set(i, bmp);
      while (layer.cache.size > layer.max) {           // suelta lo más lejano
        var far = -1, farD = -1;
        layer.cache.forEach(function (_, k) { var d = Math.abs(k - t.current); if (d > farD) { farD = d; far = k; } });
        var old = layer.cache.get(far); if (old && old.close) old.close();
        layer.cache.delete(far);
      }
    }).catch(function () {}).then(function () { layer.pending.delete(i); t.decoding--; });
  }

  // ---------- Pintado (recorte tipo object-fit: cover) ----------
  function layout(t) {
    if (!t.meta) return;
    var cw = t.canvas.clientWidth, ch = t.canvas.clientHeight;
    if (!cw || !ch) return;
    var w = t.meta.w, h = t.meta.h, s = Math.max(cw / w, ch / h);
    var sw = cw / s, sh = ch / s;
    t.crop = { sx: (w - sw) / 2, sy: (h - sh) / 2, sw: sw, sh: sh };
    t.canvas.width = Math.round(sw); t.canvas.height = Math.round(sh);
    t.drawn = "";
  }
  function paint(t, img, f) {
    var c = t.crop;
    t.ctx.drawImage(img, c.sx * f, c.sy * f, c.sw * f, c.sh * f, 0, 0, t.canvas.width, t.canvas.height);
    if (!t.canvas.classList.contains("ready")) t.canvas.classList.add("ready");
  }
  function render(t) {
    var n = t.meta.n, want = Math.round(t.current), hi = t.hi, lo = t.lo;
    for (var k = 0; k <= 4; k++) {
      var j = want + k * t.dir;
      if (j < 0 || j >= n) break;
      if (hi.blobs[j]) requestDecode(t, hi, j);
      else requestDecode(t, lo, nearest(function (x) { return lo.blobs[x]; }, j, n, 1));
    }
    var layer = null, idx = -1;
    if (hi.cache.has(want)) { layer = hi; idx = want; }
    if (!layer) { idx = nearest(function (x) { return hi.cache.has(x); }, want, n, 1); if (idx >= 0) layer = hi; }
    if (!layer) { idx = nearest(function (x) { return lo.cache.has(x); }, want, n, 2); if (idx >= 0) layer = lo; }
    if (!layer && !t.drawn) {
      idx = nearest(function (x) { return hi.cache.has(x); }, want, n); if (idx >= 0) layer = hi;
      else { idx = nearest(function (x) { return lo.cache.has(x); }, want, n); if (idx >= 0) layer = lo; }
    }
    if (!layer) return;
    var key = (layer === hi ? "h" : "l") + idx;
    if (key === t.drawn) return;
    paint(t, layer.cache.get(idx), layer === hi ? 1 : t.meta.lo.w / t.meta.w);
    t.drawn = key;
  }

  // ---------- Scroll suave con inercia ----------
  var lenis = null;
  if (window.Lenis && !reduce) {
    lenis = new Lenis({ lerp: 0.09, wheelMultiplier: 1 });
    window._lenis = lenis;
  }
  document.querySelectorAll('a[href^="#"]').forEach(function (a) {
    a.addEventListener("click", function (e) {
      var target = document.querySelector(a.getAttribute("href"));
      if (!target) return;
      e.preventDefault();
      if (lenis) lenis.scrollTo(target, { duration: 1.4 }); else target.scrollIntoView({ behavior: "smooth" });
    });
  });

  // ---------- Textos y capítulos según el avance ----------
  var chapters = [].slice.call(document.querySelectorAll(".chapters a"));
  var floatBtn = document.querySelector(".float");
  function updateOverlays(num, p) {
    for (var i = 0; i < overlays.length; i++) {
      var ov = overlays[i];
      if (ov.tramo !== num) continue;
      var span = ov.to - ov.from, fz = Math.min(0.05, span * 0.25), o = 0;
      if (p >= ov.from && p <= ov.to) {
        var fin = ov.from <= 0 ? 1 : clamp01((p - ov.from) / fz);
        var fout = ov.keep ? 1 : clamp01((ov.to - p) / fz);
        o = Math.min(fin, fout);
      } else if (ov.keep && p > ov.to) o = 1;
      o = Math.round(o * 1000) / 1000;
      if (o === ov.last) continue;
      ov.last = o;
      ov.el.style.opacity = o;
      ov.el.style.visibility = o > 0 ? "visible" : "hidden";
      ov.el.style.setProperty("--dy", ((1 - o) * 22).toFixed(1) + "px");
    }
  }

  var lastT = performance.now();
  function frame(now) {
    if (lenis) lenis.raf(now);
    var dt = Math.min(0.05, Math.max(0, (now - lastT) / 1000)) || 0.016;
    lastT = now;
    var alpha = reduce ? 1 : 1 - Math.exp(-dt * FOLLOW);
    var vh = window.innerHeight, activo = null;
    for (var i = 0; i < tramos.length; i++) tramos[i].rect = tramos[i].el.getBoundingClientRect();
    for (i = 0; i < tramos.length; i++) {
      var t = tramos[i], r = t.rect;
      if (!t.wanted && r.top < vh * 3) { t.wanted = true; pump(); }
      if (r.top <= vh * 0.5 && r.bottom > vh * 0.5) activo = t.el.dataset.chapter;
      if (r.bottom < -vh * 0.5 || r.top > vh * 1.5) continue;
      var total = t.el.offsetHeight - vh, p = total > 0 ? clamp01(-r.top / total) : 0;
      updateOverlays(t.num, p);
      if (!t.meta || !t.crop) continue;
      t.target = p * (t.meta.n - 1);
      var delta = t.target - t.current;
      if (Math.abs(delta) > 0.01) t.dir = delta > 0 ? 1 : -1;
      t.current = Math.abs(delta) < 0.05 ? t.target : t.current + delta * alpha;
      render(t);
      var pos = Math.floor(t.current / 24);
      if (pos !== t.pos) { t.pos = pos; pump(); }
    }
    // En las bandas entre tramos se mantiene el capítulo siguiente
    if (!activo) {
      var y = window.scrollY + vh * 0.5;
      for (i = 0; i < tramos.length; i++) if (y < tramos[i].el.offsetTop + tramos[i].el.offsetHeight) { activo = y > tramos[0].el.offsetTop + tramos[0].el.offsetHeight ? tramos[i].el.dataset.chapter : null; break; }
    }
    for (i = 0; i < chapters.length; i++) chapters[i].classList.toggle("on", chapters[i].dataset.chapter === activo);
    floatBtn.classList.toggle("show", window.scrollY > vh * 0.9);
    requestAnimationFrame(frame);
  }

  var resizeT;
  window.addEventListener("resize", function () { clearTimeout(resizeT); resizeT = setTimeout(function () { tramos.forEach(layout); }, 120); });

  // ---------- Descargas por prioridad ----------
  function fetchPack(pack, attempt) {
    attempt = attempt || 0;
    return fetch(FRAMES + TIER + "/" + pack.file).then(function (res) {
      if (!res.ok) throw new Error(res.status);
      return res.arrayBuffer();
    }).then(function (b) { return new Uint8Array(b); }).catch(function (e) {
      if (attempt >= 4) throw e;
      return new Promise(function (ok) { setTimeout(ok, 600 * (attempt + 1)); }).then(function () { return fetchPack(pack, attempt + 1); });
    });
  }
  function storePack(layer, pack, buf) {
    var off = 0;
    pack.idx.forEach(function (fi, k) {
      var len = pack.len[k];
      layer.blobs[fi] = new Blob([buf.subarray(off, off + len)], { type: "image/webp" });
      off += len;
    });
  }
  function queueFor(t, from) {
    var hp = t.meta.hi, c = Math.min(hp.length - 1, Math.max(0, Math.floor(from / 24) || 0));
    var ahead = [[t.lo, t.meta.lo], [t.hi, hp[c]]], behind = [];
    for (var k = c + 1; k < hp.length && hp[k].idx[0] < from + AHEAD; k++) ahead.push([t.hi, hp[k]]);
    for (k = c - 1; k >= 0 && hp[k].idx[hp[k].idx.length - 1] > from - AHEAD / 2; k--) behind.push([t.hi, hp[k]]);
    return { t: t, ahead: ahead, behind: behind };
  }
  function nextPack() {
    var vh = window.innerHeight;
    var live = tramos.filter(function (t) { return t.meta && t.wanted; });
    function R(t) { return t.rect || t.el.getBoundingClientRect(); }
    var active = live.filter(function (t) { var r = R(t); return r.top <= vh * 0.5 && r.bottom > vh * 0.5; });
    var below = live.filter(function (t) { return R(t).top > vh * 0.5; }).sort(function (a, b) { return R(a).top - R(b).top; });
    var above = live.filter(function (t) { return R(t).bottom <= vh * 0.5; });
    var plan = active.map(function (t) { return queueFor(t, Math.round(t.current)); })
      .concat(below.map(function (t) { return queueFor(t, 0); }))
      .concat(above.map(function (t) { return queueFor(t, t.meta.n - 1); }));
    var order = [];
    plan.forEach(function (q) { q.ahead.forEach(function (x) { order.push([q.t, x[0], x[1]]); }); });
    plan.forEach(function (q) { q.behind.forEach(function (x) { order.push([q.t, x[0], x[1]]); }); });
    for (var i = 0; i < order.length; i++) if (!order[i][0].packs.has(order[i][2])) return order[i];
    return null;
  }
  var fetching = 0;
  function pump() {
    var s1 = tramos[0];
    var slots = s1.meta && s1.packs.get(s1.meta.lo) === "done" ? MAX_FETCHES : 1;
    while (fetching < slots) {
      var next = nextPack();
      if (!next) return;
      (function (t, layer, pack) {
        t.packs.set(pack, "loading"); fetching++;
        fetchPack(pack).then(function (buf) { storePack(layer, pack, buf); t.packs.set(pack, "done"); })
          .catch(function () { setTimeout(function () { t.packs.delete(pack); pump(); }, 4000); })
          .then(function () { fetching--; pump(); });
      })(next[0], next[1], next[2]);
    }
  }

  // ---------- Arranque ----------
  (function loadManifest(attempt) {
    fetch(FRAMES + "manifest.json").then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (m) {
        tramos.forEach(function (t) {
          t.meta = m[TIER][t.key];
          t.hi.blobs = new Array(t.meta.n); t.lo.blobs = new Array(t.meta.n);
          layout(t);
        });
        pump();
      })
      .catch(function () { setTimeout(function () { loadManifest(attempt + 1); }, Math.min(5000, 500 * (attempt + 1))); });
  })(0);
  requestAnimationFrame(frame);
})();

// ===================== Contacto =====================
(function () {
  // ====== CONFIG: datos del hotel ======
  var WHATSAPP = "50370000000";   // [NÚMERO DEL HOTEL] con código de país, sin + ni espacios
  var MSG = "Hola, quiero reservar en el Hotel y Restaurante Mariana. ¿Tienen disponibilidad para estas fechas?";
  // =====================================
  document.querySelectorAll("a.wa").forEach(function (a) { a.href = "https://wa.me/" + WHATSAPP + "?text=" + encodeURIComponent(MSG); });
})();
