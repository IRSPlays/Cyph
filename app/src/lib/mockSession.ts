/** The session engine half of the mock server: same routes and shapes as cyph/core/session.py, with canned marking, so
 *  the one-button Study flow can be clicked through in a plain browser. */
import type { MarkPoint, SessionStep, StepResult, Strength, StudyCard } from "./types";

type Rec = Record<string, unknown>;
interface Ctx {
  subjects: { id: string; name: string; color: string }[];
  topics: { id: string; subject_id: string; name: string; why?: string }[];
  topicOf: (sid: string, name?: string | null) => string | null;
  cards: StudyCard[];
  addCard: (sid: string, front: string, back: string, state: StudyCard["state"], topic: string | null) => StudyCard;
  addNote: (sid: string, title: string, body: string, topic: string | null, kind?: string, source?: string | null) => Rec;
  notes: Rec[];
  sources: Rec[];
  attempts: Rec[];
  tests: Rec[];
  reviews: { card: string; grade: number; at: string }[];
  review: (c: StudyCard, grade: number) => void;
  changed: (what: string) => void;
  today: () => string;
  id: (p: string) => string;
}
interface Q { id: string; subject_id: string; topic_id: string; source_id: string | null; origin: "source" | "cyph"; ref: string; text: string; marks: number;
  scheme: { point: string; final?: boolean }[]; answer: string; numeric: number | null; unit: string; key: string;
  quick: { ask: string; options: string[]; correct: number; final_label: string; explain: string }; figure: string | null; state: string }
interface Sess { id: string; day: string; kind: string; device: string; steps: (Rec & { type: string; result?: StepResult; hints?: string[] })[]; pos: number; state: string; started: number; headline: string; before: Record<string, Strength>; summary?: Rec }

const fail = (status: number, detail: string) => Promise.reject({ status, detail });
const WRITING = ["english", "ss"];
const num = (v: unknown) => { const m = String(v ?? "").replace(/,/g, "").match(/-?\d+(\.\d+)?/); return m ? Number(m[0]) : null; };
const close = (a: number | null, b: number | null) => a !== null && b !== null && Math.abs(a - b) <= Math.max(0.015 * Math.abs(b), 1e-9);

export function createMockSession(c: Ctx) {
  const qs: Q[] = [];
  const sessions: Sess[] = [];
  const lessons: Record<string, { example: { problem: string; steps: string[] }; turn: Q["quick"]; source: string }> = {};
  const rules: { topic_id: string; text: string }[] = [];
  const homework: Rec[] = [];
  let debrief: { day: string; turns: { who: "you" | "cyph"; text: string }[]; result: Rec; state: string } | null = null;
  const sub = (id: string) => c.subjects.find((s) => s.id === id) ?? { id, name: "", color: "#d9984a" };
  const subByName = (n: string) => c.subjects.find((s) => s.name.toLowerCase() === n.toLowerCase() || s.id === n);
  const topic = (id: string | null) => c.topics.find((t) => t.id === id);
  const writing = (sid: string) => WRITING.includes(sub(sid).name.toLowerCase());
  const addQ = (subject: string, topicName: string, q: Partial<Q> & { text: string }): Q => {
    const sid = subByName(subject)!.id;
    const row: Q = { id: c.id("q"), subject_id: sid, topic_id: c.topicOf(sid, topicName)!, source_id: null, origin: "source", ref: "", marks: 1, scheme: [], answer: "", numeric: null, unit: "", key: "printed",
      quick: { ask: q.text, options: ["Yes", "No"], correct: 0, final_label: "", explain: "" }, figure: null, state: "active", ...q };
    row.marks = row.scheme.length || row.marks;
    qs.push(row);
    return row;
  };

  // ---- seed: a little of everything, so every kind of step can be seen
  const mk = (subject: string, t: string, front: string, back: string) => c.addCard(subByName(subject)!.id, front, back, "approved", t);
  mk("Chemistry", "Limiting reagent", "What is the limiting reagent?", "The reactant that is used up first. It decides how much product forms.");
  mk("Physics", "Moments", "State the principle of moments.", "For a body in equilibrium, the total clockwise moment about a pivot equals the total anticlockwise moment about the same pivot.");
  mk("Physics", "Moments", "Define the moment of a force.", "Force × perpendicular distance from the pivot to the line of action of the force.");
  c.addNote(subByName("Chemistry")!.id, "Chem_WS_7 summary", "The **limiting reagent** is the reactant that is used up first.\n\nConvert every reactant to moles, divide by its number in the balanced equation, and the smallest is limiting.", "Limiting reagent");
  c.addNote(subByName("Amath")!.id, "Differentiation notes", "**Chain rule**: differentiate the outside, then multiply by the derivative of the inside.\n\n$\\frac{d}{dx}(2x+1)^3 = 3(2x+1)^2 \\times 2$", "Chain rule");
  c.sources.unshift({ id: "rmock", subject_id: subByName("Chemistry")!.id, title: "Chem_WS_7.pdf", kind: "pdf", file_id: null, status: "ready", error: "", created: c.today() + "T16:42:00" });
  addQ("Chemistry", "Limiting reagent", { ref: "Q4", source_id: "rmock", text: "4.8 g of magnesium reacts with 0.30 mol of hydrochloric acid. Which reagent is limiting, and what mass of hydrogen forms?",
    scheme: [{ point: "Moles of Mg = 0.20" }, { point: "HCl is limiting" }, { point: "Moles of $\\ce{H2}$ = 0.15" }, { point: "Mass of $\\ce{H2}$ = 0.30 g", final: true }], answer: "HCl is limiting; 0.30 g", numeric: 0.3, unit: "g",
    quick: { ask: "4.8 g of magnesium reacts with 0.30 mol of hydrochloric acid. Which runs out first?", options: ["Magnesium", "Hydrochloric acid", "Neither, they match exactly"], correct: 1, final_label: "Mass of hydrogen formed", explain: "0.20 mol Mg needs 0.40 mol HCl, and there is only 0.30. HCl is the boss now: 0.30 ÷ 2 = 0.15 mol." } });
  addQ("Chemistry", "Limiting reagent", { ref: "Q5", source_id: "rmock", text: "2.0 mol of hydrogen reacts with 0.5 mol of oxygen. How many moles of water form?",
    scheme: [{ point: "$\\ce{2H2 + O2 -> 2H2O}$" }, { point: "Oxygen is limiting" }, { point: "1.0 mol of water", final: true }], answer: "1.0 mol", numeric: 1, unit: "mol",
    quick: { ask: "2.0 mol of hydrogen reacts with 0.5 mol of oxygen. Which runs out first?", options: ["Hydrogen", "Oxygen"], correct: 1, final_label: "Moles of water", explain: "0.5 mol of oxygen needs only 1.0 mol of hydrogen." } });
  addQ("Amath", "Chain rule", { origin: "cyph", key: "cyph", text: "Differentiate $y = (3x+1)^4$ with respect to $x$.", scheme: [{ point: "Outside: $4(3x+1)^3$" }, { point: "Times the inside: $\\times 3$" }, { point: "$12(3x+1)^3$", final: true }], answer: "$12(3x+1)^3$",
    quick: { ask: "$y = (3x+1)^4$. What is $\\frac{dy}{dx}$?", options: ["$4(3x+1)^3$", "$12(3x+1)^3$", "$12(3x+1)^4$"], correct: 1, final_label: "", explain: "Outside gives $4(3x+1)^3$, the inside gives $\\times 3$." } });
  addQ("SS", "Source reliability", { ref: "Source B", text: "How reliable is Source B about the riots? Explain your answer.", scheme: [{ point: "Says who made the source and why" }, { point: "Checks it against another source" }, { point: "Quotes the source as evidence" }, { point: "Ends with a judgement: how far to trust it" }] });
  lessons[c.topicOf(subByName("Amath")!.id, "Chain rule")!] = { example: { problem: "$y = (2x+1)^3$", steps: ["Outside first: $3(2x+1)^2$", "Times the inside: $\\times 2$", "$\\frac{dy}{dx} = 6(2x+1)^2$"] },
    turn: { ask: "$y = (3x+1)^4$. What is $\\frac{dy}{dx}$?", options: ["$4(3x+1)^3$", "$12(3x+1)^3$", "$12(3x+1)^4$"], correct: 1, final_label: "", explain: "Outside gives $4(3x+1)^3$, the inside gives $\\times 3$." }, source: "Differentiation notes" };
  const seedAtt = (subject: string, t: string, score: number, max: number) => c.attempts.push({ id: c.id("a"), subject_id: subByName(subject)!.id, topic_id: c.topicOf(subByName(subject)!.id, t), kind: "full", prompt: "", answer: "", score, max, feedback: "Compared masses instead of moles.", at: c.today() + "T09:00:00", question_id: null, hints: 0 });
  seedAtt("Chemistry", "Limiting reagent", 1, 4); seedAtt("Chemistry", "Limiting reagent", 2, 4); seedAtt("Physics", "Moments", 3, 5); seedAtt("Chemistry", "Mole concept", 4, 4);
  const ph = c.topics.find((t) => t.name === "Moments"); if (ph) ph.why = "This is why the arm's shoulder servo strains when the elbow is stretched out.";

  // ---- strength
  const strength = (tid: string): { score: number | null; label: Strength } => {
    const att = c.attempts.filter((a) => a.topic_id === tid && (a.max as number) > 0).slice(-8);
    const cardIds = c.cards.filter((x) => x.topic_id === tid).map((x) => x.id);
    const rv = c.reviews.filter((r) => cardIds.includes(r.card)).slice(-20);
    const parts: [number, number][] = [];
    if (att.length) parts.push([0.65, att.reduce((s, a) => s + Math.min(1, (a.score as number) / (a.max as number)), 0) / att.length]);
    if (rv.length) parts.push([0.35, rv.filter((r) => r.grade >= 1).length / rv.length]);
    if (!parts.length) return { score: null, label: "new" };
    const s = parts.reduce((x, [w, v]) => x + w * v, 0) / parts.reduce((x, [w]) => x + w, 0);
    return { score: Math.round(s * 100), label: s < 0.45 ? "weak" : s < 0.72 ? "okay" : "solid" };
  };
  const topicRows = (sid?: string) => c.topics.filter((t) => !sid || t.subject_id === sid).map((t) => ({ ...t, subject: sub(t.subject_id).name, color: sub(t.subject_id).color,
    cards: c.cards.filter((x) => x.topic_id === t.id && x.state === "approved").length, questions: qs.filter((q) => q.topic_id === t.id && q.state === "active").length,
    notes: c.notes.filter((n) => n.topic_id === t.id).length, ...strength(t.id) }));
  const weakest = (n = 3) => topicRows().filter((t) => t.label !== "solid" && (t.cards || t.questions || t.notes)).sort((a, b) => (a.score ?? 50) - (b.score ?? 50)).slice(0, n);
  const dueCards = () => c.cards.filter((x) => x.state === "approved" && x.due <= c.today());
  const doneToday = () => sessions.some((s) => s.day === c.today() && s.state === "done");

  const plan = (device: string, kind = "", topicId?: string) => {
    const phone = device === "phone";
    if (!c.cards.length && !qs.length) return { kind: "empty", minutes: 0, steps: [], headline: "My brain is empty. Feed me one page and I will have a session ready before you put the phone down.", cards: [] as StudyCard[], topic: undefined };
    if (kind === "mini") { const cs = (dueCards().length ? dueCards() : c.cards).slice(0, 3); return { kind: "mini", minutes: 2, headline: "Three cards. That is the whole deal.", steps: [{ label: `${cs.length} cards`, sub: "then you are done", min: 2 }], cards: cs, topic: undefined }; }
    const cs = kind === "topic" ? c.cards.filter((x) => x.topic_id === topicId).slice(0, 6) : dueCards().slice(0, phone ? 10 : 14);
    const t = kind === "topic" ? topicRows().find((x) => x.id === topicId) : weakest(12).find((x) => x.questions || x.notes);
    const steps: { label: string; sub: string; min: number }[] = [];
    if (cs.length) steps.push({ label: cs.length > 3 ? "A few cards" : `${cs.length} cards`, sub: [...new Set(cs.map((x) => sub(x.subject_id).name))].slice(0, 3).join(" and "), min: Math.max(1, Math.round(cs.length * 22 / 60)) });
    if (t) steps.push({ label: (t.label === "weak" ? "Weak spot: " : "") + t.name, sub: writing(t.subject_id) ? "plan an answer" : `${phone ? 1 : 2} question${phone ? "" : "s"}, marked by Cyph`, min: phone ? 4 : 7 });
    if (!steps.length) return { kind: "idle", minutes: 0, steps: [], headline: "Nothing due and nothing weak. Go build something. I will guard the cards.", cards: cs, topic: undefined };
    return { kind: kind || "normal", minutes: steps.reduce((s, x) => s + x.min, 0), steps, cards: cs, topic: t,
      headline: t ? (t.label === "new" ? `${t.name} is new. I will show you one, then you do one.` : `${t.name} first. It tripped you twice this week, so today we trip it back.`) : "Cards only today. Quick lap, then you are free." };
  };

  const qview = (q: Q, device: string) => {
    const mode = writing(q.subject_id) ? "plan" : device === "phone" ? "quick" : "full";
    const src = c.sources.find((s) => s.id === q.source_id);
    return { id: q.id, text: q.text, marks: q.marks, origin: q.origin, ref: q.ref, source: (src?.title as string) ?? "", key: q.key, figure: q.figure, topic: topic(q.topic_id)?.name ?? "", topic_id: q.topic_id, subject: sub(q.subject_id).name, mode, why: topic(q.topic_id)?.why ?? "",
      ...(mode === "quick" ? { ask: q.quick.ask, options: q.quick.options, final_label: q.numeric !== null ? q.quick.final_label || "Final answer" : "", unit: q.unit } : {}),
      ...(mode === "plan" ? { plan: q.scheme.map((p) => p.point) } : {}) };
  };
  const stepView = (s: Sess, st: Sess["steps"][number]): SessionStep => {
    const base = { type: st.type as SessionStep["type"], result: st.result ?? null };
    if (st.type === "card") { const k = c.cards.find((x) => x.id === st.card); return k ? { ...base, card: { id: k.id, front: k.front, back: k.back, topic: k.topic ?? "", subject: sub(k.subject_id).name, why: topic(k.topic_id)?.why ?? "", image_id: null } } : { ...base, gone: true }; }
    if (st.type === "lesson") { const l = lessons[st.topic as string]; const t = topic(st.topic as string)!; return { ...base, topic: t.name, subject: sub(t.subject_id).name, why: t.why ?? "", lesson: { example: l.example, ask: l.turn.ask, options: l.turn.options, source: l.source } }; }
    if (st.type === "homework") return { ...base, homework: homework.find((h) => h.id === st.homework) as never };
    const q = qs.find((x) => x.id === st.question)!;
    const v = qview(q, s.device) as SessionStep["q"] & Rec;
    if (st.type === "paragraph") { v.mode = "paragraph"; v.checklist = q.scheme.map((p) => p.point); }
    return { ...base, q: v as SessionStep["q"], retry: false, hints: st.hints ?? [] };
  };
  const stepTopic = (st: Sess["steps"][number]) => st.type === "card" ? c.cards.find((x) => x.id === st.card)?.topic_id ?? null : st.type === "lesson" ? (st.topic as string) : qs.find((x) => x.id === st.question)?.topic_id ?? null;
  const finish = (s: Sess) => {
    const moved = Object.entries(s.before).filter(([tid]) => s.steps.some((x) => stepTopic(x) === tid && x.result)).map(([tid, was]) => ({ id: tid, name: topic(tid)?.name ?? "", was, now: strength(tid).label, score: strength(tid).score }));
    const up = moved.find((m) => m.was !== m.now && (m.now === "okay" || m.now === "solid"));
    s.summary = { minutes: Math.max(1, Math.round((Date.now() - s.started) / 60000)), moved, next: weakest(5).find((t) => !(t.id in s.before))?.name ?? "", headline: up ? `${up.name} just levelled up. Paw bump.` : "Done. That counted, even if it did not feel like it.",
      cards: s.steps.filter((x) => x.type === "card" && x.result).length, good: s.steps.filter((x) => x.type === "card" && (x.result?.grade ?? 0) >= 1).length };
    s.state = "done";
    c.changed("study");
  };
  const view = (s: Sess) => {
    if (s.state === "open" && s.pos >= s.steps.length) finish(s);
    return { id: s.id, kind: s.kind, device: s.device, state: s.state, pos: s.pos, total: s.steps.length, types: s.steps.map((x) => x.type), headline: s.headline,
      ...(s.state === "open" ? { step: stepView(s, s.steps[s.pos]) } : { summary: s.summary ?? null }) };
  };
  const markFull = (q: Q, answer: string): StepResult => {
    const mine = (answer.replace(/,/g, "").match(/-?\d+(\.\d+)?/g) ?? []).map(Number);
    const lines = answer.split("\n").filter((l) => l.trim());
    const pts: MarkPoint[] = q.scheme.map((p, i) => {
      const good = p.final && q.numeric !== null ? close(mine[mine.length - 1] ?? null, q.numeric) : i < Math.max(1, lines.length - 1);
      return { point: p.point, marks: 1, got: good, quote: lines[Math.min(i, lines.length - 1)] ?? "", why: good ? "" : p.final ? `The final answer should be ${q.answer}.` : "This step is missing from your working.", checked: !!p.final && q.numeric !== null };
    });
    const score = pts.filter((p) => p.got).length;
    return { mode: "full", score, max: pts.length, points: pts, unsure: false, headline: score === pts.length ? "Full marks. Clean." : "So close. Look at the line I marked.", slip: score === pts.length ? "" : "Used the wrong reagent for the last step." };
  };
  const record = (q: Q, r: StepResult, hints: number) => { const a = { id: c.id("a"), subject_id: q.subject_id, topic_id: q.topic_id, kind: r.mode, prompt: q.text, answer: "", score: r.score ?? 0, max: r.max ?? 1, feedback: r.slip ?? "", at: new Date().toISOString().slice(0, 19), question_id: q.id, hints }; c.attempts.push(a); return a.id; };

  return async function handle(method: string, route: string, qsp: URLSearchParams, b: Rec): Promise<unknown> {
    let m: RegExpMatchArray | null;
    if (route === "/study/now") {
      const device = qsp.get("device") ?? "desk";
      const open = sessions.find((s) => s.state === "open" && s.day === c.today());
      const p = plan(device);
      const t = (c.tests as { id: string; title: string; subject_id: string; date: string }[]).filter((x) => x.date >= c.today()).sort((a, z) => a.date.localeCompare(z.date))[0];
      const today = c.sources.filter((s) => String(s.created).slice(0, 10) === c.today());
      return { state: p.kind === "empty" ? "empty" : open ? "open" : doneToday() ? "done" : p.kind === "idle" ? "idle" : "ready", kind: p.kind, minutes: p.minutes, headline: p.headline, steps: p.steps, session: open?.id ?? null, session_device: open?.device ?? null,
        weak: weakest(3).map((w) => ({ id: w.id, name: w.name, subject: w.subject, label: w.label, score: w.score, test: "" })),
        test: t ? { id: t.id, title: t.title, subject: sub(t.subject_id).name, date: t.date, days: Math.round((Date.parse(t.date) - Date.parse(c.today())) / 86400000) } : null,
        debrief: { done: !!debrief && debrief.day === c.today(), open: debrief?.state === "open", result: debrief?.result ?? null },
        inbox: { today: today.length, reading: today.filter((s) => s.status === "processing").length, titles: today.slice(0, 3).map((s) => s.title) },
        homework: homework.filter((h) => !h.done).map((h) => ({ ...h, subject: sub(h.subject_id as string).name })) };
    }
    if (route === "/study/session" && method === "POST") {
      const device = b.device === "phone" ? "phone" : "desk";
      const p = plan(device, b.kind === "more" ? "" : String(b.kind ?? ""), b.topic as string | undefined);
      if (p.kind === "empty" || p.kind === "idle") return fail(409, p.headline);
      sessions.filter((s) => s.state === "open").forEach((s) => (s.state = "ended"));
      const steps: Sess["steps"] = p.cards.map((k) => ({ type: "card", card: k.id }));
      if (p.topic) {
        if (lessons[p.topic.id] && c.attempts.filter((a) => a.topic_id === p.topic!.id).length < 2) steps.push({ type: "lesson", topic: p.topic.id });
        qs.filter((q) => q.topic_id === p.topic!.id && q.state === "active").slice(0, device === "phone" ? 1 : 2).forEach((q) => steps.push({ type: "question", question: q.id }));
      }
      if (device !== "phone" && p.kind === "normal") { const para = qs.find((q) => writing(q.subject_id) && q.state === "active" && !c.attempts.some((a) => a.kind === "paragraph")); if (para) steps.push({ type: "paragraph", question: para.id }); }
      if (!steps.length) return fail(409, "I have nothing to ask you yet. Snap a worksheet and I will.");
      const before: Record<string, Strength> = {};
      steps.forEach((x) => { const t = stepTopic(x); if (t) before[t] = strength(t).label; });
      const s: Sess = { id: c.id("z"), day: c.today(), kind: p.kind, device, steps, pos: 0, state: "open", started: Date.now(), headline: p.headline, before };
      sessions.push(s);
      c.changed("study");
      return view(s);
    }
    if ((m = route.match(/^\/study\/session\/([^/]+)(?:\/(answer|next|end|hint))?$/))) {
      const s = sessions.find((x) => x.id === m![1]);
      if (!s) return fail(404, "no such session");
      if (!m[2]) return view(s);
      if (m[2] === "next") { if (s.state === "open") s.pos++; return view(s); }
      if (m[2] === "end") { if (s.state === "open") { if (s.steps.some((x) => x.result)) finish(s); else s.state = "ended"; c.changed("study"); } return view(s); }
      const st = s.steps[s.pos];
      if (s.state !== "open" || !st) return fail(409, "that session is over");
      if (m[2] === "hint") {
        const q = qs.find((x) => x.id === st.question);
        if (!q) return fail(409, "hints are for questions");
        const all = ["Find the amount of each thing first, then compare with the ratio in the equation.", q.quick.explain || "Think about which quantity runs out first.", "First step: " + (q.scheme[0]?.point ?? "write down what you know."), q.scheme.map((p) => p.point).join("\n") + `\n\nAnswer: ${q.answer}`];
        st.hints = st.hints ?? [];
        if (st.hints.length < 4) st.hints.push(all[st.hints.length]);
        return { hints: st.hints, last: st.hints.length >= 4 };
      }
      let r: StepResult;
      if (st.type === "card") { const k = c.cards.find((x) => x.id === st.card)!; const g = Number(b.grade) >= 1 ? 2 : 0; c.review(k, g); r = { grade: g }; }
      else if (st.type === "lesson") { const l = lessons[st.topic as string]; r = { correct: Number(b.option) === l.turn.correct, answer: l.turn.correct, explain: l.turn.explain }; }
      else if (st.type === "homework") { const h = homework.find((x) => x.id === st.homework); if (h) h.done = 1; r = { done: true }; }
      else {
        const q = qs.find((x) => x.id === st.question)!;
        const mode = st.type === "paragraph" ? "paragraph" : qview(q, s.device).mode;
        if (mode === "paragraph") { const ticks = ((b.ticks as boolean[]) ?? []).filter(Boolean).length; r = { mode, ticked: ticks, of: q.scheme.length, score: ticks, max: q.scheme.length }; }
        else if (mode === "plan") r = { mode, had: !!b.had, score: b.had ? 1 : 0, max: 1 };
        else if (mode === "quick") {
          const opt = Number(b.option ?? -1); const ok = opt === q.quick.correct;
          const pts: MarkPoint[] = [{ point: q.quick.options[q.quick.correct], got: ok, quote: q.quick.options[opt] ?? "", why: ok ? "" : q.quick.explain }];
          if (q.numeric !== null) { const good = close(num(b.value), q.numeric); pts.push({ point: q.quick.final_label || "Final answer", got: good, quote: String(b.value ?? ""), why: good ? "" : `It should be ${q.numeric} ${q.unit}. ${ok ? q.quick.explain : ""}`, checked: true }); }
          const score = pts.filter((p) => p.got).length;
          r = { mode, score, max: pts.length, points: pts, unsure: false, answer: q.quick.correct, headline: score === pts.length ? "Yes. Clean." : score ? "So close. Half of it is right." : "Not this time. Here is the slip." };
        } else {
          if (!String(b.answer ?? "").trim() && !((b.images as unknown[]) ?? []).length) return fail(422, "write an answer first");
          r = markFull(q, String(b.answer ?? "") || "0.30");
        }
        r.attempt = record(q, r, st.hints?.length ?? 0);
      }
      st.result = r;
      c.changed("study");
      return r;
    }
    if ((m = route.match(/^\/study\/attempts\/([^/]+)\/dispute$/))) {
      const a = c.attempts.find((x) => x.id === m![1]);
      if (!a) return fail(404, "nothing to look at again");
      a.score = a.max;
      c.changed("study");
      return { changed: true, score: a.max, max: a.max, points: [], headline: "I checked again. You are right, my bad. Mark given." };
    }
    if ((m = route.match(/^\/study\/questions\/([^/]+)\/flag$/))) {
      const q = qs.find((x) => x.id === m![1]);
      if (!q) return fail(404, "no such question");
      q.state = "binned";
      const t = topic(q.topic_id)?.name ?? "this topic";
      const rule = b.reason === "not_in_notes" ? `I only ask from your own worksheets for ${t} now.` : b.reason === "wrong" ? "Binned. I will double-check the answer key before I ask anything like it." : `Binned. I will keep questions on ${t} shorter and plainer.`;
      rules.push({ topic_id: q.topic_id, text: rule });
      c.changed("study");
      return { binned: q.id, rule, topic: t };
    }
    if ((m = route.match(/^\/study\/cards\/([^/]+)\/flag$/))) { const k = c.cards.find((x) => x.id === m![1]); if (!k) return fail(404, "no such card"); k.state = "suspended"; c.changed("study"); return { binned: k.id, rule: "Binned that card. It will not come back." }; }
    if (route === "/study/debrief") {
      const text = String(b.text ?? "").trim();
      if (!debrief || debrief.day !== c.today() || debrief.state === "done") { if (!text) return fail(422, "tell me one thing from today"); debrief = { day: c.today(), turns: [], result: {}, state: "open" }; }
      if (text) debrief.turns.push({ who: "you", text });
      const asked = debrief.turns.filter((t) => t.who === "cyph").length;
      const ask = b.finish || asked >= 1 ? "" : "Nice. One thing: why does that work, in your own words?";
      debrief.turns.push({ who: "cyph", text: ask ? "Counts! " + ask : "That is it. Go be free." });
      const topicsOut = [{ subject: "Chemistry", topic: "Titration", state: "solid", gap: "" }, ...(ask ? [] : [{ subject: "Physics", topic: "Moments", state: "shaky", gap: "perpendicular distance" }])];
      const result = { topics: topicsOut, cards: ask ? 0 : 2, done: !ask };
      if (!ask) { mk("Physics", "Moments", "Distance in a moment is measured how?", "Perpendicular from the pivot to the line of the force."); mk("Chemistry", "Titration", "Why add acid slowly near the end point?", "One drop can overshoot it."); debrief.state = "done"; }
      debrief.result = result;
      c.changed("study");
      return { id: "d1", turns: debrief.turns, ask, ...result };
    }
    if (route === "/study/inbox") {
      if (method === "POST") {
        const src = { id: c.id("r"), subject_id: "", title: String(b.title || "Untitled"), kind: b.mime === "application/pdf" ? "pdf" : b.mime === "text/plain" ? "text" : "photo", file_id: null, status: "processing", error: "", created: new Date().toISOString().slice(0, 19) };
        c.sources.unshift(src);
        c.changed("study");
        setTimeout(() => {
          const phys = subByName("Physics")!.id;
          Object.assign(src, { status: "ready", subject_id: phys });
          c.addNote(phys, src.title, "Notes read from " + src.title, "Moments", "import", src.id);
          (mk("Physics", "Moments", "What is the SI unit of moment?", "Newton metre, N m") as unknown as Rec).source_id = src.id;
          addQ("Physics", "Moments", { ref: "Q1", source_id: src.id, text: "A 20 N force acts 0.30 m from a pivot. Find its moment.", scheme: [{ point: "moment = F × d" }, { point: "6.0 N m", final: true }], answer: "6.0 N m", numeric: 6, unit: "N m",
            quick: { ask: "A 20 N force acts 0.30 m from a pivot. Which formula gives its moment?", options: ["F × d", "F ÷ d", "F + d"], correct: 0, final_label: "Moment", explain: "Moment is force times perpendicular distance." } });
          c.changed("study");
        }, 1500);
        return src;
      }
      return { items: c.sources.map((s) => { const n = c.notes.find((x) => x.source_id === s.id); return { id: s.id, title: s.title, kind: s.kind, status: s.status, error: s.error, created: s.created, subject: s.subject_id ? sub(s.subject_id as string).name : "", subject_id: s.subject_id,
        topic: topic((n?.topic_id as string) ?? null)?.name ?? (s.id === "rmock" ? "Limiting reagent" : ""), note_id: n?.id ?? null, cards: c.cards.filter((k) => (k as unknown as Rec).source_id === s.id).length, questions: qs.filter((q) => q.source_id === s.id && q.state === "active").length }; }) };
    }
    if ((m = route.match(/^\/study\/sources\/([^/]+)\/move$/))) {
      const s = c.sources.find((x) => x.id === m![1]); const to = subByName(String(b.subject ?? ""));
      if (!s || !to) return fail(404, "no such source or subject");
      s.subject_id = to.id;
      qs.filter((q) => q.source_id === s.id).forEach((q) => { q.topic_id = c.topicOf(to.id, topic(q.topic_id)?.name)!; q.subject_id = to.id; });
      c.changed("study");
      return s;
    }
    if (route === "/study/library") {
      const subs = c.subjects.map((s) => { const ts = topicRows(s.id).sort((a, z) => (a.score ?? 999) - (z.score ?? 999)); const sc = ts.filter((t) => t.score !== null).map((t) => t.score as number); const avg = sc.length ? Math.round(sc.reduce((x, y) => x + y, 0) / sc.length) : null;
        return { ...s, score: avg, label: avg === null ? "new" : avg < 45 ? "weak" : avg < 72 ? "okay" : "solid", topics: ts, due: dueCards().filter((k) => k.subject_id === s.id).length }; });
      return { subjects: subs.sort((a, z) => (a.score ?? 999) - (z.score ?? 999)) };
    }
    if ((m = route.match(/^\/study\/topics\/([^/]+)$/)) && method === "GET") {
      const t = topicRows().find((x) => x.id === m![1]);
      if (!t) return fail(404, "no such topic");
      const att = c.attempts.filter((a) => a.topic_id === t.id).slice(-4);
      return { ...t, why: t.why ?? "", slip: (att.find((a) => (a.score as number) < (a.max as number))?.feedback as string) ?? "", record: att.length ? `Wrong ${att.filter((a) => (a.score as number) < (a.max as number)).length} of the last ${att.length}.` : "",
        rules: rules.filter((r) => r.topic_id === t.id).map((r) => r.text), notes: c.notes.filter((n) => n.topic_id === t.id).map((n) => ({ id: n.id, title: n.title, updated: n.updated })),
        card_list: c.cards.filter((k) => k.topic_id === t.id && k.state !== "suspended").map((k) => ({ id: k.id, front: k.front, back: k.back, state: k.state, due: k.due })),
        question_list: qs.filter((q) => q.topic_id === t.id && q.state === "active").map((q) => ({ id: q.id, text: q.text, origin: q.origin, ref: q.ref, marks: q.marks })) };
    }
    if (route === "/study/homework" && method === "POST") { const s = subByName(String(b.subject ?? "")); if (!s) return fail(404, "no such subject"); const h = { id: c.id("h"), subject_id: s.id, title: String(b.title), due: String(b.due), done: 0 }; homework.push(h); c.changed("study"); return { ...h, subject: s.name }; }
    if ((m = route.match(/^\/study\/homework\/([^/]+)$/))) { const h = homework.find((x) => x.id === m![1]); if (!h) return fail(404, "no such homework"); h.done = b.done === false ? 0 : 1; c.changed("study"); return { ok: true }; }
    return undefined;
  };
}
