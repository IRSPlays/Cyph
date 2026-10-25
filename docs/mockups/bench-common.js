/* Shared mock data + helpers for the Bench mockups. */
const NOW = new Date();
const pad = (n) => String(n).padStart(2, "0");
const toMin = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
const fmtMin = (m) => pad(Math.floor(m / 60)) + ":" + pad(m % 60);
const nowMin = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); };

/* A demo "now" so the screenshots always show a lively afternoon; ?live uses the real clock. */
const LIVE = location.search.includes("live");
const demoNow = () => (LIVE ? nowMin() : 12 * 60 + 14);

const TODAY = [
  { t: "Wake + run", s: "07:30", e: "08:30", k: "tl", done: true },
  { t: "Maths revision", s: "09:00", e: "11:00", k: "ne", done: true },
  { t: "Cyph planner build", s: "11:30", e: "13:15", k: "ne", now: true },
  { t: "NCDCC parade", s: "14:00", e: "17:00", k: "bl", fixed: true },
  { t: "Valorant w/ class", s: "19:00", e: "20:30", k: "co" },
  { t: "Wind down", s: "21:30", e: "22:00", k: "cr" },
];

function skyPhase(min) {
  const h = min / 60;
  if (h < 5.5 || h >= 20.5) return "night";
  if (h < 7.5) return "dawn";
  if (h < 17) return "day";
  if (h < 20.5) return "dusk";
  return "night";
}

function fitCyph(api, canvas, box, fill = 0.96, maxScale = 40) {
  const [w, h] = api.size();
  const fit = () => {
    const r = box.getBoundingClientRect();
    api.setScale(Math.max(3, Math.min(maxScale, Math.floor(Math.min(r.width / w, r.height / h) * fill))));
  };
  fit(); addEventListener("resize", fit); return fit;
}

function mountCyph(canvas, box, opts = {}) {
  const api = Cyph.create(canvas, { scale: 8 });
  fitCyph(api, canvas, box, opts.fill || 0.96, opts.max || 40);
  let timer;
  addEventListener("mousemove", (e) => {
    const r = canvas.getBoundingClientRect();
    const cx = r.left + r.width * 0.5, cy = r.top + r.height * 0.42;
    api.lookAt(Math.max(-1, Math.min(1, (e.clientX - cx) / (innerWidth * 0.35))), Math.max(-1, Math.min(1, (e.clientY - cy) / (innerHeight * 0.35))));
    clearTimeout(timer); timer = setTimeout(() => api.lookAt(null), 4500);
  });
  canvas.style.cursor = "pointer";
  canvas.addEventListener("click", () => api.poke());
  return api;
}

function buildKeypad(el, api, cols = 6, pick) {
  const names = pick || ["happy", "curious", "excited", "love", "sleepy", "smug", "think", "dance", "shy", "wave", "grumpy", "awoo"];
  el.style.setProperty("--c", cols);
  el.classList.add("kp");
  el.innerHTML = "";
  names.forEach((n) => {
    const b = document.createElement("button");
    b.dataset.m = n;
    b.innerHTML = `${Cyph.info[n].glyph.replace(/</g, "&lt;")}<small>${n}</small>`;
    b.onclick = () => api.setMood(n, 6);
    el.appendChild(b);
  });
  const prev = api.onMood;
  api.onMood = (m, why) => { prev && prev(m, why); el.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.m === m)); };
}

const TURNS = [
  [/plan|tomorrow|friday|schedule|today/i, "plan_list", "curious", "You've got the planner build now, then parade at 14:00. Maths is already done, nice. Want me to find a slot for the physics worksheet tonight?"],
  [/hello|hi |hey|morning/i, null, "wave", "Hey Cypher! Sunday is clear, Saturday is not. Do you want to talk through today first?"],
  [/sad|tired|stress/i, null, "sad", "Rough one? We can shrink the evening block. I'd rather you sleep than finish that worksheet."],
  [/love|good job|well done|thanks|thank/i, null, "love", "Okay, that actually made my ears do the thing. Back to work."],
  [/.*/, null, "smug", "I heard you. I'm choosing to be dramatic about it. Anyway, shall I put it on the plan?"],
];

function wireChat({ tape, input, send, api, mic, onTurn }) {
  const scroll = () => { tape.scrollTop = tape.scrollHeight; };
  const addUser = (text) => {
    const d = document.createElement("div"); d.className = "ln";
    d.innerHTML = `<div class="tag u"><b>HAZ</b></div><div class="us"></div>`; d.querySelector(".us").textContent = text; tape.appendChild(d); scroll();
  };
  const reply = (text, mood, tool) => {
    if (tool) { const e = document.createElement("div"); e.className = "ev"; e.textContent = `${tool}  ok`; tape.appendChild(e); }
    const d = document.createElement("div"); d.className = "ln";
    d.innerHTML = `<div class="tag"><b>CYPH</b>(${mood})</div><div class="cy"><span class="tx"></span><span class="caret"></span></div>`;
    tape.appendChild(d); scroll();
    api.setMood(mood, 7); api.speak(true);
    const tx = d.querySelector(".tx"); let i = 0;
    const iv = setInterval(() => { i += 2; tx.textContent = text.slice(0, i); scroll(); if (i >= text.length) { clearInterval(iv); d.querySelector(".caret").remove(); api.speak(false); onTurn && onTurn(); } }, 34);
  };
  const say = (text) => {
    text = (text || "").trim(); if (!text) return;
    addUser(text); input.value = ""; api.listen(false);
    const hit = TURNS.find((t) => t[0].test(text));
    setTimeout(() => reply(hit[3], hit[2], hit[1]), 500);
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") say(input.value); });
  input.addEventListener("input", () => { api.listen(input.value.length > 0); });
  input.addEventListener("blur", () => api.listen(false));
  send && (send.onclick = () => say(input.value));
  mic && (mic.onclick = () => { api.listen(true); setTimeout(() => say("hello, what's on today?"), 1400); });
  return { say, reply, addUser };
}

/* seed an opening exchange so screenshots are not empty */
function seedChat(chat) {
  chat.addUser("what's left today?");
  chat.reply(TURNS[0][3], "curious", "plan_list");
}

function tickClock(el, fmt) {
  const f = () => { const m = demoNow(); el.textContent = fmt ? fmt(m) : fmtMin(m); };
  f(); setInterval(f, 15000);
}

function idleLabel(el, api) {
  const prev = api.onMood;
  api.onMood = (m, why) => { prev && prev(m, why); if (el) el.innerHTML = `mood <b>${m}</b> · ${why}${api.calm() ? " · calm" : ""}`; };
}
