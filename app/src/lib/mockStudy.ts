/** The Study half of the in-memory mock server: same routes and shapes as cyph/core/study_api.py, with canned "model"
 *  answers, so Study can be built and clicked through in a plain browser. */
import type { BoardItem, InkStroke, StudyCard } from "./types";

type Rec = Record<string, unknown>;
const NOT_HANDLED = Symbol("not handled");
export { NOT_HANDLED };

const SUBJECTS: [string, string][] = [["Math", "#d9984a"], ["Chemistry", "#2f9d8f"], ["Physics", "#5dff7a"], ["Amath", "#f08a73"], ["English", "#b79df0"], ["SS", "#e0c36a"], ["Geog", "#7fb4e8"]];
const plus = (day: string, n: number) => { const d = new Date(day + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const fail = (status: number, detail: string) => Promise.reject({ status, detail });
let n = 0;
const id = (p: string) => `${p}${(++n).toString(36)}${Math.random().toString(36).slice(2, 5)}`;

export function schedule(c: { interval: number; ease: number; reps: number; lapses: number }, grade: number, on: string) {
  let { interval, ease, reps, lapses } = c;
  if (grade === 0) { reps = 0; lapses++; ease = Math.max(1.3, ease - 0.2); interval = 0; }
  else if (grade === 1) { ease = Math.max(1.3, ease - 0.15); interval = reps === 0 ? 1 : Math.max(1, Math.round(interval * 1.2)); reps++; }
  else { interval = reps === 0 ? 1 : reps === 1 ? 3 : Math.max(1, Math.round(interval * ease)); ease = Math.min(3, ease + 0.05); reps++; }
  return { interval, ease: Math.round(ease * 1000) / 1000, reps, lapses, due: plus(on, interval) };
}

export function createMockStudy(changed: (what: string) => void, today: () => string) {
  const subjects = SUBJECTS.map(([name, color], i) => ({ id: `s${i}`, name, color }));
  const sub = (ref?: string | null) => (ref ? subjects.find((s) => s.id === ref || s.name.toLowerCase() === ref.toLowerCase()) : undefined);
  const topics: { id: string; subject_id: string; name: string }[] = [];
  const topicOf = (sid: string, name?: string | null) => {
    if (!name) return null;
    let t = topics.find((x) => x.subject_id === sid && x.name.toLowerCase() === name.toLowerCase());
    if (!t) topics.push((t = { id: id("t"), subject_id: sid, name }));
    return t.id;
  };
  const topicName = (tid: string | null) => topics.find((t) => t.id === tid)?.name ?? null;
  const chem = "s1";
  const notes: Rec[] = [];
  const addNote = (subject_id: string, title: string, body: string, topic: string | null, kind = "typed", source_id: string | null = null) => {
    const t = new Date().toISOString().slice(0, 19);
    const row = { id: id("n"), subject_id, source_id, topic_id: topicOf(subject_id, topic), title, body, kind, created: t, updated: t };
    notes.unshift(row);
    return row;
  };
  const view = (r: Rec) => ({ ...r, topic: topicName(r.topic_id as string | null) });
  const cards: StudyCard[] = [];
  const addCard = (subject_id: string, front: string, back: string, state: StudyCard["state"], topic: string | null, note_id: string | null = null): StudyCard => {
    const c = { id: id("c"), subject_id, topic_id: topicOf(subject_id, topic), topic, note_id, front, back, image_id: null, state, due: today(), interval: 0, ease: 2.5, reps: 0, lapses: 0 } as StudyCard;
    cards.unshift(c);
    return c;
  };
  const sources: Rec[] = [];
  const outputs: Record<string, Rec> = {};
  const attempts: Rec[] = [];
  const questions: Record<string, Rec> = {};
  const tests: Rec[] = [];
  const reviews: { card: string; grade: number; at: string }[] = [];
  const notebooks: Rec[] = [];
  const pages: Record<string, { id: string; notebook_id: string; idx: number; background: string; strokes: InkStroke[] }> = {};
  const boards: { id: string; subject_id: string | null; title: string; updated: string; items: BoardItem[] }[] = [];

  // a little seed so the pages are not empty
  const seed = addNote(chem, "The mole", "A **mole** is $6.02\\times10^{23}$ particles.\n\nMolar mass is the mass of one mole, in $\\text{g mol}^{-1}$.\n\nFor water: $\\ce{H2O}$, $M_r = 18$.", "Mole concept");
  addCard(chem, "How many particles in one mole?", "$6.02\\times10^{23}$ (Avogadro's constant)", "approved", "Mole concept", seed.id as string);
  addCard(chem, "Unit of molar mass?", "$\\text{g mol}^{-1}$", "approved", "Mole concept", seed.id as string);
  addCard(chem, "Mass of 2 mol of $\\ce{H2O}$?", "36 g", "draft", "Mole concept", seed.id as string);
  addCard("s0", "Gradient of $y=3x+2$?", "3", "approved", "Linear graphs");

  const due = (subject?: string) => cards.filter((c) => c.state === "approved" && c.due <= today() && (!subject || c.subject_id === subject));
  const stats = (subject?: string) => {
    const mine = cards.filter((c) => !subject || c.subject_id === subject);
    const rv = reviews.filter((r) => !subject || cards.find((c) => c.id === r.card)?.subject_id === subject);
    const days = new Set(rv.map((r) => r.at));
    let streak = 0;
    for (let d = today(); days.has(d); d = plus(d, -1)) streak++;
    return { approved: mine.filter((c) => c.state === "approved").length, drafts: mine.filter((c) => c.state === "draft").length, suspended: mine.filter((c) => c.state === "suspended").length, due: due(subject).length, reviewed_today: rv.filter((r) => r.at === today()).length, streak, retention: rv.length ? rv.filter((r) => r.grade >= 1).length / rv.length : null, reviews_30d: rv.length };
  };
  const weak = (subject?: string) =>
    topics.filter((t) => !subject || t.subject_id === subject).map((t) => {
      const a = attempts.filter((x) => x.topic_id === t.id);
      const mx = a.reduce((s, x) => s + (x.max as number), 0);
      return { id: t.id, subject_id: t.subject_id, name: t.name, weakness: mx ? 1 - a.reduce((s, x) => s + (x.score as number), 0) / mx : 0 };
    }).filter((w) => w.weakness > 0.15).sort((a, b) => b.weakness - a.weakness);

  const HINTS = ["So the question is asking you to find what happens to the quantity in the middle step. It tests the mole concept.", "Think about which formula links mass, moles and molar mass.", "First step: write $n = m / M_r$ and substitute the numbers you are given.", "Worked solution: $n = 36/18 = 2\\text{ mol}$. So the mass of 2 mol of water is $2\\times18 = 36\\text{ g}$."];

  return async function handle(method: string, route: string, qs: URLSearchParams, b: Rec): Promise<unknown> {
    if (!route.startsWith("/study/")) return NOT_HANDLED;
    let m: RegExpMatchArray | null;
    const subject = qs.get("subject") ?? undefined;
    const sid = subject ? sub(subject)?.id : undefined;
    if (subject && !sid) return fail(404, "no such subject");
    if (route === "/study/today") {
      const ups = (tests as { date: string }[]).filter((t) => t.date >= today());
      return { subjects: subjects.map((s) => ({ ...s, due: due(s.id).length, drafts: cards.filter((c) => c.subject_id === s.id && c.state === "draft").length, approved: cards.filter((c) => c.subject_id === s.id && c.state === "approved").length })), stats: stats(), weak: weak().slice(0, 5), tests: ups.slice(0, 5) };
    }
    if (route === "/study/subjects") {
      if (method === "POST") {
        const name = String(b.name ?? "").trim();
        if (!name) return fail(422, "a subject needs a name");
        if (sub(name)) return fail(409, "that subject exists");
        const s = { id: id("s"), name, color: String(b.color ?? "#d9984a") };
        subjects.push(s);
        changed("study");
        return s;
      }
      return { subjects };
    }
    if (route === "/study/sources" && method === "POST") {
      const s = sub(String(b.subject));
      if (!s) return fail(404, "no such subject");
      const mime = String(b.mime);
      if (!["application/pdf", "image/png", "image/jpeg", "image/webp", "text/plain"].includes(mime)) return fail(422, "PDF, picture (PNG/JPEG/WebP) or text only");
      const src = { id: id("r"), subject_id: s.id, title: String(b.title || "Untitled source"), kind: mime === "application/pdf" ? "pdf" : mime === "text/plain" ? "text" : "photo", file_id: id("f"), status: "processing", error: "", created: new Date().toISOString() };
      sources.unshift(src);
      setTimeout(() => {
        const body = b.text ? String(b.text) : "Cleaned up from your upload.\n\nKey idea: $v = u + at$.\n\nUnits: $\\text{m s}^{-1}$.";
        const note = addNote(s.id, src.title as string, body, "Imported", "import", src.id);
        outputs[src.id] = { clean_notes: body, summary: "A short summary of what you uploaded.", key_terms: JSON.stringify([{ term: "velocity", definition: "speed in a direction" }]), study_guide: "Read it once, then do three questions.", likely_questions: JSON.stringify(["State the equation for $v$."]) };
        src.status = "ready";
        void note;
        changed("study");
      }, 600);
      changed("study");
      return src;
    }
    if (route === "/study/sources") return { sources: sources.filter((s) => !sid || s.subject_id === sid) };
    if ((m = route.match(/^\/study\/sources\/([^/]+)$/))) {
      const i = sources.findIndex((s) => s.id === m![1]);
      if (i < 0) return fail(404, "no such source");
      if (method === "DELETE") { sources.splice(i, 1); changed("study"); return { ok: true }; }
      return { ...sources[i], outputs: outputs[m[1]] ?? {} };
    }
    if ((m = route.match(/^\/study\/sources\/([^/]+)\/retry$/))) { const s = sources.find((x) => x.id === m![1]); if (!s) return fail(404, "no such source"); s.status = "ready"; s.error = ""; changed("study"); return s; }
    if (route.startsWith("/study/files/")) return { mime: "application/pdf", data: "JVBERi0xLjQK" };
    if (route === "/study/notes" && method === "GET") {
      const text = (qs.get("q") ?? "").toLowerCase();
      return { notes: notes.filter((r) => (!sid || r.subject_id === sid) && (!text || `${r.title} ${r.body}`.toLowerCase().includes(text))).map((r) => ({ ...view(r), preview: String(r.body).slice(0, 160) })) };
    }
    if (route === "/study/notes" && method === "POST") {
      const s = sub(String(b.subject));
      if (!s) return fail(404, "no such subject");
      if (!String(b.body ?? "").trim()) return fail(422, "a note needs some text");
      const r = addNote(s.id, String(b.title || "Untitled"), String(b.body), (b.topic as string) || null);
      changed("study");
      return view(r);
    }
    if ((m = route.match(/^\/study\/notes\/([^/]+)$/))) {
      const i = notes.findIndex((r) => r.id === m![1]);
      if (i < 0) return fail(404, "no such note");
      if (method === "DELETE") { notes.splice(i, 1); changed("study"); return { ok: true }; }
      if (method === "PATCH") {
        const r = notes[i];
        if (b.title !== undefined) r.title = String(b.title);
        if (b.body !== undefined && String(b.body).trim()) r.body = String(b.body);
        if (b.topic !== undefined) r.topic_id = topicOf(r.subject_id as string, b.topic as string);
        r.updated = new Date().toISOString().slice(0, 19);
        changed("study");
      }
      return view(notes[i]);
    }
    if (route === "/study/search") {
      const w = (qs.get("q") ?? "").toLowerCase().split(/\W+/).filter((x) => x.length > 2);
      const hits = notes.filter((r) => !sid || r.subject_id === sid).map((r) => ({ note_id: r.id, title: r.title, text: String(r.body), score: w.filter((x) => String(r.body).toLowerCase().includes(x)).length / (w.length || 1) })).filter((h) => h.score > 0).sort((a, c) => c.score - a.score);
      return { results: hits.slice(0, 5) };
    }
    if (route === "/study/topics" && method === "GET") {
      return { topics: topics.filter((t) => !sid || t.subject_id === sid).map((t) => ({ ...t, cards: cards.filter((c) => c.topic_id === t.id && c.state === "approved").length, notes: notes.filter((r) => r.topic_id === t.id).length, weakness: weak().find((w) => w.id === t.id)?.weakness ?? 0 })), weak: weak(sid) };
    }
    if ((m = route.match(/^\/study\/topics\/([^/]+)$/))) {
      const t = topics.find((x) => x.id === m![1]);
      if (!t) return fail(404, "no such topic");
      const into = (b.merge_into as string) ?? topics.find((x) => x.id !== t.id && x.subject_id === t.subject_id && x.name.toLowerCase() === String(b.name ?? "").toLowerCase())?.id;
      if (into) {
        for (const l of [cards, notes, attempts] as { topic_id: unknown }[][]) l.forEach((r) => { if (r.topic_id === t.id) r.topic_id = into; });
        topics.splice(topics.indexOf(t), 1);
      } else if (String(b.name ?? "").trim()) t.name = String(b.name).trim();
      else return fail(422, "a topic needs a name");
      changed("study");
      return { ok: true };
    }
    if (route === "/study/cards" && method === "GET") return { cards: cards.filter((c) => (!sid || c.subject_id === sid) && (!qs.get("state") || c.state === qs.get("state"))).map((c) => ({ ...c, topic: topicName(c.topic_id) })) };
    if (route === "/study/cards/draft") {
      const note = notes.find((r) => r.id === b.note_id);
      const s = note ? (note.subject_id as string) : sub(String(b.subject))?.id;
      if (!s) return fail(422, "give a note or some text");
      const topic = note ? topicName(note.topic_id as string) : null;
      const out = [addCard(s, "What is the key idea of this note?", String(note?.title ?? b.text ?? "").slice(0, 120) || "See the note", "draft", topic, (note?.id as string) ?? null), addCard(s, "State one fact from this note.", String(note?.body ?? b.text ?? "").slice(0, 120), "draft", topic, (note?.id as string) ?? null)];
      changed("study");
      return { cards: out };
    }
    if (route === "/study/cards" && method === "POST") {
      const s = sub(String(b.subject));
      if (!s) return fail(404, "no such subject");
      if (!String(b.front ?? "").trim() || !String(b.back ?? "").trim()) return fail(422, "a card needs a front and a back");
      const c = addCard(s.id, String(b.front), String(b.back), (b.state as StudyCard["state"]) ?? "approved", (b.topic as string) ?? null);
      changed("study");
      return c;
    }
    if ((m = route.match(/^\/study\/cards\/([^/]+)(?:\/(approve|review))?$/))) {
      const i = cards.findIndex((c) => c.id === m![1]);
      if (i < 0) return fail(404, "no such card");
      if (method === "DELETE") { cards.splice(i, 1); changed("study"); return { ok: true }; }
      const c = cards[i];
      if (m[2] === "approve") c.state = "approved";
      else if (m[2] === "review") {
        const g = Number(b.grade);
        if (![0, 1, 2].includes(g)) return fail(422, "grade must be 0, 1 or 2");
        if (c.state !== "approved") return fail(409, "only approved cards can be reviewed");
        Object.assign(c, schedule(c, g, today()));
        reviews.push({ card: c.id, grade: g, at: today() });
      } else {
        for (const k of ["front", "back", "state"] as const) if (b[k] !== undefined && String(b[k]).trim()) (c as unknown as Rec)[k] = String(b[k]);
        if (b.topic !== undefined) { c.topic_id = topicOf(c.subject_id, b.topic as string); c.topic = (b.topic as string) || null; }
      }
      changed("study");
      return { ...c, topic: topicName(c.topic_id) };
    }
    if (route === "/study/due") return { cards: due(sid).slice(0, Number(qs.get("limit") ?? 30)).map((c) => ({ ...c, topic: topicName(c.topic_id) })) };
    if (route === "/study/stats") return stats(sid);
    if (route === "/study/tutor") {
      const step = Math.max(0, Math.min(3, Number(b.step ?? 0)));
      const hit = notes[0];
      return { step, stage: ["restate", "nudge", "first_step", "solution"][step], text: HINTS[step], last: step === 3, cites: hit ? [{ n: 1, note_id: hit.id, title: hit.title }] : [] };
    }
    if (route === "/study/practice") {
      const s = sub(String(b.subject));
      if (!s) return fail(404, "no such subject");
      const q = { id: id("q"), subject_id: s.id, kind: String(b.kind ?? "structured"), question: `(a) Define the mole. [1]\n(b) Calculate the mass of 2 mol of $\\ce{H2O}$. [3]`, marks: 4, topic: (b.topic as string) || weak(s.id)[0]?.name || "Mole concept" };
      questions[q.id] = q;
      return q;
    }
    if ((m = route.match(/^\/study\/practice\/([^/]+)\/answer$/))) {
      const q = questions[m[1]];
      if (!q) return fail(404, "that question has expired; ask for a new one");
      const answer = String(b.answer ?? "").trim();
      if (!answer) return fail(422, "write an answer first");
      const score = /36/.test(answer) ? 4 : /mole|mol/i.test(answer) ? 2 : 1;
      attempts.unshift({ id: id("a"), subject_id: q.subject_id, topic_id: topicOf(q.subject_id as string, q.topic as string), topic: q.topic, kind: q.kind, prompt: q.question, score, max: q.marks, feedback: score === 4 ? "Full marks." : "Check the units and the working.", at: new Date().toISOString() });
      delete questions[m[1]];
      changed("study");
      return { score, marks: q.marks, feedback: attempts[0].feedback, next: "Try one on molar mass." };
    }
    if (route === "/study/papers/mark") {
      const s = sub(String(b.subject));
      if (!s) return fail(404, "no such subject");
      const marks = Number(b.marks ?? 4);
      const score = Math.min(marks, String(b.answer ?? "").length > 3 ? marks - 1 : 0);
      attempts.unshift({ id: id("a"), subject_id: s.id, topic_id: topicOf(s.id, (b.topic as string) || "Papers"), topic: b.topic || "Papers", kind: "paper", prompt: b.question, score, max: marks, feedback: "Marked against " + (b.scheme ? "your scheme." : "the usual scheme."), at: new Date().toISOString() });
      changed("study");
      return { score, marks, feedback: attempts[0].feedback, next: "Redo the part you lost marks on." };
    }
    if (route === "/study/attempts") return { attempts: attempts.filter((a) => !sid || a.subject_id === sid) };
    if (route === "/study/tests" && method === "GET") return { tests };
    if (route === "/study/tests" && method === "POST") {
      const s = sub(String(b.subject));
      if (!s) return fail(404, "no such subject");
      const blocks = [7, 3, 1].map((k) => ({ id: id("e"), date: plus(String(b.date), -k) })).filter((x) => x.date >= today());
      const t = { id: id("x"), subject_id: s.id, title: String(b.title), date: String(b.date), plan_event_id: null, topics: (b.topics as string[]) ?? [], blocks };
      tests.push(t);
      changed("study");
      changed("plan");
      return { test: t, blocks };
    }
    if ((m = route.match(/^\/study\/tests\/([^/]+)$/)) && method === "DELETE") { const i = tests.findIndex((t) => t.id === m![1]); if (i < 0) return fail(404, "no such test"); tests.splice(i, 1); changed("study"); return { ok: true }; }
    const nbView = (nb: Rec) => ({ ...nb, pages: Object.values(pages).filter((p) => p.notebook_id === nb.id).sort((a, c) => a.idx - c.idx).map((p) => ({ id: p.id, idx: p.idx, background: p.background })) });
    const newPage = (nid: string, background = "lined") => {
      if (!["lined", "grid", "blank", "dots"].includes(background)) throw { status: 422, detail: "background: lined, grid, dots or blank" };
      const idx = Math.max(0, ...Object.values(pages).filter((p) => p.notebook_id === nid).map((p) => p.idx)) + 1;
      const pid = id("p");
      return (pages[pid] = { id: pid, notebook_id: nid, idx, background, strokes: [] });
    };
    if (route === "/study/notebooks" && method === "GET") return { notebooks: notebooks.map(nbView) };
    if (route === "/study/notebooks" && method === "POST") { const nb = { id: id("b"), subject_id: sub(b.subject as string)?.id ?? null, title: String(b.title || "Notebook"), updated: new Date().toISOString() }; notebooks.unshift(nb); newPage(nb.id); changed("study"); return nbView(nb); }
    if ((m = route.match(/^\/study\/notebooks\/([^/]+)$/))) { const nb = notebooks.find((x) => x.id === m![1]); if (!nb) return fail(404, "no such notebook"); if (method === "DELETE") { notebooks.splice(notebooks.indexOf(nb), 1); for (const k of Object.keys(pages)) if (pages[k].notebook_id === nb.id) delete pages[k]; changed("study"); return { ok: true }; } return nbView(nb); }
    if ((m = route.match(/^\/study\/notebooks\/([^/]+)\/pages$/))) { if (!notebooks.find((x) => x.id === m![1])) return fail(404, "no such notebook"); try { const p = newPage(m[1], b.background as string); changed("study"); return p; } catch (e) { return Promise.reject(e); } }
    if ((m = route.match(/^\/study\/notebooks\/([^/]+)\/pdf\/data$/))) return { mime: "application/pdf", data: "JVBERi0xLjQK" };
    if ((m = route.match(/^\/study\/pages\/([^/]+)$/))) {
      const p = pages[m[1]];
      if (!p) return fail(404, "no such page");
      if (method === "DELETE") { if (Object.values(pages).filter((x) => x.notebook_id === p.notebook_id).length <= 1) return fail(409, "a notebook keeps at least one page"); delete pages[m[1]]; changed("study"); return { ok: true }; }
      if (method === "PUT") { p.strokes = ((b.strokes as InkStroke[]) ?? []).filter((s) => s.pts?.length); if (b.background) p.background = String(b.background); changed("study"); }
      return p;
    }
    if (route === "/study/boards" && method === "GET") return { boards: boards.map(({ items, ...r }) => (void items, r)) };
    if (route === "/study/boards" && method === "POST") { const bd = { id: id("d"), subject_id: sub(b.subject as string)?.id ?? null, title: String(b.title || "Board"), updated: new Date().toISOString(), items: [] }; boards.unshift(bd); changed("study"); return bd; }
    if ((m = route.match(/^\/study\/boards\/([^/]+)$/))) { const i = boards.findIndex((x) => x.id === m![1]); if (i < 0) return fail(404, "no such board"); if (method === "DELETE") { boards.splice(i, 1); changed("study"); return { ok: true }; } return boards[i]; }
    if ((m = route.match(/^\/study\/boards\/([^/]+)\/items(?:\/([^/]+))?$/))) {
      const bd = boards.find((x) => x.id === m![1]);
      if (!bd) return fail(404, "no such board");
      if (method === "DELETE") { bd.items = bd.items.filter((x) => x.id !== m![2]); changed("study"); return { ok: true }; }
      if (!["stroke", "text", "sticky", "shape", "connector", "image", "pdf_page", "link"].includes(String(b.kind))) return fail(422, "bad item kind");
      const it = { id: (b.id as string) || id("i"), kind: b.kind, x: Number(b.x ?? 0), y: Number(b.y ?? 0), w: Number(b.w ?? 0), h: Number(b.h ?? 0), data: (b.data as Rec) ?? {} } as BoardItem;
      const i = bd.items.findIndex((x) => x.id === it.id);
      if (i >= 0) bd.items[i] = it; else bd.items.push(it);
      bd.updated = new Date().toISOString();
      changed("study");
      return it;
    }
    return fail(404, `mock server has no ${method} ${route}`);
  };
}
