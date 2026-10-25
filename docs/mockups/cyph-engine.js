/* Cyph live face engine: a JS port of cyph/face.py render() + the idle life of body.py.
   Needs window.CYPH_RIG (from tools_bake_rig.py).
   const cy = Cyph.create(canvas, {scale: 12});  cy.setMood("happy"); cy.speak(true); cy.listen(true); cy.lookAt(nx, ny)/lookAt(null)
   cy.poke() = boop; cy.idle(true|false); cy.onMood = (mood, why) => {}; cy.onFrame = (t) => {} */
(function () {
  const R = window.CYPH_RIG, W = R.w, H = R.h;
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const lerp = (a, b, f) => { const A = hex(a), B = hex(b); return "#" + A.map((v, i) => Math.round(v + (B[i] - v) * f).toString(16).padStart(2, "0")).join(""); };
  const hash01 = (k) => { k = Math.imul(k, 2654435761) >>> 0; k = (k ^ (k >>> 15)) >>> 0; k = Math.imul(k, 2246822519) >>> 0; return (k & 0xffff) / 65535; };
  const decode = (s) => { const g = []; for (let y = 0; y < H; y++) { const row = []; for (let x = 0; x < W; x++) { const ch = s[y * W + x]; row.push(ch === "." ? null : R.palette[R.chars.indexOf(ch)]); } g.push(row); } return g; };
  const clone = (g) => g.map((r) => r.slice());
  const BASE = {}, BLINK = {};
  for (const m in R.moods) { BASE[m] = decode(R.moods[m].base); BLINK[m] = decode(R.moods[m].blink); }
  const rnd = (a, b) => a + Math.random() * (b - a);

  function applyMouth(grid, style) {
    const out = clone(grid), [bx0, by0, bx1, by1] = R.mouthBox, [mx, my] = R.mouthAt;
    for (let y = by0; y <= by1; y++) for (let x = bx0; x <= bx1; x++) out[y][x] = R.colors.fur;
    const name = R.mouthAlias[style] || style, shape = R.mouths[name] || R.mouths.smirk;
    shape.forEach((row, dy) => [...row].forEach((ch, dx) => {
      if (ch === "T") out[my + dy][mx + dx] = R.colors.tongue;
      else if (ch === "I") out[my + dy][mx + dx] = R.colors.mouthIn;
      else if (ch === "#" || ch === "L") out[my + dy][mx + dx] = R.colors.line;
    }));
    return out;
  }

  function create(canvas, opts = {}) {
    let scale = opts.scale || 10;
    const size = () => { canvas.width = W * scale; canvas.height = H * scale + scale * 2; };
    size();
    const ctx = canvas.getContext("2d");
    const S = {
      mood: "rest", speaking: false, listening: false, person: null, idleOn: true,
      t: 0, last: performance.now(), trans: { m: null, t: 0, seen: false },
      ear: { t: null, l: 0, r: 0 }, gaze: { t: null, x: 0, y: 0 }, lip: { t: null, v: 0, pk: 0.3, lo: 0.2 },
      nextIdle: 2, lastIdle: null, idleUntil: 0, convoUntil: 0, calmAfter: 180, lastTalk: 0,
      moodEnds: Infinity, level: 0,
    };
    const api = { state: S, onMood: null, onFrame: null };

    function setMood(m, why = "set", seconds) {
      if (!R.moods[m]) m = "rest";
      S.mood = m; S.moodEnds = seconds ? S.t + seconds : Infinity;
      api.onMood && api.onMood(m, why);
    }
    api.setMood = (m, seconds) => { S.convoUntil = S.t + 25; setMood(m, "caller", seconds); };
    api.speak = (on) => { S.speaking = on; if (on) S.convoUntil = S.t + 25; };
    api.listen = (on) => { S.listening = on; if (on) S.convoUntil = S.t + 25; };
    api.lookAt = (nx, ny) => { S.person = nx == null ? null : [nx, ny]; };
    api.idle = (on) => { S.idleOn = on; };
    api.poke = () => { S.convoUntil = S.t + 8; setMood("boop", "poke", 2.6); };
    api.calm = () => S.t - S.lastTalk > S.calmAfter;
    api.setScale = (s) => { if (s !== scale) { scale = s; size(); } };
    api.size = () => [W, H + 2];

    function idleTick() {
      if (!S.idleOn || S.mood !== "rest" || S.t < S.nextIdle || S.t < S.convoUntil) return;
      const calm = api.calm();
      const pool = (Math.random() < 0.2 && !calm) ? R.idle.lively : R.idle.passive;
      const opts = pool.filter((n) => n !== S.lastIdle && R.moods[n]);
      const name = opts[Math.floor(Math.random() * opts.length)];
      S.lastIdle = name;
      setMood(name, "idle", (R.idle.seconds[name] || 5) * rnd(0.6, 1.0));
    }

    function viseme(level, t, base) {
      const l = S.lip, dt = l.t == null ? 0.05 : Math.min(0.25, t - l.t); l.t = t;
      l.pk = Math.max(level, l.pk * Math.exp(-dt * 0.35), 0.12);
      l.lo = Math.min(level, l.lo + dt * 0.05);
      const span = Math.max(0.08, l.pk - l.lo), n = Math.max(0, Math.min(1, (level - l.lo) / span));
      l.v += (n - l.v) * (1 - Math.exp(-dt * (n > l.v ? 40 : 16)));
      const v = l.v, k = Math.floor(t * 9);
      if (v < 0.12) return base;
      if (v < 0.30) return "talk1";
      if (v < 0.50) return hash01(k) < 0.65 ? "talk2" : "o";
      if (v < 0.72) return hash01(k + 3) < 0.5 ? "tri" : "talk2";
      return hash01(k + 7) < 0.6 ? "talk3" : "open";
    }

    function flick(t, side) {
      const period = 3.1 + 1.3 * side, k = Math.floor((t + 1.7 * side) / period);
      if (hash01(k * 2 + side) > 0.55) return 0;
      const start = k * period - 1.7 * side + hash01(k + 91 * side) * (period - 0.6), u = (t - start) / 0.32;
      return u >= 0 && u <= 1 ? -16 * Math.sin(Math.PI * u) ** 2 : 0;
    }

    function earsAnim(g, mood, t) {
      let [la, ra] = R.moods[mood].earPose;
      if (S.listening) { la = ra = -10; }
      if (S.speaking) { const bob = 3 * Math.sin(t * 9); la += bob; ra += bob; }
      const pop = t - S.trans.t;
      if (pop >= 0 && pop < 0.5 && S.trans.seen) { const kick = -9 * Math.exp(-pop * 7) * Math.sin(Math.min(1, pop / 0.12) * Math.PI / 2); la += kick; ra += kick; }
      const e = S.ear;
      if (e.t == null || t < e.t) { e.l = la; e.r = ra; }
      else { const k = 1 - Math.exp(-Math.min(0.25, t - e.t) * 7); e.l += (la - e.l) * k; e.r += (ra - e.r) * k; }
      e.t = t;
      const angles = [-(e.l + flick(t, 0)), e.r + flick(t, 1)];
      for (const side of [0, 1]) {
        const ang = angles[side]; if (Math.abs(ang) < 1.5) continue;
        const th = ang * Math.PI / 180, [px, py] = R.ears.pivot[side], src = new Map();
        for (const [x, y] of R.ears.cells[side]) { src.set(x + "," + y, g[y][x]); g[y][x] = null; }
        const cs = Math.cos(-th), sn = Math.sin(-th), [x0, y0, x1, y1] = R.ears.box[side];
        for (let y = Math.max(0, y0 - 3); y < Math.min(H, y1 + 4); y++)
          for (let x = Math.max(0, x0 - 4); x < Math.min(W, x1 + 5); x++) {
            if (g[y][x] != null) continue;
            const rx = x - px, ry = y - py;
            const sx = Math.round(px + rx * cs - ry * sn), sy = Math.round(py + rx * sn + ry * cs);
            const col = src.get(sx + "," + sy); if (col != null) g[y][x] = col;
          }
      }
    }

    function wind(g, t) {
      const gust = 0.55 + 0.45 * Math.sin(t * 0.5);
      for (const [x, y, d] of R.furEdge) {
        if ((x * 7 + y * 3) % 3) continue;
        if (Math.sin(t * 1.4 + x * 0.9 + y * 0.55) * gust > 0.62 && g[y][x + d] == null) g[y][x + d] = g[y][x];
      }
      const lean = Math.sin(t * 0.6) > 0 ? 1 : -1;
      for (const [x, y] of R.hairTips) if (Math.sin(t * 1.2 + x * 0.8) * gust > 0.6 && g[y][x + lean] == null) g[y][x + lean] = g[y][x];
    }

    function gazeTarget(t) {
      const gz = S.gaze; let tx, ty;
      if (S.person) { tx = -1 * -S.person[0]; ty = S.person[1]; }   // mouse: screen-left/right already mirrored
      else { const k = Math.floor(t / 2.6), glance = hash01(k * 3 + 1) < 0.45; tx = glance ? (hash01(k * 5 + 2) - 0.5) * 2 : 0; ty = glance ? hash01(k * 7 + 3) - 0.3 : 0; }
      if (gz.t == null || t < gz.t) { gz.x = tx; gz.y = ty; }
      else { const a = 1 - Math.exp(-Math.min(0.25, t - gz.t) * 14); gz.x += (tx - gz.x) * a; gz.y += (ty - gz.y) * a; }
      gz.t = t; return [gz.x, gz.y];
    }

    function look(g, side, gx, gy) {
      const [lo, hi] = R.gaze.x[side], rest = R.gaze.rest[side];
      const px = Math.max(lo, Math.min(hi, rest + Math.round(gx * 2.2))), dy = gy > 0.35 ? 1 : 0;
      if (px === rest && dy === 0) return;
      for (const [x, y] of R.gaze.pupil[side]) if (g[y][x] === R.gaze.ink) g[y][x] = R.gaze.cream;
      for (let a = 0; a < 2; a++) for (let b = 0; b < 2; b++) g[23 + dy + b][px + a] = R.gaze.ink;
    }

    function frame() {
      const t = S.t, mood = S.mood, info = R.moods[mood];
      const tr = S.trans;
      if (tr.m !== mood || t < tr.t) { tr.m = mood; tr.t = t; }
      const since = t - tr.t, changing = since < 0.16 && since >= 0 && tr.seen; tr.seen = true;
      const blinkMood = R.blinkMoods.includes(mood);
      const blink = (blinkMood && (t % 4.3) < 0.12) || (!blinkMood && mood !== "sleepy" && (t % 6.1) < 0.1) || (changing && mood !== "rest" && mood !== "sleepy");
      let g = clone(blink ? BLINK[mood] : BASE[mood]);
      if (!blink) {
        const [gx, gy] = gazeTarget(t);
        if (info.gazeL) look(g, 0, gx, gy);
        if (info.gazeR) look(g, 1, gx, gy);
      } else gazeTarget(t);
      if (S.speaking) {
        const lvl = Math.max(0, Math.min(1, 0.5 + 0.28 * Math.sin(t * 7.1) + 0.22 * Math.sin(t * 13.3 + 1) + 0.12 * Math.sin(t * 3.1)));
        g = applyMouth(g, viseme(lvl, t, info.mouth));
      } else S.lip.t = null;
      let glow = R.neonColor;
      if (S.speaking) glow = lerp(R.neonColor, "#e8ffe0", 0.5 + 0.5 * Math.sin(t * 9));
      else if (S.listening) glow = lerp(R.neonColor, R.jacket, 0.5 + 0.5 * Math.sin(t * 3));
      for (const [x, y] of R.neon) g[y][x] = glow;
      if (S.listening) for (const row of g) for (let x = 0; x < W; x++) if (row[x] === R.earCoral) row[x] = R.earCoralHot;
      earsAnim(g, mood, t);
      wind(g, t);
      return g;
    }

    function draw(g, t) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const bob = Math.sin(t * 1.7) > 0.45 ? 0 : 1;       // a slow breath: the head rises one face-pixel
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) { const c = g[y][x]; if (c) { ctx.fillStyle = c; ctx.fillRect(x * scale, (y + bob) * scale + scale, scale, scale); } }
    }

    let running = true;
    function loop(now) {
      if (!running) return;
      const dt = Math.min(0.1, (now - S.last) / 1000); S.last = now; S.t += dt;
      if (S.speaking || S.listening) S.lastTalk = S.t;
      if (S.mood !== "rest" && S.t > S.moodEnds) { S.moodEnds = Infinity; const was = S.mood; setMood("rest", "end"); S.nextIdle = S.t + (api.calm() ? rnd(6, 16) : rnd(0.8, 3.0)); }
      idleTick();
      draw(frame(), S.t);
      api.onFrame && api.onFrame(S.t);
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
    api.stop = () => { running = false; };
    return api;
  }
  window.Cyph = { create, moods: Object.keys(R.moods), info: R.moods };
})();
