"use strict";
/* Night-time diorama of Jeju on a plain 2D canvas (no WebGL, no external library).
 *
 * The island is drawn as a raised slab with cliff walls, a stylised Hallasan with a crater
 * lake, and decorative trees, houses and roads. None of the scenery is data: terrain is a
 * shield-volcano curve, trees are random, houses sit near stops, and roads join nearby stops.
 *
 * The night glow painted on the land is real: NASA Black Marble 2016 annual night lights
 * (VIIRS, ~500 m), via NASA GIBS, CC0. It shows where Jeju is actually lit at night.
 *
 * What IS data: stops, CCTV and emergency bells sit at their real coordinates, and the light
 * that cuts through the darkness is exactly each camera's or bell's coverage radius
 * (100 m / 200 m by default). Everything outside those circles stays dark.
 */
function NightMap3D(canvas, opts) {
  const self = this;
  const ctx = canvas.getContext("2d");
  const outline = opts.outline || [];
  const STILL = !!opts.still;
  const LAT0 = 33.38, LON0 = 126.55, MX = 111320 * Math.cos((LAT0 * Math.PI) / 180), MY = 110540;
  const toXY = (lat, lon) => [(lon - LON0) * MX, (lat - LAT0) * MY];
  const ZEX = 5.6;      // vertical exaggeration of the terrain
  const WALL = 2600;    // cliff height below the coastline (drawn units)
  const HOME = { tx: 0, ty: 1500, D: 118000, yaw: -0.16, pitch: 0.5 };

  let cam = Object.assign({}, HOME), anim = null, dirty = true, raf = 0;
  let W = 0, H = 0, F = 0, dpr = 1, CX = 0.5, CY = 0.54;
  let data = null, cards = [], zoneSel = null, stopSel = null, hover = null, lmBoxes = [];

  // ---------- island geometry ----------
  const poly = outline.map(([lon, lat]) => toXY(lat, lon));
  function inside(x, y) {
    let c = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
    }
    return c;
  }
  let area2 = 0;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) area2 += poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1];
  const CCW = area2 > 0;

  let seed = 7;
  const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  const HALLA = toXY(33.3617, 126.5292);
  // Well-known places, for orientation only. kind: peak | cone | airport | beach | falls | rock | island
  const LANDMARKS = [
    { name: "한라산", sub: "1,947m", lat: 33.3617, lon: 126.5292, kind: "peak" },
    { name: "성산일출봉", lat: 33.4581, lon: 126.9425, kind: "cone" },
    { name: "산방산", lat: 33.2384, lon: 126.3134, kind: "cone" },
    { name: "제주국제공항", lat: 33.5104, lon: 126.4914, kind: "airport" },
    { name: "용두암", lat: 33.5164, lon: 126.5122, kind: "rock", near: true },
    { name: "협재해수욕장", lat: 33.394, lon: 126.2397, kind: "beach" },
    { name: "중문색달해변", lat: 33.2443, lon: 126.4115, kind: "beach" },
    { name: "천지연폭포", lat: 33.2447, lon: 126.5594, kind: "falls" },
    { name: "섭지코지", lat: 33.4239, lon: 126.9307, kind: "rock", near: true },
    { name: "우도", lat: 33.505, lon: 126.955, kind: "island" },
  ].map((m) => Object.assign(m, { xy: toXY(m.lat, m.lon) }));
  const SEONGSAN = toXY(33.4581, 126.9425), SANBANG = toXY(33.2384, 126.3134);
  const cones = [];
  for (let i = 0; i < 70 && poly.length; i++) {
    const x = (rnd() - 0.5) * 70000, y = (rnd() - 0.5) * 40000;
    if (inside(x, y)) cones.push([x, y, 40 + rnd() * 130, 450 + rnd() * 500]);
  }
  function height(x, y) {
    const d = Math.hypot(x - HALLA[0], y - HALLA[1]);
    // Hallasan is a shield volcano: a broad, gentle slope stretched along the island's
    // WSW-ENE axis, a short steep summit dome, and the flat-floored Baengnokdam crater on top.
    const ax = x - HALLA[0], ay = y - HALLA[1];
    const u = ax * 0.966 + ay * 0.259, v = -ax * 0.259 + ay * 0.966;   // rotate ~15 deg to the island axis
    const de = Math.hypot(u, v / 0.5);
    let h = 1520 * Math.exp(-Math.pow(de / 10500, 1.55));               // the shield
    h += 430 * Math.exp(-Math.pow(d / 1900, 3.2));                       // summit dome, round-shouldered
    h -= 110 * Math.exp(-Math.pow(d / 420, 4));                          // Baengnokdam, flat-floored
    const ds = Math.hypot(x - SEONGSAN[0], y - SEONGSAN[1]);   // Seongsan: tuff cone with a bowl
    h += 260 * Math.exp(-Math.pow(ds / 520, 2)) - 120 * Math.exp(-Math.pow(ds / 240, 2));
    const db = Math.hypot(x - SANBANG[0], y - SANBANG[1]);     // Sanbangsan: lava dome
    h += 420 * Math.exp(-Math.pow(db / 620, 2.2));
    for (const c of cones) {
      const q = ((x - c[0]) ** 2 + (y - c[1]) ** 2) / (c[3] * c[3]);
      if (q < 6) h += c[2] * Math.exp(-q);
    }
    return h;
  }
  const ground = (x, y) => (inside(x, y) ? Math.max(0, height(x, y)) * ZEX : 0);

  // ---------- satellite night lights (NASA Black Marble 2016) ----------
  const SAT = { lat0: 33.15, lat1: 33.6, lon0: 126.1, lon1: 127.0, w: 0, h: 0, lum: null };
  function satAt(lat, lon) {
    if (!SAT.lum) return null;
    const fx = (lon - SAT.lon0) / (SAT.lon1 - SAT.lon0), fy = (SAT.lat1 - lat) / (SAT.lat1 - SAT.lat0);
    if (fx < 0 || fx >= 1 || fy < 0 || fy >= 1) return 0;
    return SAT.lum[Math.floor(fy * SAT.h) * SAT.w + Math.floor(fx * SAT.w)];
  }
  const satXY = (x, y) => satAt(LAT0 + y / MY, LON0 + x / MX);
  if (opts.nightLights) {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
      const g = c.getContext("2d"); g.drawImage(img, 0, 0);
      const px4 = g.getImageData(0, 0, c.width, c.height).data, lum = new Float32Array(c.width * c.height);
      for (let i = 0; i < lum.length; i++) {
        const y = (0.3 * px4[i * 4] + 0.6 * px4[i * 4 + 1] + 0.1 * px4[i * 4 + 2]) / 255;
        lum[i] = Math.pow(Math.max(0, Math.min(1, (y - 0.07) / 0.85)), 1.5);
      }
      SAT.w = c.width; SAT.h = c.height; SAT.lum = lum;
      recolor(); dirty = true; kick();
      if (opts.onSatellite) opts.onSatellite();
    };
    img.src = opts.nightLights;
  }

  // terrain mesh, flat-shaded "voxel" look under moonlight
  const NX = 140, NY = 82, X0 = -39500, X1 = 39500, Y0 = -23500, Y1 = 23500;
  const vx = new Float32Array((NX + 1) * (NY + 1)), vy = new Float32Array(vx.length), vz = new Float32Array(vx.length);
  const quads = [];
  const MOON = [-0.55, 0.45, 0.7];
  (function build() {
    if (!poly.length) return;
    for (let j = 0; j <= NY; j++) for (let i = 0; i <= NX; i++) {
      const k = j * (NX + 1) + i, x = X0 + ((X1 - X0) * i) / NX, y = Y0 + ((Y1 - Y0) * j) / NY;
      vx[k] = x; vy[k] = y; vz[k] = ground(x, y);
    }
    const dx = (X1 - X0) / NX, dy = (Y1 - Y0) / NY;
    for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {
      const cx = X0 + dx * (i + 0.5), cy = Y0 + dy * (j + 0.5);
      if (!inside(cx, cy)) continue;
      const a = j * (NX + 1) + i, b = a + 1, c = a + NX + 2, d = a + NX + 1;
      const nx = -((vz[b] + vz[c]) - (vz[a] + vz[d])) / (2 * dx), ny = -((vz[d] + vz[c]) - (vz[a] + vz[b])) / (2 * dy);
      const nl = Math.hypot(nx, ny, 1), lam = Math.max(0, (nx * MOON[0] + ny * MOON[1] + MOON[2]) / nl);
      const hh = (vz[a] + vz[b] + vz[c] + vz[d]) / 4 / ZEX;
      const jit = (rnd() - 0.5) * 0.08;                    // per-cell variation for the blocky look
      const t = Math.max(0, Math.min(1, 0.2 + (lam - 0.7) * 1.6 + Math.pow(hh / 1950, 0.8) * 0.25 + jit));
      quads.push({ a, b, c, d, t, hh, cx, cy, sat: 0, col: "", z: 0 });
    }
  })();

  // moonlit base colour, warmed where the satellite saw light at night
  function recolor() {
    for (const q of quads) {
      const s = satXY(q.cx, q.cy) || 0, t = q.t;
      q.sat = s;
      const dim = 1 - Math.min(0.45, s * 0.45);
      let r = (34 + 62 * t) * dim + 230 * s * 0.5, g = (66 + 96 * t) * dim + 160 * s * 0.45, bl = (74 + 92 * t) * dim + 70 * s * 0.3;
      // upper slopes turn to moonlit rock, the summit to pale snow
      const rock = Math.max(0, Math.min(1, (q.hh - 1050) / 500)), snow = Math.max(0, Math.min(1, (q.hh - 1620) / 260));
      const lift = 0.55 + t * 0.6;
      r += (128 * lift - r) * rock * 0.7; g += (140 * lift - g) * rock * 0.7; bl += (160 * lift - bl) * rock * 0.7;
      r += (215 * lift - r) * snow; g += (226 * lift - g) * snow; bl += (240 * lift - bl) * snow;
      q.col = `rgb(${Math.min(255, Math.round(r))},${Math.min(255, Math.round(g))},${Math.min(255, Math.round(bl))})`;
    }
  }
  recolor();

  // contour rings around Hallasan (radial search; good enough for a stylised cone)
  const CONTOURS = [300, 600, 900, 1200, 1500, 1800].map((lev) => {
    const ring = [];
    for (let i = 0; i < 96; i++) {
      const a = (i / 96) * 6.2832, ca = Math.cos(a), sa = Math.sin(a);
      let lo = 500, hi = 26000;
      for (let k = 0; k < 24; k++) {
        const mid = (lo + hi) / 2, x = HALLA[0] + ca * mid, y = HALLA[1] + sa * mid;
        if (inside(x, y) && height(x, y) > lev) lo = mid; else hi = mid;
      }
      const x = HALLA[0] + ca * lo, y = HALLA[1] + sa * lo;
      ring.push([x, y]);
    }
    return { lev, ring };
  });

  // cliff walls: split each coast edge so the rock face reads as a diorama
  const walls = [];
  (function buildWalls() {
    for (let i = 0; i < poly.length; i++) {
      const p = poly[i], q = poly[(i + 1) % poly.length];
      const len = Math.hypot(q[0] - p[0], q[1] - p[1]), n = Math.max(1, Math.round(len / 1400));
      let nx = (q[1] - p[1]) / len, ny = -(q[0] - p[0]) / len;
      if (!CCW) { nx = -nx; ny = -ny; }
      const lit = Math.max(0, nx * MOON[0] + ny * MOON[1]);
      for (let s = 0; s < n; s++) {
        const a = [p[0] + ((q[0] - p[0]) * s) / n, p[1] + ((q[1] - p[1]) * s) / n];
        const b = [p[0] + ((q[0] - p[0]) * (s + 1)) / n, p[1] + ((q[1] - p[1]) * (s + 1)) / n];
        walls.push({ a, b, lit, notch: 0.86 + rnd() * 0.22, z: 0 });
      }
    }
  })();

  // trees (decorative)
  const trees = [];
  (function buildTrees() {
    let guard = 0;
    while (trees.length < 1300 && guard++ < 9000) {
      const x = (rnd() - 0.5) * 76000, y = (rnd() - 0.5) * 44000;
      if (!inside(x, y)) continue;
      const d = Math.hypot(x - HALLA[0], y - HALLA[1]);
      if (height(x, y) > 1450) continue;                      // above the tree line
      if (Math.hypot(x - SEONGSAN[0], y - SEONGSAN[1]) < 500 || Math.hypot(x - SANBANG[0], y - SANBANG[1]) < 450) continue;
      const keep = d < 14000 ? 0.95 : 0.5;                     // forest thickens on the slopes
      if (rnd() > keep) continue;
      trees.push({ x, y, z: ground(x, y), s: 0.75 + rnd() * 0.6, hue: rnd() });
    }
  })();

  // data-dependent scenery
  let houses = [], roads = [];
  function buildScenery() {
    houses = []; roads = [];
    if (!data) return;
    const pts = data.stops.map((s) => toXY(s.lat, s.lon));
    let hs = 99;
    const hr = () => { hs = (hs * 1664525 + 1013904223) >>> 0; return hs / 4294967296; };
    pts.forEach(([x, y]) => {
      const n = 1 + (hr() < 0.6) + (hr() < 0.3);
      for (let k = 0; k < n; k++) {
        const a = hr() * 6.283, r = 60 + hr() * 170, hx = x + Math.cos(a) * r, hy = y + Math.sin(a) * r;
        if (!inside(hx, hy)) continue;
        houses.push({ x: hx, y: hy, z: ground(hx, hy), s: 0.8 + hr() * 0.5, roof: hr(), win: hr() < 0.55 });
      }
    });
    // roads: minimum spanning tree over stops, keeping short links only
    const n = pts.length;
    if (n > 1 && n < 3000) {
      const inT = new Uint8Array(n), best = new Float64Array(n).fill(Infinity), from = new Int32Array(n).fill(-1);
      best[0] = 0;
      for (let it = 0; it < n; it++) {
        let u = -1;
        for (let v = 0; v < n; v++) if (!inT[v] && (u < 0 || best[v] < best[u])) u = v;
        inT[u] = 1;
        if (from[u] >= 0 && best[u] < 4500) roads.push([pts[from[u]], pts[u]]);
        for (let v = 0; v < n; v++) {
          if (inT[v]) continue;
          const d = Math.hypot(pts[u][0] - pts[v][0], pts[u][1] - pts[v][1]);
          if (d < best[v]) { best[v] = d; from[v] = u; }
        }
      }
    }
  }

  // ---------- camera ----------
  const px = new Float32Array(vx.length), py = new Float32Array(vx.length), pd = new Float32Array(vx.length);
  let cy_, sy_, cp_, sp_, tz_ = 0;
  // The camera looks at the ground under its target, so zooming in never dives below the terrain.
  function prep() { cy_ = Math.cos(cam.yaw); sy_ = Math.sin(cam.yaw); cp_ = Math.cos(cam.pitch); sp_ = Math.sin(cam.pitch); tz_ = ground(cam.tx, cam.ty) * Math.max(0, Math.min(1, (40000 - cam.D) / 30000)); }
  function proj(x, y, z) {
    const ax = x - cam.tx, ay = y - cam.ty; z -= tz_;
    const x1 = ax * cy_ + ay * sy_, y1 = -ax * sy_ + ay * cy_;
    const depth = y1 * cp_ - z * sp_ + cam.D;
    if (depth < 200) return null;
    return [W * CX + (F * x1) / depth, H * CY - (F * (y1 * sp_ + z * cp_)) / depth, depth];
  }
  const projLL = (lat, lon, zAdd) => { const [x, y] = toXY(lat, lon); return proj(x, y, ground(x, y) + (zAdd || 0)); };
  const onScreen = (p, m) => p && p[0] > -m && p[0] < W + m && p[1] > -m && p[1] < H + m;

  // offscreen layers
  const dark = document.createElement("canvas"), dctx = dark.getContext("2d");
  function sprite(rgb) {
    const c = document.createElement("canvas"); c.width = c.height = 64;
    const g = c.getContext("2d"), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    gr.addColorStop(0, `rgba(${rgb},1)`); gr.addColorStop(0.55, `rgba(${rgb},.85)`); gr.addColorStop(1, `rgba(${rgb},0)`);
    g.fillStyle = gr; g.fillRect(0, 0, 64, 64); return c;
  }
  const HOLE = sprite("255,255,255"), POOL_C = sprite("150,195,255"), POOL_B = sprite("120,235,195"), WARM = sprite("255,196,120");

  function resize() {
    const r = canvas.getBoundingClientRect();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = Math.max(300, r.width); H = Math.max(260, r.height); F = Math.min(H * 1.55, W * 1.25);
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    dark.width = canvas.width; dark.height = canvas.height;
    dirty = true; kick();
  }

  // ---------- drawing helpers ----------
  const COL = { stop: "235,235,245", cctv: "120,180,255", bell: "110,230,185", prio: "255,179,64", blind: "255,84,84", half: "255,160,80" };
  function text(str, x, y, size, color, align, weight) {
    ctx.font = `${weight || 600} ${size}px ${opts.font || "sans-serif"}`;
    ctx.textAlign = align || "center"; ctx.textBaseline = "middle";
    ctx.lineWidth = 3.5; ctx.strokeStyle = "rgba(0,0,0,.8)"; ctx.lineJoin = "round";
    ctx.strokeText(str, x, y); ctx.fillStyle = color; ctx.fillText(str, x, y);
  }
  function ring(x, y, radiusM, stroke, fill) {
    ctx.beginPath();
    for (let i = 0; i <= 40; i++) {
      const a = (i / 40) * Math.PI * 2, qx = x + Math.cos(a) * radiusM, qy = y + Math.sin(a) * radiusM;
      const p = proj(qx, qy, ground(qx, qy) + 6);
      if (!p) return;
      i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]);
    }
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.stroke(); }
  }
  const pathPoly = (c, pts) => { c.beginPath(); pts.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1]))); c.closePath(); };
  // projected ground radius in pixels for a radius in metres
  const rpx = (meters, depth) => (F * meters) / depth;

  let stars = null;
  function drawSky() {
    const bg = ctx.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, "#02040a"); bg.addColorStop(0.45, "#060b18"); bg.addColorStop(1, "#03060d");
    ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
    if (!stars) { let s = 3; const r = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }; stars = Array.from({ length: 140 }, () => [r(), r(), r()]); }
    for (const [sx, sy, sb] of stars) { ctx.fillStyle = `rgba(200,215,255,${(0.15 + sb * 0.45).toFixed(2)})`; ctx.fillRect(sx * W, sy * H, sb > 0.85 ? 1.6 : 1, sb > 0.85 ? 1.6 : 1); }
    // moon
    const mx = W * 0.9, my = H * 0.12, mg = ctx.createRadialGradient(mx, my, 0, mx, my, 120);
    mg.addColorStop(0, "rgba(190,210,255,.18)"); mg.addColorStop(1, "rgba(190,210,255,0)");
    ctx.fillStyle = mg; ctx.fillRect(mx - 120, my - 120, 240, 240);
    ctx.fillStyle = "rgba(225,232,255,.9)"; ctx.beginPath(); ctx.arc(mx, my, 13, 0, 6.2832); ctx.fill();
    ctx.fillStyle = "#060b18"; ctx.beginPath(); ctx.arc(mx + 5, my - 3, 11.5, 0, 6.2832); ctx.fill();
    // sea plane
    const sea = [[-90000, -70000], [90000, -70000], [90000, 70000], [-90000, 70000]].map(([x, y]) => proj(x, y, -WALL));
    if (sea.every(Boolean)) {
      pathPoly(ctx, sea);
      const sg = ctx.createLinearGradient(0, Math.min(...sea.map((p) => p[1])), 0, H);
      sg.addColorStop(0, "#071226"); sg.addColorStop(1, "#0b1d3e");
      ctx.fillStyle = sg; ctx.fill();
    }
    ctx.lineWidth = 1; ctx.strokeStyle = "rgba(110,150,220,.06)";
    for (let g = -80000; g <= 80000; g += 8000) {
      for (const seg of [[g, -60000, g, 60000], [-80000, g * 0.75, 80000, g * 0.75]]) {
        const a = proj(seg[0], seg[1], -WALL), b = proj(seg[2], seg[3], -WALL);
        if (a && b) { ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
      }
    }
  }

  function drawIsland() {
    if (!poly.length) return;
    // surf line where the cliff meets the sea
    const base = poly.map((p) => proj(p[0], p[1], -WALL));
    if (base.every(Boolean)) {
      pathPoly(ctx, base);
      ctx.save(); ctx.shadowColor = "rgba(120,170,255,.55)"; ctx.shadowBlur = 22; ctx.strokeStyle = "rgba(140,185,255,.35)"; ctx.lineWidth = 2; ctx.stroke(); ctx.restore();
    }
    // cliff walls, far to near
    for (const w of walls) {
      const a = proj(w.a[0], w.a[1], 0), b = proj(w.b[0], w.b[1], 0);
      w.z = a && b ? a[2] + b[2] : -1;
    }
    walls.sort((m, n) => n.z - m.z);
    for (const w of walls) {
      if (w.z < 0) continue;
      const t0 = proj(w.a[0], w.a[1], 0), t1 = proj(w.b[0], w.b[1], 0);
      const b0 = proj(w.a[0], w.a[1], -WALL), b1 = proj(w.b[0], w.b[1], -WALL * w.notch);
      if (!b0 || !b1) continue;
      const k = 0.45 + w.lit * 0.75;
      pathPoly(ctx, [t0, t1, b1, b0]);
      ctx.fillStyle = `rgb(${Math.round(78 * k)},${Math.round(60 * k)},${Math.round(56 * k + 10)})`; ctx.fill();
      // strata
      ctx.lineWidth = 1;
      for (const f of [0.32, 0.6, 0.82]) {
        const s0 = proj(w.a[0], w.a[1], -WALL * f), s1 = proj(w.b[0], w.b[1], -WALL * f * w.notch);
        if (s0 && s1) { ctx.strokeStyle = `rgba(0,0,0,${(0.25 + f * 0.2).toFixed(2)})`; ctx.beginPath(); ctx.moveTo(s0[0], s0[1]); ctx.lineTo(s1[0], s1[1]); ctx.stroke(); }
      }
      // grass lip
      const g0 = proj(w.a[0], w.a[1], -WALL * 0.1), g1 = proj(w.b[0], w.b[1], -WALL * 0.1);
      if (g0 && g1) { pathPoly(ctx, [t0, t1, g1, g0]); ctx.fillStyle = `rgb(${Math.round(36 * k + 14)},${Math.round(80 * k + 18)},${Math.round(76 * k + 20)})`; ctx.fill(); }
    }
    // cap under the mesh so jagged cells never show gaps
    const cap = poly.map((p) => proj(p[0], p[1], 0));
    if (cap.every(Boolean)) { pathPoly(ctx, cap); ctx.fillStyle = "#2c4f4c"; ctx.fill(); }
    // terrain
    for (let k = 0; k < vx.length; k++) {
      const p = proj(vx[k], vy[k], vz[k]);
      if (p) { px[k] = p[0]; py[k] = p[1]; pd[k] = p[2]; } else pd[k] = -1;
    }
    for (const q of quads) q.z = pd[q.a] + pd[q.c];
    quads.sort((m, n) => n.z - m.z);
    ctx.lineWidth = 0.8;
    for (const q of quads) {
      if (pd[q.a] < 0 || pd[q.b] < 0 || pd[q.c] < 0 || pd[q.d] < 0) continue;
      ctx.beginPath(); ctx.moveTo(px[q.a], py[q.a]); ctx.lineTo(px[q.b], py[q.b]); ctx.lineTo(px[q.c], py[q.c]); ctx.lineTo(px[q.d], py[q.d]); ctx.closePath();
      ctx.fillStyle = q.col; ctx.strokeStyle = q.col; ctx.fill(); ctx.stroke();
    }
    // crater lake on the summit
    const lake = [];
    for (let i = 0; i < 24; i++) {
      const a = (i / 24) * 6.2832, x = HALLA[0] + Math.cos(a) * 300, y = HALLA[1] + Math.sin(a) * 230;
      const p = proj(x, y, ground(x, y) + 4); if (p) lake.push(p);
    }
    if (lake.length === 24) {
      pathPoly(ctx, lake); ctx.fillStyle = "#0c1a33"; ctx.fill();
      ctx.strokeStyle = "rgba(170,200,255,.45)"; ctx.lineWidth = 1; ctx.stroke();
    }
  }

  function drawScenery(near) {
    // roads
    if (roads.length) {
      ctx.lineCap = "round";
      for (const [p, q] of roads) {
        const pts = [];
        for (let s = 0; s <= 6; s++) {
          const x = p[0] + ((q[0] - p[0]) * s) / 6, y = p[1] + ((q[1] - p[1]) * s) / 6, r = proj(x, y, ground(x, y) + 3);
          if (!r) { pts.length = 0; break; } pts.push(r);
        }
        if (pts.length < 2) continue;
        ctx.lineWidth = Math.max(0.7, Math.min(4, rpx(30, pts[3][2])));
        ctx.strokeStyle = "rgba(190,196,210,.55)";
        ctx.beginPath(); pts.forEach((r, i) => (i ? ctx.lineTo(r[0], r[1]) : ctx.moveTo(r[0], r[1]))); ctx.stroke();
      }
      ctx.lineCap = "butt";
    }
    // trees and houses as billboards, far to near
    const items = [];
    for (const t of trees) { const p = proj(t.x, t.y, t.z); if (onScreen(p, 30)) items.push([p, t, 0]); }
    for (const h of houses) { const p = proj(h.x, h.y, h.z); if (onScreen(p, 30)) items.push([p, h, 1]); }
    items.sort((a, b) => b[0][2] - a[0][2]);
    const lights = [];
    for (const [p, o, kind] of items) {
      if (kind === 0) {
        const s = Math.max(2.6, Math.min(15, rpx(420, p[2]))) * o.s, h = s * 1.7;
        const g1 = o.hue < 0.5 ? [64, 138, 112] : [52, 120, 128], g2 = [g1[0] * 0.55, g1[1] * 0.55, g1[2] * 0.6];
        ctx.fillStyle = `rgb(${g1.join(",")})`;
        ctx.beginPath(); ctx.moveTo(p[0], p[1] - h); ctx.lineTo(p[0] - s * 0.6, p[1]); ctx.lineTo(p[0], p[1]); ctx.closePath(); ctx.fill();
        ctx.fillStyle = `rgb(${g2.map(Math.round).join(",")})`;
        ctx.beginPath(); ctx.moveTo(p[0], p[1] - h); ctx.lineTo(p[0] + s * 0.6, p[1]); ctx.lineTo(p[0], p[1]); ctx.closePath(); ctx.fill();
      } else {
        const w = Math.max(3.4, Math.min(20, rpx(300, p[2]))) * o.s, bh = w * 0.62, rh = w * 0.42;
        ctx.fillStyle = "#5c6480"; ctx.fillRect(p[0] - w / 2, p[1] - bh, w * 0.62, bh);
        ctx.fillStyle = "#3a4058"; ctx.fillRect(p[0] - w / 2 + w * 0.62, p[1] - bh, w * 0.38, bh);
        ctx.fillStyle = o.roof < 0.4 ? "#8c4a50" : o.roof < 0.75 ? "#4a6a9a" : "#6e7484";
        ctx.beginPath(); ctx.moveTo(p[0] - w / 2 - w * 0.08, p[1] - bh); ctx.lineTo(p[0] - w * 0.19, p[1] - bh - rh); ctx.lineTo(p[0] + w / 2 + w * 0.05, p[1] - bh - rh * 0.55); ctx.lineTo(p[0] + w / 2, p[1] - bh); ctx.closePath(); ctx.fill();
        if (o.win && w > 3.3) lights.push([p[0] - w * 0.2, p[1] - bh * 0.55, Math.max(1, w * 0.14)]);
      }
    }
    return lights;
  }

  function draw() {
    dirty = false; prep();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    drawSky();
    drawIsland();
    const near = cam.D < 26000, close = cam.D < 9000;
    const windows = drawScenery(near);
    if (!data) { drawWindows(windows); return; }

    const rr = data.radii || { cctv: 100, bell: 200 };
    // 1) tinted light pools exactly as large as each device's coverage
    const sources = [];
    for (const c of data.cctv) { const p = projLL(c[0], c[1], 4); if (onScreen(p, 60)) sources.push([p, rr.cctv, POOL_C]); }
    for (const b of data.bell) { const p = projLL(b[0], b[1], 4); if (onScreen(p, 60)) sources.push([p, rr.bell, POOL_B]); }
    ctx.globalCompositeOperation = "lighter";
    for (const [p, m, spr] of sources) {
      const r = Math.max(1.2, rpx(m, p[2]));
      ctx.globalAlpha = 0.32; ctx.drawImage(spr, p[0] - r, p[1] - r * sp_, r * 2, r * 2 * sp_);
    }
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";

    // 2) darkness over everything, with holes cut where light actually reaches
    dctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    dctx.globalCompositeOperation = "source-over";
    dctx.clearRect(0, 0, W, H);
    dctx.fillStyle = "rgba(2,4,14,.5)"; dctx.fillRect(0, 0, W, H);
    dctx.globalCompositeOperation = "destination-out";
    for (const [p, m] of sources) {
      const r = Math.max(1.2, rpx(m, p[2]));
      dctx.drawImage(HOLE, p[0] - r, p[1] - r * sp_, r * 2, r * 2 * sp_);
    }
    // high ground catches the moonlight, so Hallasan keeps its shape above the dark lowlands
    for (const q of quads) {
      if (q.hh < 350 || pd[q.a] < 0 || pd[q.b] < 0 || pd[q.c] < 0 || pd[q.d] < 0) continue;
      dctx.globalAlpha = Math.min(0.95, (q.hh - 350) / 900);
      dctx.beginPath(); dctx.moveTo(px[q.a], py[q.a]); dctx.lineTo(px[q.b], py[q.b]); dctx.lineTo(px[q.c], py[q.c]); dctx.lineTo(px[q.d], py[q.d]); dctx.closePath();
      dctx.fillStyle = "#000"; dctx.fill();
    }
    dctx.globalAlpha = 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(dark, 0, 0); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 3) real night lights from the satellite, then light sources and windows
    // the satellite grid is ~500 m, so its glow fades as the view zooms past that scale
    const satFade = Math.max(0.12, Math.min(1, (cam.D - 6000) / 34000));
    if (SAT.lum) {
      ctx.globalCompositeOperation = "lighter";
      for (const q of quads) {
        if (q.sat < 0.06 || pd[q.a] < 0 || pd[q.c] < 0) continue;
        const cxp = (px[q.a] + px[q.c]) / 2, cyp = (py[q.a] + py[q.c]) / 2;
        const r = Math.max(2.5, Math.hypot(px[q.c] - px[q.a], py[q.c] - py[q.a]) * 0.9);
        ctx.globalAlpha = Math.min(0.42, q.sat * 0.42) * satFade;
        ctx.drawImage(WARM, cxp - r, cyp - r * 0.75, r * 2, r * 1.5);
      }
      ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";
    }
    drawWindows(windows);
    ctx.globalCompositeOperation = "lighter";
    for (const [p, , spr] of sources) {
      const r = Math.max(1, Math.min(5, rpx(24, p[2])));
      ctx.globalAlpha = near ? 0.95 : 0.6; ctx.drawImage(spr, p[0] - r * 2, p[1] - r * 2, r * 4, r * 4);
    }
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";


    // zones (hover and selection only)
    if (!close) {
      for (const z of data.zones) {
        const sel = zoneSel === z.key, hov = hover && hover.type === "zone" && hover.key === z.key;
        if (!sel && !hov) continue;
        const [x, y] = toXY(z.lat, z.lon);
        ctx.lineWidth = 2;
        ring(x, y, 950, "rgba(255,255,255,.85)", "rgba(255,255,255,.06)");
      }
    }
    // selected stop: show the two radii
    if (stopSel) {
      const [x, y] = toXY(stopSel.lat, stopSel.lon);
      ctx.lineWidth = 1.6; ctx.setLineDash([5, 4]);
      ring(x, y, rr.bell, `rgba(${COL.bell},.9)`, null);
      ring(x, y, rr.cctv, `rgba(${COL.cctv},.95)`, null);
      ctx.setLineDash([]);
    }

    drawFlats();
    // 4) stops: blind spots in red, half-covered in orange, covered in white
    for (const s of data.stops) {
      const p = projLL(s.lat, s.lon, 10);
      s._p = onScreen(p, 20) ? p : null;
      if (!s._p) continue;
      const people = s.demand !== 0, blind = people && s.gaps.includes("eyes") && s.gaps.includes("bell");
      const half = people && !blind && (s.gaps.includes("eyes") || s.gaps.includes("bell"));
      const r = Math.max(near ? 3.2 : 2, Math.min(11, rpx(70, p[2])));
      if (blind) {
        const g = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], r * 3.2);
        g.addColorStop(0, `rgba(${COL.blind},.55)`); g.addColorStop(1, `rgba(${COL.blind},0)`);
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p[0], p[1], r * 3.2, 0, 6.2832); ctx.fill();
        ctx.fillStyle = "#12060a"; ctx.beginPath(); ctx.arc(p[0], p[1], r, 0, 6.2832); ctx.fill();
        ctx.strokeStyle = `rgb(${COL.blind})`; ctx.lineWidth = Math.max(1.2, r * 0.38); ctx.stroke();
      } else if (half) {
        ctx.fillStyle = `rgba(${COL.half},.95)`; ctx.beginPath(); ctx.arc(p[0], p[1], r * 0.75, 0, 6.2832); ctx.fill();
      } else {
        ctx.fillStyle = `rgba(${COL.stop},${people ? 0.85 : 0.45})`; ctx.beginPath(); ctx.arc(p[0], p[1], r * 0.6, 0, 6.2832); ctx.fill();
      }
    }

    // the mountain hides whatever lies behind it, then landmarks sit on top
    drawMountain();
    drawRelief();
    drawMinis();
    drawLandmarks(close);

    // 5) priority beams
    const tops = cards.slice().sort((a, b) => b.rank - a.rank);
    for (const c of tops) {
      const [x, y] = toXY(c.lat, c.lon), g0 = ground(x, y);
      const dem = typeof c.demand === "number" ? Math.min(60, 12 * Math.log1p(c.demand / 4)) : 6;   // log so big stops stay on screen
      const hgt = Math.max(220, cam.D * 0.045) + dem * Math.max(3, cam.D * 0.0007);
      const a = proj(x, y, g0), b = proj(x, y, g0 + hgt);
      if (!a || !b) continue;
      const sel = stopSel && stopSel.id === c.id;
      const grad = ctx.createLinearGradient(a[0], a[1], b[0], b[1]);
      grad.addColorStop(0, `rgba(${COL.prio},.05)`); grad.addColorStop(1, `rgba(${COL.prio},.95)`);
      ctx.strokeStyle = grad; ctx.lineWidth = sel ? 3.5 : 2.2; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      const halo = ctx.createRadialGradient(b[0], b[1], 0, b[0], b[1], 26);
      halo.addColorStop(0, `rgba(${COL.prio},.55)`); halo.addColorStop(1, `rgba(${COL.prio},0)`);
      ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(b[0], b[1], 26, 0, 6.2832); ctx.fill();
      ctx.fillStyle = sel ? "#fff" : "#ffb340"; ctx.beginPath(); ctx.arc(b[0], b[1], 12, 0, 6.2832); ctx.fill();
      ctx.font = `700 12px ${opts.font || "sans-serif"}`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillStyle = "#1d1300";
      ctx.fillText(String(c.rank), b[0], b[1] + 0.5);
      c._p = b;
    }

    // 6) labels
    if (!close) {
      const shown = [];
      for (const z of data.zones) {
        if (shown.length >= (near ? 30 : 11)) break;
        if (!near && z.gap_stops === 0) continue;
        const p = projLL(z.lat, z.lon, 0);
        if (!p || p[0] < 40 || p[0] > W - 40 || p[1] < 20 || p[1] > H - 20) continue;
        if (shown.some((q) => Math.abs(q[0] - p[0]) < 64 && Math.abs(q[1] - p[1]) < 20)) continue;
        if (lmBoxes.some((b) => p[0] + 30 > b[0] && p[0] - 30 < b[0] + b[2] && p[1] + 28 > b[1] && p[1] + 8 < b[1] + b[3])) continue;
        shown.push(p);
        text(z.label, p[0], p[1] + 18, 13, zoneSel === z.key ? "#fff" : "rgba(255,255,255,.9)");
      }
    } else {
      const placed = [];
      const list = data.stops.filter((s) => s._p).sort((a, b) => (stopSel && stopSel.id === a.id ? -1 : stopSel && stopSel.id === b.id ? 1 : a.rank - b.rank));
      for (const s of list) {
        if (placed.length > 22) break;
        if (s._p[0] < 60 || s._p[0] > W - 60 || s._p[1] < 20 || s._p[1] > H - 30) continue;
        const sel = stopSel && stopSel.id === s.id;
        const ly = s._p[1] + (sel ? 24 : 17);
        if (placed.some((q) => Math.abs(q[0] - s._p[0]) < 132 && Math.abs(q[1] - ly) < 19)) continue;
        placed.push([s._p[0], ly]);
        text(s.name, s._p[0], ly, sel ? 14 : 12.5, sel ? "#fff" : "rgba(255,255,255,.88)", "center", sel ? 700 : 500);
      }
    }
    if (hover && hover.label && hover.p) text(hover.label, hover.p[0], hover.p[1] - 18, 12.5, "#fff");
  }

  // Hallasan is redrawn over the light layers so city glow behind it can't shine through.
  function drawMountain() {
    ctx.lineWidth = 0.8;
    for (const q of quads) {                       // quads are already sorted far to near
      if (q.hh < 140 || pd[q.a] < 0 || pd[q.b] < 0 || pd[q.c] < 0 || pd[q.d] < 0) continue;
      ctx.beginPath(); ctx.moveTo(px[q.a], py[q.a]); ctx.lineTo(px[q.b], py[q.b]); ctx.lineTo(px[q.c], py[q.c]); ctx.lineTo(px[q.d], py[q.d]); ctx.closePath();
      ctx.globalAlpha = Math.min(1, (q.hh - 140) / 260);
      ctx.fillStyle = q.col; ctx.fill();
      ctx.strokeStyle = q.col; ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  // Moonlit contour lines keep Hallasan readable under the darkness layer.
  function drawRelief() {
    for (const c of CONTOURS) {
      const pts = c.ring.map(([x, y]) => proj(x, y, ground(x, y) + 20));
      if (!pts.every(Boolean)) continue;
      ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]))); ctx.closePath();
      ctx.strokeStyle = `rgba(200,220,255,${(0.08 + (c.lev / 1800) * 0.24).toFixed(2)})`;
      ctx.lineWidth = c.lev >= 1800 ? 1.4 : 1; ctx.stroke();
    }
  }


  // ---------- landmark miniatures (original simplified drawings, slightly enlarged like a diorama) ----------
  const MOON_ANG = Math.atan2(MOON[1], MOON[0]);
  const ll = (lat, lon) => toXY(lat, lon);
  const M_UDO = ll(33.5035, 126.9535), M_UDO_LH = ll(33.4985, 126.9690), M_SEOP_LH = ll(33.4232, 126.9318);
  const M_AIR = ll(33.5113, 126.4930), M_FALLS = ll(33.2475, 126.5543), M_DRAGON = ll(33.5162, 126.5120);
  const BEACHES = [
    { c: ll(33.3940, 126.2397), sea: [-0.62, 0.78] },     // Hyeopjae opens to the north-west
    { c: ll(33.2443, 126.4115), sea: [0.05, -1] },        // Jungmun opens to the south
  ];
  const shade = (rgb, k) => `rgb(${rgb.map((v) => Math.max(0, Math.min(255, Math.round(v * k)))).join(",")})`;

  // A solid of revolution around (cx, cy): rings of [radius m, height m or null = sit on ground].
  // Faces are sorted far to near and lit by the moon so the shape reads at night.
  function lathe(cx, cy, prof, n, o) {
    const sx = o.sx || 1, sy = o.sy || 1, faces = [], pts = [];
    for (let k = 0; k < prof.length; k++) {
      const row = [];
      for (let i = 0; i < n; i++) {
        const a = (i / n) * 6.2832, r = prof[k][0];
        const x = cx + Math.cos(a) * r * sx, y = cy + Math.sin(a) * r * sy;
        let z = prof[k][1] === null ? ground(x, y) - 30 : prof[k][1] * ZEX;
        if (o.jag && k === prof.length - 1) z += o.jag(i) * ZEX;
        row.push(proj(x, y, z));
      }
      pts.push(row);
    }
    for (let k = 0; k < prof.length - 1; k++) {
      const base = (o.colors && o.colors[k]) || o.color;
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n, q = [pts[k][i], pts[k][j], pts[k + 1][j], pts[k + 1][i]];
        if (q.some((v) => !v)) continue;
        const a = ((i + 0.5) / n) * 6.2832, lam = 0.5 + 0.5 * Math.cos(a - MOON_ANG);
        const tex = o.streak ? 0.9 + 0.2 * Math.abs(Math.sin(i * 12.9898)) : 1;
        faces.push({ d: (q[0][2] + q[1][2] + q[2][2] + q[3][2]) / 4, q, col: shade(base, (0.45 + 0.75 * lam) * tex) });
      }
    }
    if (o.cap) {   // crater floor or flat top, drawn between the far and near walls
      const ring = [], z = o.cap.z * ZEX;
      for (let i = 0; i < n; i++) { const a = (i / n) * 6.2832; ring.push(proj(cx + Math.cos(a) * o.cap.r * sx, cy + Math.sin(a) * o.cap.r * sy, z)); }
      const c = proj(cx, cy, z);
      if (c && ring.every(Boolean)) faces.push({ d: c[2], q: ring, col: o.cap.col });
    }
    faces.sort((a, b) => b.d - a.d);
    ctx.lineJoin = "round"; ctx.lineWidth = 0.6;
    for (const f of faces) {
      ctx.beginPath(); f.q.forEach((v, i) => (i ? ctx.lineTo(v[0], v[1]) : ctx.moveTo(v[0], v[1]))); ctx.closePath();
      ctx.fillStyle = f.col; ctx.strokeStyle = f.col; ctx.fill(); ctx.stroke();
    }
    return pts[prof.length - 1];
  }

  function worldPoly(list, z, fill) {
    const ps = list.map(([x, y]) => proj(x, y, z === null ? ground(x, y) + 4 : z));
    if (!ps.every(Boolean)) return false;
    ctx.beginPath(); ps.forEach((v, i) => (i ? ctx.lineTo(v[0], v[1]) : ctx.moveTo(v[0], v[1]))); ctx.closePath();
    ctx.fillStyle = fill; ctx.fill(); return true;
  }
  function glow(p, r, rgb, a) {
    const g = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], r);
    g.addColorStop(0, `rgba(${rgb},${a})`); g.addColorStop(1, `rgba(${rgb},0)`);
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p[0], p[1], r, 0, 6.2832); ctx.fill();
  }

  // Flat landmarks sit under the stop markers: runways and beaches.
  function drawFlats() {
    // Jeju airport: main runway 07/25 (~065 deg) and the shorter 13/31 crossing it
    const rw = (len, wid, hdg, off) => {
      const ux = Math.sin(hdg), uy = Math.cos(hdg), vx = -uy, vy = ux;
      const cx = M_AIR[0] + (off || [0, 0])[0], cy = M_AIR[1] + (off || [0, 0])[1];
      const corner = (s, t) => [cx + ux * s * len / 2 + vx * t * wid / 2, cy + uy * s * len / 2 + vy * t * wid / 2];
      worldPoly([corner(-1, -1), corner(1, -1), corner(1, 1), corner(-1, 1)], null, "rgba(24,27,34,.96)");
      const near = cam.D < 40000;
      for (let k = -9; k <= 9; k++) {                 // centre line dashes
        const a = proj(cx + ux * k * len / 20, cy + uy * k * len / 20, ground(cx, cy) + 6);
        const b = proj(cx + ux * (k + 0.45) * len / 20, cy + uy * (k + 0.45) * len / 20, ground(cx, cy) + 6);
        if (a && b) { ctx.strokeStyle = "rgba(255,255,255,.7)"; ctx.lineWidth = near ? 1.4 : 0.7; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
      }
      for (let k = 0; k <= 24; k++) for (const t of [-1, 1]) {   // edge lights
        const [x, y] = corner(-1 + (2 * k) / 24, t * 1.15), p = proj(x, y, ground(x, y) + 8);
        if (p) { ctx.fillStyle = k === 0 || k === 24 ? "rgba(120,255,160,.95)" : "rgba(255,236,190,.95)"; ctx.fillRect(p[0] - 1, p[1] - 1, near ? 2.4 : 1.6, near ? 2.4 : 1.6); }
      }
      for (const s of [-1, 1]) for (let k = 1; k <= 6; k++) {    // approach lights beyond each end
        const [x, y] = corner(s * (1 + k * 0.035), 0), p = proj(x, y, 8);
        if (p) glow(p, near ? 5 : 3, "255,220,150", 0.8);
      }
    };
    rw(3180, 150, (65 * Math.PI) / 180);
    rw(1900, 110, (130 * Math.PI) / 180, [-350, -250]);

    // beaches: a crescent of pale sand and moonlit emerald shallows
    for (const b of BEACHES) {
      const [sx, sy] = b.sea, base = Math.atan2(sy, sx), cx = b.c[0] - sx * 700, cy = b.c[1] - sy * 700;
      const arc = (r0, r1, spread) => {
        const out = [], inn = [];
        for (let i = 0; i <= 16; i++) {
          const a = base - spread + (2 * spread * i) / 16;
          out.push([cx + Math.cos(a) * r1, cy + Math.sin(a) * r1]); inn.push([cx + Math.cos(a) * r0, cy + Math.sin(a) * r0]);
        }
        return out.concat(inn.reverse());
      };
      worldPoly(arc(980, 1500, 0.8), 2, "rgba(40,190,175,.18)");
      worldPoly(arc(800, 1000, 0.76), 2, "rgba(70,220,200,.32)");
      worldPoly(arc(660, 800, 0.72), 6, "rgba(238,228,200,.7)");
    }
    // Cheonjiyeon falls: a basalt gorge wall with a white ribbon of water and a misty pool
    const p = proj(M_FALLS[0], M_FALLS[1], ground(M_FALLS[0], M_FALLS[1]));
    if (p && cam.D < 70000) {
      const s = Math.max(10, Math.min(46, rpx(900, p[2])));
      ctx.fillStyle = "#1f2a26";
      ctx.beginPath(); ctx.moveTo(p[0] - s * 0.7, p[1]); ctx.lineTo(p[0] - s * 0.55, p[1] - s * 0.9); ctx.lineTo(p[0] - s * 0.1, p[1] - s); ctx.lineTo(p[0] + s * 0.5, p[1] - s * 0.92); ctx.lineTo(p[0] + s * 0.7, p[1]); ctx.closePath(); ctx.fill();
      ctx.fillStyle = "#2f4a3c"; ctx.fillRect(p[0] - s * 0.6, p[1] - s * 1.02, s * 1.15, s * 0.14);
      const wg = ctx.createLinearGradient(0, p[1] - s, 0, p[1]);
      wg.addColorStop(0, "rgba(235,250,255,.95)"); wg.addColorStop(1, "rgba(160,230,240,.75)");
      ctx.fillStyle = wg; ctx.fillRect(p[0] - s * 0.08, p[1] - s * 0.95, s * 0.16, s * 0.9);
      ctx.fillStyle = "rgba(60,190,190,.85)"; ctx.beginPath(); ctx.ellipse(p[0], p[1], s * 0.45, s * 0.14, 0, 0, 6.2832); ctx.fill();
      glow([p[0], p[1] - s * 0.1], s * 0.5, "210,245,255", 0.45);
    }
  }

  function lighthouse(xy, hM, sea) {
    const g0 = Math.max(0, ground(xy[0], xy[1])), a = proj(xy[0], xy[1], g0), b = proj(xy[0], xy[1], g0 + hM * ZEX);
    if (!a || !b) return;
    const w = Math.max(1.6, rpx(70, a[2]));
    ctx.fillStyle = "#e9edf5"; ctx.beginPath();
    ctx.moveTo(a[0] - w, a[1]); ctx.lineTo(b[0] - w * 0.6, b[1]); ctx.lineTo(b[0] + w * 0.6, b[1]); ctx.lineTo(a[0] + w, a[1]); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#c8323c"; ctx.fillRect(b[0] - w * 0.75, b[1] - w * 1.2, w * 1.5, w * 1.2);
    // a soft beam out to sea
    const ang = Math.atan2(-(sea[1]), sea[0]), L = Math.max(30, rpx(2600, b[2]));
    const gr = ctx.createRadialGradient(b[0], b[1], 0, b[0], b[1], L);
    gr.addColorStop(0, "rgba(255,240,190,.55)"); gr.addColorStop(1, "rgba(255,240,190,0)");
    ctx.fillStyle = gr; ctx.beginPath(); ctx.moveTo(b[0], b[1]); ctx.arc(b[0], b[1] - w * 0.6, L, ang - 0.16, ang + 0.16); ctx.closePath(); ctx.fill();
    glow([b[0], b[1] - w * 0.6], Math.max(6, w * 5), "255,236,170", 0.95);
  }

  // Tall landmarks are drawn over the mountain pass so they stay readable.
  function drawMinis() {
    // Seongsan Ilchulbong: a tuff cone whose rim breaks into a crown of rock teeth, grassy bowl inside
    lathe(SEONGSAN[0], SEONGSAN[1], [[900, null], [760, 40], [620, 150], [540, 182]], 22, {
      color: [96, 98, 86], colors: [[70, 92, 72], [104, 100, 86], [118, 112, 96]],
      jag: (i) => (i % 2 ? 26 : -4) + 6 * Math.sin(i * 2.3), cap: { r: 470, z: 120, col: "rgb(52,78,56)" }, sx: 1.35, sy: 1.25 });
    // Sanbangsan: a steep bell-shaped lava dome with vertical rock streaks
    lathe(SANBANG[0], SANBANG[1], [[1250, null], [1120, 70], [1020, 200], [880, 320], [650, 385], [360, 410], [0, 418]], 26, {
      color: [104, 98, 88], colors: [[66, 86, 66], [110, 104, 92], [112, 106, 94], [104, 100, 90], [92, 100, 84], [80, 98, 78]], streak: true, sx: 1.2, sy: 1.0 });
    // Udo: a low green island, sandy shore, Udobong rising on its south-east side
    lathe(M_UDO[0], M_UDO[1], [[1500, 0], [1420, 10], [1250, 22], [700, 40], [0, 46]], 24, {
      color: [58, 82, 62], colors: [[200, 190, 160], [72, 98, 70], [62, 88, 64], [58, 82, 60]], sx: 1.25, sy: 0.85 });
    lathe(M_UDO_LH[0] - 600, M_UDO_LH[1] + 250, [[520, 40], [380, 95], [180, 132], [0, 132]], 16, { color: [74, 96, 70], sx: 1.1 });
    lighthouse(M_UDO_LH, 70, [1, -0.3]);
    if (cam.D < 60000) lighthouse(M_SEOP_LH, 50, [0.6, -0.8]);

    let p;
    // Yongduam: a basalt rock rising from the surf like a dragon's head, jaws open to the sky
    p = proj(M_DRAGON[0], M_DRAGON[1], 0);
    if (p && cam.D < 30000) {
      const s = Math.max(12, Math.min(90, rpx(420, p[2])));
      ctx.save(); ctx.translate(p[0], p[1]);
      ctx.fillStyle = "rgba(180,230,255,.25)"; ctx.beginPath(); ctx.ellipse(0, 0, s * 0.75, s * 0.16, 0, 0, 6.2832); ctx.fill();
      ctx.fillStyle = "#262a30"; ctx.strokeStyle = "rgba(200,220,255,.75)"; ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(-s * 0.35, 0);
      ctx.bezierCurveTo(-s * 0.32, -s * 0.45, -s * 0.12, -s * 0.8, s * 0.05, -s * 0.95);   // neck up
      ctx.lineTo(s * 0.42, -s * 1.08);                                                    // upper jaw
      ctx.lineTo(s * 0.3, -s * 0.92);
      ctx.lineTo(s * 0.12, -s * 0.86);                                                    // open mouth
      ctx.lineTo(s * 0.36, -s * 0.72);                                                    // lower jaw
      ctx.bezierCurveTo(s * 0.18, -s * 0.6, s * 0.12, -s * 0.3, s * 0.3, 0);
      ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.fillStyle = "rgba(255,220,150,.9)"; ctx.beginPath(); ctx.arc(s * 0.12, -s * 0.97, Math.max(1, s * 0.03), 0, 6.2832); ctx.fill();
      ctx.restore();
    }
  }

  function landmarkIcon(kind, x, y, s) {
    ctx.save(); ctx.translate(x, y);
    ctx.fillStyle = "#fff"; ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.4; ctx.lineJoin = "round"; ctx.lineCap = "round";
    if (kind === "peak" || kind === "cone") {
      ctx.beginPath(); ctx.moveTo(-s, s * 0.7); ctx.lineTo(0, -s * 0.8); ctx.lineTo(s, s * 0.7); ctx.closePath(); ctx.fill();
      if (kind === "peak") { ctx.fillStyle = "#1a2234"; ctx.beginPath(); ctx.moveTo(-s * 0.35, -s * 0.2); ctx.lineTo(0, -s * 0.8); ctx.lineTo(s * 0.35, -s * 0.2); ctx.closePath(); ctx.fill(); }
    } else if (kind === "airport") {
      ctx.beginPath(); ctx.moveTo(0, -s); ctx.lineTo(0, s); ctx.moveTo(-s, 0.1 * s); ctx.lineTo(s, 0.1 * s); ctx.moveTo(-s * 0.45, s * 0.85); ctx.lineTo(s * 0.45, s * 0.85); ctx.stroke();
    } else if (kind === "beach") {
      ctx.beginPath(); for (let i = 0; i <= 8; i++) { const xx = -s + (2 * s * i) / 8, yy = Math.sin(i * 1.6) * s * 0.3; i ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy); } ctx.stroke();
      ctx.beginPath(); for (let i = 0; i <= 8; i++) { const xx = -s + (2 * s * i) / 8, yy = s * 0.55 + Math.sin(i * 1.6) * s * 0.3; i ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy); } ctx.stroke();
    } else if (kind === "falls") {
      ctx.beginPath(); ctx.moveTo(0, -s); ctx.bezierCurveTo(s * 0.9, 0, s * 0.6, s, 0, s); ctx.bezierCurveTo(-s * 0.6, s, -s * 0.9, 0, 0, -s); ctx.fill();
    } else if (kind === "island") {
      ctx.beginPath(); ctx.ellipse(0, s * 0.2, s, s * 0.55, 0, 0, 6.2832); ctx.fill();
    } else {
      ctx.beginPath(); ctx.moveTo(-s, s * 0.7); ctx.lineTo(-s * 0.4, -s * 0.6); ctx.lineTo(s * 0.3, -s * 0.2); ctx.lineTo(s, s * 0.7); ctx.closePath(); ctx.fill();
    }
    ctx.restore();
  }

  // Landmark tags: a thin stem from the ground to a dark pill with an icon and the name.
  function drawLandmarks(close) {
    ctx.font = `600 13px ${opts.font || "sans-serif"}`;
    const items = [];
    for (const m of LANDMARKS) {
      if (m.near && cam.D > 30000) continue;          // minor places only when zoomed in
      const g = ground(m.xy[0], m.xy[1]), base = proj(m.xy[0], m.xy[1], g);
      if (!onScreen(base, 80)) continue;
      if (m.kind === "peak") {
        // hang the summit tag on the empty south face instead of above it, where priority pins crowd in
        prep();
        const fx = m.xy[0] + 2600 * sy_, fy = m.xy[1] - 2600 * cy_, face = proj(fx, fy, ground(fx, fy));
        if (face) { items.push({ m, base, top: [face[0], face[1] + 12], below: true }); continue; }
      }
      items.push({ m, base, top: [base[0], base[1] - 18] });
    }
    // place near ones first; anything that would overlap an earlier tag moves up
    items.sort((a, b) => a.base[2] - b.base[2]);
    const boxes = [];
    for (const it of items) {
      const label = it.m.sub ? `${it.m.name} ${it.m.sub}` : it.m.name;
      const pw = ctx.measureText(label).width + 34, ph = 24;
      let y0 = it.top[1] - ph;
      const x0 = it.top[0] - pw / 2;
      for (let guard = 0; guard < 8; guard++) {
        const hit = boxes.find((b) => x0 < b[0] + b[2] + 4 && x0 + pw + 4 > b[0] && y0 < b[1] + b[3] + 3 && y0 + ph + 3 > b[1]);
        if (!hit) break;
        y0 = hit[1] - ph - 4;
      }
      boxes.push([x0, y0, pw, ph, it.m]);
      Object.assign(it, { label, pw, ph, x0, y0 });
    }
    lmBoxes = boxes;
    for (const { m, base, label, pw, ph, x0, y0, below } of items.slice().reverse()) {
      const top = below ? [x0 + pw / 2, y0] : [base[0], y0 + ph];
      ctx.strokeStyle = "rgba(255,255,255,.55)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(base[0], base[1]); ctx.lineTo(top[0], top[1]); ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,.95)"; ctx.beginPath(); ctx.arc(base[0], base[1], 2.2, 0, 6.2832); ctx.fill();
      ctx.fillStyle = m.kind === "peak" ? "rgba(26,34,52,.95)" : "rgba(16,20,30,.88)";
      ctx.strokeStyle = m.kind === "peak" ? "rgba(220,232,255,.75)" : "rgba(255,255,255,.35)";
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x0, y0, pw, ph, 12); else ctx.rect(x0, y0, pw, ph);
      ctx.fill(); ctx.stroke();
      landmarkIcon(m.kind, x0 + 14, y0 + ph / 2, 5.5);
      ctx.fillStyle = "#fff"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.fillText(label, x0 + 25, y0 + ph / 2 + 0.5);
    }
  }

  function drawWindows(windows) {
    ctx.globalCompositeOperation = "lighter";
    for (const [x, y, s] of windows) {
      ctx.globalAlpha = 0.9; ctx.fillStyle = "rgb(255,200,120)"; ctx.fillRect(x - s / 2, y - s / 2, s, s);
      ctx.globalAlpha = 0.35; ctx.drawImage(WARM, x - s * 3, y - s * 3, s * 6, s * 6);
    }
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";
  }

  function kick() { if (STILL) { if (dirty && W) draw(); return; } if (!raf) raf = requestAnimationFrame(frame); }
  function frame(t) {
    raf = 0;
    if (anim) {
      const k = Math.min(1, (t - anim.t0) / anim.ms), e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      for (const key in anim.to) cam[key] = anim.from[key] + (anim.to[key] - anim.from[key]) * e;
      dirty = true;
      if (k >= 1) anim = null;
    }
    if (dirty) draw();
    if (anim) kick();
  }
  function fly(to, ms) {
    if (STILL || ms === 0) { Object.assign(cam, to); anim = null; dirty = true; draw(); return; }
    anim = { from: Object.assign({}, cam), to, t0: performance.now(), ms: ms || 1100 }; kick();
  }

  // ---------- picking and input ----------
  function pick(mx, my, touch) {
    if (!data) return null;
    let best = null, bd = 1e9;
    const consider = (p, r, item) => { if (!p) return; const d = Math.hypot(p[0] - mx, p[1] - my); if (d < r && d < bd) { bd = d; best = item; } };
    // 순위 핀이 지명 표시보다 먼저예요. 손가락은 더 넓게 잡아요.
    for (const c of cards) consider(c._p, touch ? 24 : 16, { type: "stop", id: c.id, label: `${c.rank}. ${c.name}`, p: c._p });
    if (best) return best;
    const lm = lmBoxes.find((b) => mx >= b[0] && mx <= b[0] + b[2] && my >= b[1] && my <= b[1] + b[3]);
    if (lm) return { type: "landmark", m: lm[4], label: lm[4].name + " 가까이 보기", p: [lm[0] + lm[2] / 2, lm[1] + 4] };
    if (cam.D < 26000) for (const s of data.stops) consider(s._p, 12, { type: "stop", id: s.id, label: s.name, p: s._p });
    if (best) return best;
    if (cam.D >= 9000) for (const z of data.zones) { const p = projLL(z.lat, z.lon, 0); consider(p, Math.max(18, (F * 950) / (p ? p[2] : 1e9)), { type: "zone", key: z.key, label: z.label, p }); }
    return best;
  }
  let drag = null;
  const touches = new Map();   // 두 손가락으로 벌리고 오므리면 확대·축소해요
  let pinch = null;
  canvas.addEventListener("pointerdown", (e) => {
    try { canvas.setPointerCapture(e.pointerId); } catch (_) { /* 이미 끝난 터치 */ }
    touches.set(e.pointerId, [e.clientX, e.clientY]);
    if (touches.size === 2) {
      const [p, q] = [...touches.values()];
      pinch = Math.hypot(p[0] - q[0], p[1] - q[1]); drag = null; return;
    }
    drag = { x: e.clientX, y: e.clientY, moved: 0, touch: e.pointerType === "touch", rotate: e.button === 2 || e.ctrlKey || e.shiftKey };
  });
  const endTouch = (e) => { touches.delete(e.pointerId); if (touches.size < 2) pinch = null; };
  canvas.addEventListener("pointercancel", (e) => { endTouch(e); drag = null; });
  canvas.addEventListener("pointermove", (e) => {
    const r = canvas.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    if (touches.has(e.pointerId)) touches.set(e.pointerId, [e.clientX, e.clientY]);
    if (pinch && touches.size === 2) {
      const [p, q] = [...touches.values()], d = Math.hypot(p[0] - q[0], p[1] - q[1]);
      if (d > 0) { self.zoom(pinch / d); pinch = d; }
      return;
    }
    if (drag) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.x = e.clientX; drag.y = e.clientY; drag.moved += Math.abs(dx) + Math.abs(dy);
      anim = null;
      if (drag.rotate) { cam.yaw -= dx * 0.006; cam.pitch = Math.max(0.35, Math.min(1.35, cam.pitch + dy * 0.005)); }
      else {
        prep();
        const sc = cam.D / F, mx1 = -dx * sc, my1 = (dy * sc) / Math.max(0.35, sp_);
        cam.tx += mx1 * cy_ - my1 * sy_; cam.ty += mx1 * sy_ + my1 * cy_;
        cam.tx = Math.max(-45000, Math.min(45000, cam.tx)); cam.ty = Math.max(-28000, Math.min(28000, cam.ty));
      }
      dirty = true; kick(); return;
    }
    const h = pick(mx, my);
    if ((h && h.label) !== (hover && hover.label)) { hover = h; canvas.style.cursor = h ? "pointer" : "grab"; dirty = true; kick(); }
  });
  canvas.addEventListener("pointerup", (e) => {
    const r = canvas.getBoundingClientRect(), was = drag; drag = null;
    endTouch(e);
    if (was && was.moved < (was.touch ? 12 : 6)) {
      const h = pick(e.clientX - r.left, e.clientY - r.top, was.touch);
      if (h && h.type === "landmark") self.lookAt(h.m.lat, h.m.lon, h.m.kind === "peak" ? 26000 : 7000);
      else if (h && opts.onPick) opts.onPick(h);
    }
  });
  canvas.addEventListener("pointerleave", () => { if (hover) { hover = null; dirty = true; kick(); } });
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); self.zoom(Math.exp(e.deltaY * 0.0012)); }, { passive: false });
  if ("ResizeObserver" in window) new ResizeObserver(resize).observe(canvas); else window.addEventListener("resize", resize);

  // ---------- public ----------
  self.setData = (d, cardList) => { data = d; cards = cardList || []; zoneSel = null; stopSel = null; buildScenery(); dirty = true; kick(); };
  self.setCards = (cardList) => { cards = cardList || []; dirty = true; kick(); };
  self.zoom = (f) => { anim = null; cam.D = Math.max(1400, Math.min(140000, cam.D * f)); dirty = true; kick(); };
  self.rotate = (a) => fly({ yaw: cam.yaw + a }, 500);
  self.tilt = (a) => fly({ pitch: Math.max(0.35, Math.min(1.35, cam.pitch + a)) }, 400);
  self.home = (ms) => { zoneSel = null; stopSel = null; fly(Object.assign({}, HOME), ms); };
  self.focusZone = (z, ms) => { zoneSel = z.key; stopSel = null; const [x, y] = toXY(z.lat, z.lon); fly({ tx: x, ty: y, D: 12500, pitch: 0.92, yaw: cam.yaw }, ms); };
  self.focusStop = (s, ms) => { stopSel = s; zoneSel = s.z || zoneSel; const [x, y] = toXY(s.lat, s.lon); fly({ tx: x, ty: y, D: 2600, pitch: 1.0, yaw: cam.yaw }, ms); };
  self.fit = (pts, ms) => {
    if (!pts.length) return self.home(ms);
    const xy = pts.map((p) => toXY(p.lat, p.lon));
    const x0 = Math.min(...xy.map((p) => p[0])), x1 = Math.max(...xy.map((p) => p[0])), y0 = Math.min(...xy.map((p) => p[1])), y1 = Math.max(...xy.map((p) => p[1]));
    const span = Math.max(x1 - x0, (y1 - y0) * 1.7, 2500);
    zoneSel = null; stopSel = null;
    fly({ tx: (x0 + x1) / 2, ty: (y0 + y1) / 2, D: Math.min(96000, span * 1.9), pitch: 0.9, yaw: -0.22 }, ms);
  };
  self.snapshot = () => { draw(); return canvas.toDataURL("image/png"); };
  // Satellite night brightness (0 = dark, 1 = brightest in the crop) at a point, or null before load.
  self.nightAt = (lat, lon) => satAt(lat, lon);
  self.redraw = () => { dirty = true; kick(); };
  // Where on the canvas the camera target sits (fractions), so overlays can cover one side.
  self.lookAt = (lat, lon, D, ms) => { const [x, y] = toXY(lat, lon); fly({ tx: x, ty: y, D: D || 7000, pitch: 0.78, yaw: cam.yaw }, ms); };
  self.setCenter = (fx, fy) => { if (fx === CX && fy === CY) return; CX = fx; CY = fy; dirty = true; kick(); };
  resize();
}
