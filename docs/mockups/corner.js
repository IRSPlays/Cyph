/* Shared page shell for the Corner-style mockups: top bar + the small Cyph "buddy" bottom-right. */
const PAGES = [["HOME","home-c2-corner.html"],["CALENDAR","calendar.html"],["MEMORY","memory.html"],["STUDY","study.html"],["STUDY MODE","study-desktop.html"],["STUDY ON DEVICES","study-devices.html"],["ARM","arm.html"],["SETTINGS","settings.html"],["WIDGET","widget.html"],["PHONE","phone.html"]];
function shell(active, opts = {}) {
  const bar = document.getElementById("bar");
  bar.innerHTML = `<div class="logo">CYPH<em>/</em>${active}</div><div class="nav">${PAGES.map(([n, h]) => `<a class="${n === active ? "on" : ""}" href="${h}">${n}</a>`).join("")}</div>
   <div class="leds"><div><i></i>SERVER <b>SYNCED</b></div><div><i class="${opts.arm === "on" ? "" : "x"}"></i>ARM <b>${opts.arm === "on" ? "COM15" : "OFFLINE"}</b></div><div><i class="o"></i>VOICE <b>HOLD SPACE</b></div></div>`;
}
/* returns the Cyph api. say(text, mood) makes him type a comment into the bubble. */
function buddy(scale = 7) {
  const el = document.createElement("div"); el.className = "buddy";
  el.innerHTML = `<div class="bb"><div class="h"><i></i><i></i><i></i>CYPH <span id="bdm" style="margin-left:auto;color:var(--dim)"></span></div><div class="b" id="bdt"></div></div><canvas id="bdc"></canvas>`;
  document.body.appendChild(el);
  const api = Cyph.create(el.querySelector("canvas"), {scale});
  let mt; addEventListener("mousemove", (e) => { const r = el.querySelector("canvas").getBoundingClientRect(); api.lookAt(Math.max(-1, Math.min(1, (e.clientX - (r.left + r.width / 2)) / (innerWidth * .35))), Math.max(-1, Math.min(1, (e.clientY - (r.top + r.height * .4)) / (innerHeight * .35)))); clearTimeout(mt); mt = setTimeout(() => api.lookAt(null), 4500); });
  el.querySelector("canvas").addEventListener("click", () => api.poke());
  let iv;
  api.say = (text, mood = "curious", hold = 7000) => {
    const t = document.getElementById("bdt"); document.getElementById("bdm").textContent = mood.toUpperCase();
    api.setMood(mood, 7); api.speak(true); let i = 0; clearInterval(iv); el.classList.remove("quiet");
    iv = setInterval(() => { i += 2; t.innerHTML = text.slice(0, i) + (i < text.length ? '<span class="caret"></span>' : ""); if (i >= text.length) { clearInterval(iv); api.speak(false); if (hold) setTimeout(() => el.classList.add("quiet"), hold); } }, 26);
  };
  api.hush = () => el.classList.add("quiet");
  return api;
}
