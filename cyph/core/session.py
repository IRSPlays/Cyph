"""The study engine behind the one START button.

It decides what a session holds (cards that are due, questions on the weakest topic, one second try), runs it one step at
a time, marks answers, and keeps what it learned. Questions come from his own worksheets first; anything Cyph wrote is
labelled and never shown for a topic he flagged. Marks are tied to scheme points, the final number is checked in code,
and "not sure" is an answer. Everything here is plain data in the study database, so a session started on the phone
continues on the PC."""
from __future__ import annotations

import io
import json
import re
from datetime import date, timedelta

from .study import SYSTEM, StudyError, _int, new_id, now_iso, parse_json, today

WRITING = {"english", "ss", "social studies"}          # skills, not facts: planned and self-checked, never AI-marked
DEVICES = {"phone": {"minutes": 8, "cards": 10, "questions": 1}, "desk": {"minutes": 15, "cards": 14, "questions": 2}}
CARD_SECS = 22
FLAGS = {"not_in_notes": "I only ask from your own worksheets for this topic now.",
         "wrong": "Binned. I will double-check the answer key before I ask anything like it.",
         "confusing": "Binned. I will keep questions on this topic shorter and plainer."}


def _j(v, default):
    try:
        return json.loads(v) if v else default
    except ValueError:
        return default


def _num(v):
    """A plain number out of a model field or his typing ("0.30 g", "3/4", "1.2e3"); None if there is none."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v or "").replace(",", "").replace("−", "-").strip()
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\s*[^\d]*", s)
    if m and float(m.group(2)):
        return float(m.group(1)) / float(m.group(2))
    m = re.search(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", s)
    return float(m.group(0)) if m else None


def close(a, b) -> bool:
    """Equal as a student would write it: within 1.5%, so 0.3 and 0.30 and 0.298 all match 0.30."""
    if a is None or b is None:
        return False
    return abs(a - b) <= max(0.015 * abs(b), 1e-9)


def scheme_of(raw, marks=1) -> list[dict]:
    """Scheme points as a clean list, whatever shape they arrived in (a list of points, or a line of text)."""
    pts = []
    if isinstance(raw, str):
        got = parse_json(raw) if raw.strip().startswith("[") else None
        raw = got if isinstance(got, list) else [x.strip() for x in re.split(r"[;\n]+", raw) if x.strip()]
    for p in raw or []:
        if isinstance(p, dict) and str(p.get("point") or "").strip():
            pts.append({"point": str(p["point"]).strip()[:300], "marks": max(1, min(10, _int(p.get("marks"), 1))), "final": bool(p.get("final"))})
        elif isinstance(p, str) and p.strip():
            pts.append({"point": p.strip()[:300], "marks": 1, "final": False})
    return pts[:12]


def quick_of(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    opts = [str(o).strip()[:120] for o in (raw.get("options") or []) if str(o).strip()][:4]
    ci = _int(raw.get("correct"), -1)
    if len(opts) < 2 or not 0 <= ci < len(opts):
        return None
    return {"ask": str(raw.get("ask") or "").strip()[:600], "options": opts, "correct": ci,
            "final_label": str(raw.get("final_label") or "").strip()[:60], "explain": str(raw.get("explain") or "").strip()[:300]}


def page_png(mime: str, data: bytes, page: int) -> bytes | None:
    """One page of a PDF as a picture, so a question keeps its real diagram. None when it cannot be drawn."""
    if mime != "application/pdf":
        return None
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(data)
        if not 1 <= page <= len(doc):
            return None
        img = doc[page - 1].render(scale=1.6).to_pil()
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return None


def save_questions(st, src: dict, raw: list, topic, meta: dict | None = None, data: bytes | None = None, origin="source") -> list[str]:
    """Store questions read from a source (or written by Cyph). Returns the new ids."""
    out, pages = [], {}
    for q in raw[:20]:
        if not isinstance(q, dict) or not str(q.get("text") or "").strip():
            continue
        scheme = scheme_of(q.get("scheme"))
        marks = sum(p["marks"] for p in scheme) or max(1, min(100, _int(q.get("marks"), 1)))
        fig = None
        if q.get("figure") and meta and data:
            if meta["mime"].startswith("image/"):
                fig = src["file_id"]                    # a photo is its own figure
            else:
                pg = max(1, _int(q.get("page"), 1))
                if pg not in pages:
                    png = page_png(meta["mime"], data, pg)
                    pages[pg] = st.put_file(f"{src['title']} p{pg}.png", "image/png", png) if png else None
                fig = pages[pg]
        qid = new_id("q")
        quick = quick_of(q.get("quick"))
        st.run("INSERT INTO questions(id,subject_id,topic_id,source_id,origin,ref,text,marks,scheme,answer,numeric,unit,key,quick,figure,state,created) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (qid, src["subject_id"], st.topic(src["subject_id"], q.get("topic") or topic or "General"), src.get("id"), origin,
                str(q.get("ref") or "")[:20], str(q["text"]).strip()[:4000], marks, json.dumps(scheme), str(q.get("answer") or "")[:2000],
                _num(q.get("numeric")) if q.get("numeric") is not None else None, str(q.get("unit") or "")[:20],
                "printed" if origin == "source" and q.get("key") == "printed" else "cyph", json.dumps(quick) if quick else "", fig,
                "active", now_iso()))
        out.append(qid)
    return out


class Engine:
    def __init__(self, svc, projects=None):
        self.svc, self.st, self.projects = svc, svc.store, projects or (lambda: "")

    @property
    def ai(self):
        return self.svc.ai

    # ---- small helpers ---------------------------------------------------------------------------
    def kv(self, k, v=None):
        if v is None:
            r = self.st.one("SELECT v FROM kv WHERE k=?", (k,))
            return r["v"] if r else None
        self.st.run("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, str(v)))
        return v

    def _subj(self, sid):
        return self.st.one("SELECT * FROM subjects WHERE id=?", (sid,)) or {"id": sid, "name": "", "color": "#d9984a"}

    def _topic(self, tid):
        return self.st.one("SELECT * FROM topics WHERE id=?", (tid,)) if tid else None

    def _find_subject(self, name):
        """A subject from a name the model gave; None instead of an error when it made one up."""
        try:
            return self.st.subject(str(name or "").strip()) if str(name or "").strip() else None
        except StudyError:
            return None

    def writing(self, subject_id) -> bool:
        return self._subj(subject_id)["name"].strip().lower() in WRITING

    # ---- strength: one number per topic, shown as a word --------------------------------------------
    def strength(self, tid) -> dict:
        att = self.st.q("SELECT score, max, hints, kind FROM attempts WHERE topic_id=? AND max>0 ORDER BY at DESC LIMIT 8", (tid,))
        rv = self.st.q("SELECT r.grade FROM reviews r JOIN cards c ON c.id=r.card_id WHERE c.topic_id=? ORDER BY r.id DESC LIMIT 20", (tid,))
        wt = lambda a: 4.0 if a["kind"] == "teacher" else 1.0      # noqa: E731  a real teacher's mark outweighs Cyph's
        qs = sum(wt(a) * min(1.0, a["score"] / a["max"]) * (0.85 if a["hints"] else 1.0) for a in att) / sum(wt(a) for a in att) if att else None
        cs = sum(1 for r in rv if r["grade"] >= 1) / len(rv) if rv else None
        have = [(w, v) for w, v in ((0.65, qs), (0.35, cs)) if v is not None]
        if not have:
            return {"score": None, "label": "new"}
        s = sum(w * v for w, v in have) / sum(w for w, _ in have)
        return {"score": round(s * 100), "label": "weak" if s < 0.45 else "okay" if s < 0.72 else "solid"}

    def topics(self, subject_id=None) -> list[dict]:
        rows = self.st.q("SELECT t.*, s.name AS subject, s.color AS color, "
                         "(SELECT COUNT(*) FROM cards c WHERE c.topic_id=t.id AND c.state='approved') AS cards, "
                         "(SELECT COUNT(*) FROM questions q WHERE q.topic_id=t.id AND q.state='active') AS questions, "
                         "(SELECT COUNT(*) FROM notes n WHERE n.topic_id=t.id) AS notes FROM topics t JOIN subjects s ON s.id=t.subject_id"
                         + (" WHERE t.subject_id=?" if subject_id else "") + " ORDER BY t.name", (subject_id,) if subject_id else ())
        for r in rows:
            r.update(self.strength(r["id"]))
        return rows

    def weakest(self, n=3, subject_id=None) -> list[dict]:
        """Topics to work on, worst first. A test coming up pushes its topics to the front."""
        boost = self._test_topics()
        rows = [t for t in self.topics(subject_id) if t["label"] != "solid" and (t["cards"] or t["questions"] or t["notes"])]
        def rank(t):
            s = 50 if t["score"] is None else t["score"]
            return s - (60 if t["id"] in boost else 0)
        rows.sort(key=rank)
        for t in rows:
            t["test"] = boost.get(t["id"], "")
        return rows[:n]

    def _tests(self, within=7):
        lo, hi = today().isoformat(), (today() + timedelta(days=within)).isoformat()
        return [t for t in self.st.tests() if lo <= t["date"] <= hi]

    def _test_topics(self) -> dict:
        out = {}
        for t in self._tests():
            for name in t.get("topics") or []:
                row = self.st.one("SELECT id FROM topics WHERE subject_id=? AND lower(name)=lower(?)", (t["subject_id"], str(name)))
                if row:
                    out[row["id"]] = t["date"]
        return out

    # ---- what a session would hold ------------------------------------------------------------------
    def _last_active(self):
        a = self.st.one("SELECT MAX(at) m FROM reviews")["m"]
        b = self.st.one("SELECT MAX(at) m FROM attempts")["m"]
        got = max([x[:10] for x in (a, b) if x] or [""])
        return date.fromisoformat(got) if got else None

    def done_today(self) -> bool:
        day = today().isoformat()
        return bool(self.st.one("SELECT 1 FROM sessions WHERE day=? AND state='done'", (day,)))

    def _due_cards(self, subject_id=None, limit=200):
        """Due cards, weakest topics first, so a short session spends its time where it matters."""
        rows = self.st.due(subject_id, 2000)
        memo = {}
        def key(c):
            tid = c["topic_id"]
            if tid not in memo:
                s = self.strength(tid)["score"] if tid else None
                memo[tid] = 50 if s is None else s
            return (memo[tid], c["due"])
        rows.sort(key=key)
        return rows[:limit]

    def _has_material(self, t) -> bool:
        return bool(t["questions"] or t["notes"])

    def _pick_topic(self, skip=(), subject_id=None):
        for t in self.weakest(12, subject_id):
            if t["id"] not in skip and self._has_material(t):
                return t
        return None

    def _retry(self, skip_topic=None):
        """One question he got wrong a couple of days ago (or argued about) and has not got right since."""
        cut = (today() - timedelta(days=2)).isoformat()
        for a in self.st.q("SELECT * FROM attempts WHERE question_id IS NOT NULL AND (score<max OR disputed=1) AND substr(at,1,10)<=? ORDER BY at DESC LIMIT 30", (cut,)):
            q = self.st.one("SELECT * FROM questions WHERE id=? AND state='active'", (a["question_id"],))
            if not q or q["topic_id"] == skip_topic:
                continue
            later = self.st.one("SELECT 1 FROM attempts WHERE question_id=? AND at>? AND score>=max AND disputed=0", (q["id"], a["at"]))
            if not later:
                return q
        return None

    def _homework(self):
        lim = (today() + timedelta(days=1)).isoformat()
        return self.st.one("SELECT * FROM homework WHERE done=0 AND due<=? ORDER BY due LIMIT 1", (lim,))

    def plan(self, device="desk", kind="", topic_id=None, skip_topics=()) -> dict:
        """What START would run right now. Pure reading: nothing is created or asked of the model."""
        dev = DEVICES["phone" if device == "phone" else "desk"]
        has = self.st.one("SELECT (SELECT COUNT(*) FROM cards WHERE state='approved') c, (SELECT COUNT(*) FROM questions WHERE state='active') q, (SELECT COUNT(*) FROM notes) n")
        if not (has["c"] or has["q"] or has["n"]):
            return {"kind": "empty", "minutes": 0, "steps": [], "headline": "My brain is empty. Feed me one page and I will have a session ready before you put the phone down."}
        steps, out = [], {"kind": "normal" if kind in ("", "more") else kind, "cards": [], "topic": None, "retry": None, "homework": None, "paragraph": None, "subject": None}
        if kind == "mini":
            cards = self._due_cards(limit=3) or self.st.q("SELECT c.*, t.name AS topic FROM cards c LEFT JOIN topics t ON t.id=c.topic_id WHERE c.state='approved' ORDER BY c.due LIMIT 3")
            out["cards"] = [c["id"] for c in cards]
            return {**out, "minutes": 2, "headline": "Three cards. That is the whole deal.", "steps": [{"label": f"{len(cards)} cards", "sub": "then you are done", "min": 2}]}
        if kind == "topic" and topic_id:
            t = next((x for x in self.topics() if x["id"] == topic_id), None)
            if not t:
                raise StudyError("no such topic", 404)
            cards = self.st.q("SELECT id FROM cards WHERE topic_id=? AND state='approved' ORDER BY due LIMIT 6", (topic_id,))
            out.update(cards=[c["id"] for c in cards], topic=t, kind="topic")
            steps = ([{"label": f"{len(cards)} cards", "sub": t["name"], "min": max(1, len(cards) * CARD_SECS // 60)}] if cards else []) + \
                    ([{"label": t["name"], "sub": "questions, marked by Cyph", "min": 6}] if self._has_material(t) else [])
            return {**out, "minutes": sum(s["min"] for s in steps) or 1, "steps": steps, "headline": f"{t['name']}, and only {t['name']}. Let us go."}
        tests = [t for t in self._tests(1)]
        last = self._last_active()
        hw = self._homework()
        if hw and not kind:
            out.update(kind="homework", homework=hw)
            s = self._subj(hw["subject_id"])["name"]
            return {**out, "minutes": 15, "headline": f"Homework first: {hw['title']}. Doing it counts as today's session.",
                    "steps": [{"label": f"{s}: {hw['title']}", "sub": "due " + hw["due"], "min": 15}]}
        if tests and not kind:
            t = tests[0]
            sid = t["subject_id"]
            name = self._subj(sid)["name"]
            cards = self._due_cards(sid, 15)
            tt = [x for x in self.topics(sid) if x["id"] in self._test_topics() or not t.get("topics")]
            tt.sort(key=lambda x: 50 if x["score"] is None else x["score"])
            ready = [x["name"] for x in tt if x["label"] == "solid"]
            notyet = [x["name"] for x in tt if x["label"] in ("weak", "new")]
            line = f"{name} only. Honest report: " + (", ".join(notyet[:2]) + (" is" if len(notyet[:2]) == 1 else " are") + " not ready" if notyet else "you look ready") + \
                   (", " + ready[0] + " is solid." if ready else ".")
            out.update(kind="prep", cards=[c["id"] for c in cards], subject=sid, prep_topics=[x["id"] for x in tt[:4] if self._has_material(x)], test=t)
            steps = [{"label": x["name"], "sub": "questions until it sticks", "min": 6} for x in tt[:3] if self._has_material(x)]
            if cards:
                steps.append({"label": f"{name} cards", "sub": f"{len(cards)} to go", "min": max(1, len(cards) * CARD_SECS // 60)})
            return {**out, "minutes": 25, "headline": line, "steps": steps or [{"label": name, "sub": "cards", "min": 5}]}
        if last and (today() - last).days >= 5 and not kind:
            cards = sorted(self.st.due(None, 400), key=lambda c: -c["ease"])[:8]
            if cards:
                out.update(kind="comeback", cards=[c["id"] for c in cards])
                return {**out, "minutes": 6, "headline": "You vanished. I napped. No lecture. Six easy minutes to warm up.",
                        "steps": [{"label": "A few cards you nearly know", "sub": "the rest can wait", "min": 6}]}
        cards = self._due_cards(limit=dev["cards"])
        out["cards"] = [c["id"] for c in cards]
        if cards:
            names = sorted({self._subj(c["subject_id"])["name"] for c in cards})
            steps.append({"label": "A few cards" if len(cards) > 3 else f"{len(cards)} cards", "sub": " and ".join(names[:3]), "min": max(1, len(cards) * CARD_SECS // 60)})
        topic = self._pick_topic(skip_topics)
        if topic:
            out["topic"] = topic
            n = dev["questions"]
            steps.append({"label": ("Weak spot: " if topic["label"] == "weak" else "") + topic["name"],
                          "sub": ("plan an answer" if self.writing(topic["subject_id"]) else f"{n} question{'s' if n > 1 else ''}, marked by Cyph"), "min": 4 if device == "phone" else 7})
        if device != "phone":
            para = self._paragraph_due()
            if para:
                out["paragraph"] = para["id"]
                steps.append({"label": f"Weekly paragraph: {self._subj(para['subject_id'])['name']}", "sub": "one real question, you check it", "min": 10})
            else:
                rq = self._retry(topic["id"] if topic else None)
                if rq:
                    out["retry"] = rq["id"]
                    tn = (self._topic(rq["topic_id"]) or {}).get("name", "")
                    steps.append({"label": "Second try: " + tn, "sub": "a question that got away", "min": 4})
        if not steps:
            return {**out, "kind": "idle", "minutes": 0, "steps": [], "headline": "Nothing due and nothing weak. Go build something. I will guard the cards."}
        return {**out, "minutes": sum(s["min"] for s in steps), "steps": steps, "headline": self._headline(topic, cards)}

    def _headline(self, topic, cards) -> str:
        if not topic:
            return "Cards only today. Quick lap, then you are free."
        name = topic["name"]
        if topic.get("test"):
            d = (date.fromisoformat(topic["test"]) - today()).days
            when = "today" if d <= 0 else "tomorrow" if d == 1 else f"in {d} days"
            return f"{name} first, the test is {when}."
        week = (today() - timedelta(days=7)).isoformat()
        slips = self.st.one("SELECT COUNT(*) n FROM attempts WHERE topic_id=? AND score<max AND at>=?", (topic["id"], week))["n"]
        if slips >= 2:
            return f"{name} first. It tripped you {slips} times this week, so today we trip it back."
        if topic["label"] == "new":
            return f"{name} is new. I will show you one, then you do one."
        return f"{name} first. It is your wobbliest thing right now. Not for long."

    def _paragraph_due(self):
        """Once a week per writing subject, on a real question from his own papers."""
        week = (today() - timedelta(days=7)).isoformat()
        for s in self.st.subjects():
            if s["name"].lower() not in WRITING:
                continue
            if self.st.one("SELECT 1 FROM attempts WHERE subject_id=? AND kind='paragraph' AND at>=?", (s["id"], week)):
                continue
            q = self.st.one("SELECT q.* FROM questions q WHERE q.subject_id=? AND q.origin='source' AND q.state='active' "
                            "ORDER BY (SELECT MAX(at) FROM attempts a WHERE a.question_id=q.id) IS NOT NULL, created LIMIT 1", (s["id"],))
            if q:
                return q
        return None

    # ---- the front of Study -----------------------------------------------------------------------
    def now(self, device="desk") -> dict:
        day = today().isoformat()
        open_s = self.st.one("SELECT * FROM sessions WHERE state='open' AND day=? ORDER BY started DESC LIMIT 1", (day,))
        done = self.done_today()
        p = self.plan(device)
        tests = self._tests(14)
        test = None
        if tests:
            t = tests[0]
            test = {"id": t["id"], "title": t["title"], "subject": self._subj(t["subject_id"])["name"], "date": t["date"],
                    "days": (date.fromisoformat(t["date"]) - today()).days}
        deb = self.st.one("SELECT * FROM debriefs WHERE day=? ORDER BY created DESC LIMIT 1", (day,))
        src = self.st.q("SELECT id, title, status FROM sources WHERE substr(created,1,10)=? ORDER BY created DESC", (day,))
        state = "empty" if p["kind"] == "empty" else "open" if open_s else "done" if done else "idle" if p["kind"] == "idle" else "ready"
        return {"state": state, "kind": p["kind"], "minutes": p["minutes"], "headline": p["headline"],
                "steps": p["steps"], "session": open_s["id"] if open_s else None, "session_device": open_s["device"] if open_s else None,
                "weak": [{"id": t["id"], "name": t["name"], "subject": t["subject"], "label": t["label"], "score": t["score"], "test": t.get("test", "")} for t in self.weakest(3)],
                "test": test, "debrief": {"done": bool(deb), "result": _j(deb["result"], None) if deb else None, "open": bool(deb and deb["state"] == "open")},
                "inbox": {"today": len(src), "reading": sum(1 for s in src if s["status"] == "processing"), "titles": [s["title"] for s in src[:3]]},
                "homework": [{**h, "subject": self._subj(h["subject_id"])["name"]} for h in self.st.q("SELECT * FROM homework WHERE done=0 ORDER BY due")]}

    def nudge(self, device="phone") -> str | None:
        """The reminder line, or None when today is already done or there is nothing to do."""
        if self.done_today():
            return None
        p = self.plan(device)
        if p["kind"] in ("empty", "idle"):
            return None
        what = p["topic"]["name"] if p.get("topic") else p["steps"][0]["label"] if p["steps"] else "cards"
        return f"Home yet? {p['minutes']} minutes of {what.lower()} and I will leave you alone. Promise."

    def chat_nudge(self) -> str:
        """Once a day, a line for the chat prompt so Cyph brings the session up before the thing he was asked."""
        day = today().isoformat()
        if self.kv("nudged:" + day):
            return ""
        line = self.nudge()
        if not line:
            return ""
        self.kv("nudged:" + day, "1")
        p = self.plan("phone")
        return (f"Today's study session is not done yet ({p['minutes']} minutes: {', '.join(s['label'] for s in p['steps'])}). "
                "Before you answer, remind him of it in ONE short playful sentence and tell him the START button is right there. "
                "Then help with what he asked anyway. This is a nudge, never a condition: do not refuse, do not repeat it.")

    # ---- questions ---------------------------------------------------------------------------------
    def _rule(self, topic_id, kind) -> bool:
        return bool(self.st.one("SELECT 1 FROM rules WHERE topic_id=? AND kind=?", (topic_id, kind)))

    def _notes_for(self, topic) -> list[dict]:
        rows = self.st.q("SELECT title, body FROM notes WHERE topic_id=? ORDER BY updated DESC LIMIT 3", (topic["id"],))
        return rows or self.st.q("SELECT title, body FROM notes WHERE subject_id=? ORDER BY updated DESC LIMIT 1", (topic["subject_id"],))

    def _write_questions(self, topic, n) -> list[str]:
        """Cyph writes questions only from his notes, and never for a topic he said to keep to worksheets."""
        notes = self._notes_for(topic)
        if not notes or self._rule(topic["id"], "not_in_notes"):
            return []
        from .study import QUESTION_SHAPE
        plain = "; ".join(r["text"] for r in self.st.q("SELECT text FROM rules WHERE topic_id=?", (topic["id"],)))
        prompt = (f"Write {n} exam questions on the topic \"{topic['name']}\" using ONLY the facts in his notes below. Do not use anything that is "
                  f"not in the notes, and never invent a source or a quotation. Reply with a JSON array, each item shaped as\n{QUESTION_SHAPE}\n"
                  'Set "key" to "cyph" and "ref" to "".' + (f"\nHe told you: {plain}" if plain else "") +
                  "\n\nHis notes:\n" + "\n---\n".join(f"{x['title']}\n{x['body'][:3500]}" for x in notes))
        got = parse_json(self.ai.generate(SYSTEM + f" Subject: {self._subj(topic['subject_id'])['name']}.", prompt))
        if isinstance(got, dict):
            got = got.get("questions") or [got]
        if not isinstance(got, list):
            return []
        return save_questions(self.st, {"id": None, "subject_id": topic["subject_id"], "title": ""}, got[:n], topic["name"], origin="cyph")

    def _questions_for(self, topic, n, device, avoid=()) -> list[dict]:
        week = (today() - timedelta(days=7)).isoformat()
        rows = self.st.q("SELECT q.*, (SELECT MAX(at) FROM attempts a WHERE a.question_id=q.id) AS last, "
                         "(SELECT COUNT(*) FROM attempts a WHERE a.question_id=q.id AND a.score>=a.max AND a.at>=?) AS right "
                         "FROM questions q WHERE q.topic_id=? AND q.state='active'", (week, topic["id"]))
        rows = [r for r in rows if r["id"] not in avoid and not r["right"]]
        rows.sort(key=lambda r: (r["origin"] != "source", r["last"] or ""))
        if len(rows) < n:
            try:
                made = set(self._write_questions(topic, n - len(rows)))
            except StudyError:
                made = set()
            rows += [r for r in self.st.q("SELECT * FROM questions WHERE topic_id=? AND state='active'", (topic["id"],)) if r["id"] in made]
        if device == "phone" and not self.writing(topic["subject_id"]):
            rows = [r for r in rows if self._ensure_quick(r)]
        return rows[:n]

    def _ensure_quick(self, q) -> bool:
        if quick_of(_j(q["quick"], None)):
            return True
        prompt = ('Cut this question down so it can be answered on a phone by tapping. Reply JSON {"ask":..., "options": 3 or 4 short options, '
                  '"correct": index, "final_label": name of the numeric answer or "", "explain": one sentence}.\n'
                  f"Question: {q['text']}\nAnswer: {q['answer']}")
        try:
            quick = quick_of(parse_json(self.ai.generate(SYSTEM, prompt)))
        except StudyError:
            quick = None
        if quick:
            self.st.run("UPDATE questions SET quick=? WHERE id=?", (json.dumps(quick), q["id"]))
            q["quick"] = json.dumps(quick)
        return bool(quick)

    def _needs_lesson(self, topic) -> bool:
        n = self.st.one("SELECT COUNT(*) n FROM attempts WHERE topic_id=? AND kind!='lesson'", (topic["id"],))["n"]
        return topic["label"] in ("new", "weak") and n < 2 and bool(self._notes_for(topic)) and not self.writing(topic["subject_id"])

    def lesson(self, topic, fresh=False) -> dict | None:
        """A three-minute lesson from his own notes: one worked example, then one for him. Cached per topic."""
        got = None if fresh else self.st.one("SELECT body FROM lessons WHERE topic_id=?", (topic["id"],))
        if got:
            return _j(got["body"], None)
        notes = self._notes_for(topic)
        if not notes:
            return None
        projects = (self.projects() or "").strip()[:2500]
        prompt = (f"Teach \"{topic['name']}\" in under three minutes using ONLY his notes below. Reply JSON:\n"
                  '{"example": {"problem": one worked example, "steps": [3 to 5 short steps, the last is the answer]},\n'
                  ' "turn": {"ask": a problem almost identical to the example, "options": 3 short options, "correct": index, "explain": one sentence},\n'
                  ' "why": ONE plain sentence linking this topic to something he is building, ONLY if the link is real and direct; otherwise ""}\n'
                  + (f"What he is building (from his memory file):\n{projects}\n" if projects else 'He has no projects on file, so "why" is "".\n') +
                  "\nHis notes:\n" + "\n---\n".join(f"{x['title']}\n{x['body'][:3500]}" for x in notes))
        raw = parse_json(self.ai.generate(SYSTEM + f" Subject: {self._subj(topic['subject_id'])['name']}.", prompt))
        turn = quick_of((raw or {}).get("turn") if isinstance(raw, dict) else None)
        ex = (raw or {}).get("example") if isinstance(raw, dict) else None
        if not turn or not isinstance(ex, dict) or not str(ex.get("problem") or "").strip():
            return None
        body = {"example": {"problem": str(ex["problem"])[:800], "steps": [str(s)[:300] for s in (ex.get("steps") or [])][:6]},
                "turn": turn, "source": notes[0]["title"]}
        why = str(raw.get("why") or "").strip()[:240]
        self.st.run("INSERT OR REPLACE INTO lessons VALUES(?,?,?)", (topic["id"], json.dumps(body), now_iso()))
        self.st.run("UPDATE topics SET why=? WHERE id=?", (why, topic["id"]))
        return body

    # ---- running a session ------------------------------------------------------------------------
    def start(self, device="desk", kind="", topic_id=None) -> dict:
        device = "phone" if device == "phone" else "desk"
        day = today().isoformat()
        for old in self.st.q("SELECT id FROM sessions WHERE state='open'"):
            self.st.run("UPDATE sessions SET state='ended', finished=? WHERE id=?", (now_iso(), old["id"]))
        skip = ()
        if kind == "more":
            seen = [_j(s["info"], {}).get("topic") for s in self.st.q("SELECT info FROM sessions WHERE day=?", (day,))]
            skip, kind = tuple(x for x in seen if x), ""
            p = self.plan(device, "more", skip_topics=skip)
        else:
            p = self.plan(device, kind, topic_id)
        if p["kind"] in ("empty", "idle"):
            raise StudyError(p["headline"], 409)
        self._spread_backlog(DEVICES[device]["cards"])
        steps = []
        if p.get("homework"):
            steps.append({"type": "homework", "homework": p["homework"]["id"]})
        if p["kind"] == "prep":
            for tid in p.get("prep_topics", []):
                t = next((x for x in self.topics(p["subject"]) if x["id"] == tid), None)
                if t:
                    steps += self._topic_steps(t, 2, device)
            steps += [{"type": "card", "card": c} for c in p["cards"]]
        else:
            steps += [{"type": "card", "card": c} for c in p["cards"]]
            if p.get("topic"):
                steps += self._topic_steps(p["topic"], 2 if p["kind"] == "topic" else DEVICES[device]["questions"], device)
            if p.get("paragraph"):
                steps.append({"type": "paragraph", "question": p["paragraph"]})
            if p.get("retry"):
                rq = self.st.one("SELECT * FROM questions WHERE id=?", (p["retry"],))
                if rq and (device != "phone" or self._ensure_quick(rq)):
                    steps.append({"type": "question", "question": rq["id"], "retry": True})
        if not steps:
            raise StudyError("I have nothing to ask you yet. Snap a worksheet and I will.", 409)
        tids = sorted({x for x in (self._step_topic(s) for s in steps) if x})
        info = {"topic": p["topic"]["id"] if p.get("topic") else None, "headline": p["headline"], "minutes": p["minutes"],
                "before": {t: self.strength(t) for t in tids}, "steps": p["steps"]}
        sid = new_id("z")
        self.st.run("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?)", (sid, day, p["kind"], device, json.dumps(steps), 0, "open", now_iso(), None, json.dumps(info)))
        self.svc.changed("study")
        return self.view(sid)

    def _topic_steps(self, topic, n, device) -> list[dict]:
        steps = []
        if self._needs_lesson(topic):
            try:
                if self.lesson(topic):
                    steps.append({"type": "lesson", "topic": topic["id"]})
            except StudyError:
                pass
        for q in self._questions_for(topic, n, device):
            steps.append({"type": "question", "question": q["id"]})
        return steps

    def _spread_backlog(self, cap):
        """After time away the pile is spread over the coming days instead of shown. He never sees a backlog."""
        due = self.st.due(None, 5000)
        if len(due) <= cap * 3:
            return
        due.sort(key=lambda c: -c["ease"])
        for i, c in enumerate(due[cap * 2:]):
            self.st.run("UPDATE cards SET due=? WHERE id=?", ((today() + timedelta(days=1 + i // cap)).isoformat(), c["id"]))

    def _step_topic(self, s):
        if s["type"] == "card":
            c = self.st.one("SELECT topic_id FROM cards WHERE id=?", (s["card"],))
            return c["topic_id"] if c else None
        if s["type"] == "lesson":
            return s["topic"]
        if s["type"] in ("question", "paragraph"):
            q = self.st.one("SELECT topic_id FROM questions WHERE id=?", (s["question"],))
            return q["topic_id"] if q else None
        return None

    def _session(self, sid):
        s = self.st.one("SELECT * FROM sessions WHERE id=?", (sid,))
        if not s:
            raise StudyError("no such session", 404)
        s["steps"], s["info"] = _j(s["steps"], []), _j(s["info"], {})
        return s

    def _save(self, s):
        self.st.run("UPDATE sessions SET steps=?, pos=?, state=?, finished=?, info=? WHERE id=?",
                    (json.dumps(s["steps"]), s["pos"], s["state"], s["finished"], json.dumps(s["info"]), s["id"]))

    def _qview(self, q, device) -> dict:
        src = self.st.one("SELECT title FROM sources WHERE id=?", (q["source_id"],)) if q["source_id"] else None
        t = self._topic(q["topic_id"]) or {}
        mode = "plan" if self.writing(q["subject_id"]) else "quick" if device == "phone" else "full"
        quick = quick_of(_j(q["quick"], None)) or {}
        scheme = _j(q["scheme"], [])
        out = {"id": q["id"], "text": q["text"], "marks": q["marks"], "origin": q["origin"], "ref": q["ref"], "source": src["title"] if src else "",
               "key": q["key"], "figure": q["figure"] or None, "topic": t.get("name", ""), "topic_id": q["topic_id"],
               "subject": self._subj(q["subject_id"])["name"], "mode": mode, "why": t.get("why") or ""}
        if mode == "quick":
            out.update(ask=quick.get("ask") or q["text"], options=quick.get("options", []),
                       final_label=(quick.get("final_label") or "Final answer") if q["numeric"] is not None else "", unit=q["unit"] or "")
        if mode == "plan":
            out["plan"] = [p["point"] for p in scheme] or ([q["answer"]] if q["answer"] else [])
        return out

    def _stepview(self, s, step) -> dict:
        t = step["type"]
        base = {"type": t, "result": step.get("result")}
        if t == "card":
            c = self.st.one("SELECT c.*, t.name AS topic, t.why AS why FROM cards c LEFT JOIN topics t ON t.id=c.topic_id WHERE c.id=?", (step["card"],))
            if not c:
                return {**base, "gone": True}
            return {**base, "card": {"id": c["id"], "front": c["front"], "back": c["back"], "topic": c["topic"] or "", "image_id": c["image_id"],
                                     "subject": self._subj(c["subject_id"])["name"], "why": c["why"] or ""}}
        if t == "lesson":
            topic = self._topic(step["topic"])
            body = _j((self.st.one("SELECT body FROM lessons WHERE topic_id=?", (step["topic"],)) or {}).get("body"), None)
            if not topic or not body:
                return {**base, "gone": True}
            return {**base, "topic": topic["name"], "subject": self._subj(topic["subject_id"])["name"], "why": topic.get("why") or "",
                    "lesson": {"example": body["example"], "ask": body["turn"]["ask"], "options": body["turn"]["options"], "source": body.get("source", "")}}
        if t in ("question", "paragraph"):
            q = self.st.one("SELECT * FROM questions WHERE id=? AND state='active'", (step["question"],))
            if not q:
                return {**base, "gone": True}
            v = self._qview(q, s["device"])
            if t == "paragraph":
                v["mode"] = "paragraph"
                v["checklist"] = [p["point"] for p in _j(q["scheme"], [])] or self._checklist(q)
            return {**base, "q": v, "retry": bool(step.get("retry")), "hints": step.get("hints", [])}
        if t == "homework":
            h = self.st.one("SELECT * FROM homework WHERE id=?", (step["homework"],))
            if not h:
                return {**base, "gone": True}
            return {**base, "homework": {**h, "subject": self._subj(h["subject_id"])["name"]}}
        return {**base, "gone": True}

    def _checklist(self, q) -> list[str]:
        """What a strong answer to a writing question has. Made once and kept on the question."""
        try:
            got = parse_json(self.ai.generate(SYSTEM + f" Subject: {self._subj(q['subject_id'])['name']}.",
                                              "List the 3 to 5 things a top-band answer to this question must do, as short checklist lines he can tick "
                                              "himself. Reply with a JSON array of strings.\nQuestion: " + q["text"]))
        except StudyError:
            got = None
        pts = [str(x).strip()[:200] for x in got if str(x).strip()][:5] if isinstance(got, list) else []
        pts = pts or ["Answers the question that was asked", "Uses evidence from the source or text", "Explains, not just describes", "Ends with a clear judgement"]
        self.st.run("UPDATE questions SET scheme=? WHERE id=?", (json.dumps([{"point": p, "marks": 1, "final": False} for p in pts]), q["id"]))
        return pts

    def view(self, sid) -> dict:
        s = self._session(sid)
        while s["state"] == "open" and s["pos"] < len(s["steps"]) and self._stepview(s, s["steps"][s["pos"]]).get("gone"):
            s["pos"] += 1                               # something was deleted since: step over it
        if s["state"] == "open" and s["pos"] >= len(s["steps"]):
            self._finish(s)
        else:
            self._save(s)
        out = {"id": s["id"], "kind": s["kind"], "device": s["device"], "state": s["state"], "pos": s["pos"], "total": len(s["steps"]),
               "types": [x["type"] for x in s["steps"]], "headline": s["info"].get("headline", "")}
        if s["state"] == "open":
            out["step"] = self._stepview(s, s["steps"][s["pos"]])
        else:
            out["summary"] = s["info"].get("summary")
        return out

    def next(self, sid) -> dict:
        s = self._session(sid)
        if s["state"] == "open":
            s["pos"] += 1
            self._save(s)
        return self.view(sid)

    def end(self, sid) -> dict:
        s = self._session(sid)
        if s["state"] == "open":
            answered = sum(1 for x in s["steps"] if x.get("result"))
            if answered:
                self._finish(s)                         # stopping early still counts: he did something
            else:
                s["state"], s["finished"] = "ended", now_iso()
                self._save(s)
            self.svc.changed("study")
        return self.view(sid)

    def _finish(self, s):
        before = s["info"].get("before", {})
        moved = []
        for tid, was in before.items():
            t = self._topic(tid)
            if not t:
                continue
            now_ = self.strength(tid)
            touched = any(self._step_topic(x) == tid and x.get("result") for x in s["steps"])
            if touched:
                moved.append({"id": tid, "name": t["name"], "was": was["label"], "now": now_["label"], "score": now_["score"]})
        moved.sort(key=lambda m: (m["was"] == m["now"], m["score"] if m["score"] is not None else 50))
        up = next((m for m in moved if m["was"] != m["now"] and m["now"] in ("okay", "solid")), None)
        mins = max(1, round((_secs(now_iso()) - _secs(s["started"])) / 60))
        nxt = self._pick_topic(skip=tuple(before))
        s["info"]["summary"] = {"minutes": mins, "moved": moved[:4], "next": nxt["name"] if nxt else "",
                                "headline": (f"{up['name']} just levelled up. Paw bump." if up else "Done. That counted, even if it did not feel like it."),
                                "cards": sum(1 for x in s["steps"] if x["type"] == "card" and x.get("result")),
                                "good": sum(1 for x in s["steps"] if x["type"] == "card" and (x.get("result") or {}).get("grade", 0) >= 1)}
        s["state"], s["finished"] = "done", now_iso()
        self._save(s)
        self.svc.changed("study")

    # ---- answering --------------------------------------------------------------------------------
    def answer(self, sid, body: dict, images=()) -> dict:
        s = self._session(sid)
        if s["state"] != "open" or s["pos"] >= len(s["steps"]):
            raise StudyError("that session is over", 409)
        step = s["steps"][s["pos"]]
        if step.get("result") and step["type"] != "question":
            return step["result"]
        fn = {"card": self._a_card, "lesson": self._a_lesson, "question": self._a_question, "paragraph": self._a_paragraph,
              "homework": self._a_homework}[step["type"]]
        res = fn(s, step, body or {}, images)
        step["result"] = res
        self._save(s)
        self.svc.changed("study")
        return res

    def _a_card(self, s, step, body, images):
        grade = 2 if _int(body.get("grade"), 0) >= 1 else 0
        self.st.review(step["card"], grade)
        return {"grade": grade}

    def _a_lesson(self, s, step, body, images):
        topic = self._topic(step["topic"])
        turn = _j(self.st.one("SELECT body FROM lessons WHERE topic_id=?", (step["topic"],))["body"], {})["turn"]
        ok = _int(body.get("option"), -1) == turn["correct"]
        self.st.add_attempt(topic["subject_id"], topic["name"], "lesson", turn["ask"], str(body.get("option")), 1 if ok else 0, 1, turn["explain"])
        return {"correct": ok, "answer": turn["correct"], "explain": turn["explain"]}

    def _a_homework(self, s, step, body, images):
        self.st.run("UPDATE homework SET done=1 WHERE id=?", (step["homework"],))
        return {"done": True}

    def _a_paragraph(self, s, step, body, images):
        q = self.st.one("SELECT * FROM questions WHERE id=?", (step["question"],))
        ticks = [bool(x) for x in (body.get("ticks") or [])]
        pts = _j(q["scheme"], [])
        n = max(1, len(pts))
        t = self._topic(q["topic_id"]) or {"name": "General"}
        att = self.st.add_attempt(q["subject_id"], t["name"], "paragraph", q["text"], str(body.get("answer") or "(written by hand)"), sum(ticks[:n]), n,
                                  "Self-checked against the list.", q["scheme"], q["id"])
        return {"attempt": att["id"], "ticked": sum(ticks[:n]), "of": n}

    def _a_question(self, s, step, body, images):
        q = self.st.one("SELECT * FROM questions WHERE id=?", (step["question"],))
        if not q:
            raise StudyError("that question is gone", 404)
        t = self._topic(q["topic_id"]) or {"name": "General"}
        hints = len(step.get("hints", []))
        mode = self._qview(q, s["device"])["mode"]
        if mode == "plan":
            had = bool(body.get("had"))
            att = self.st.add_attempt(q["subject_id"], t["name"], "plan", q["text"], str(body.get("answer") or ""), 1 if had else 0, 1,
                                      "Compared with what a strong plan has.", q["scheme"], q["id"], hints)
            return {"attempt": att["id"], "mode": "plan", "had": had}
        if mode == "quick":
            return self._mark_quick(q, t, body, hints)
        return self._mark_full(q, t, str(body.get("answer") or "").strip(), list(images), hints)

    def _mark_quick(self, q, t, body, hints):
        quick = quick_of(_j(q["quick"], None))
        if not quick:
            raise StudyError("that question cannot be answered on a phone")
        pts = []
        opt = _int(body.get("option"), -1)
        ok = opt == quick["correct"]
        pts.append({"point": quick["options"][quick["correct"]], "got": ok, "quote": quick["options"][opt] if 0 <= opt < len(quick["options"]) else "",
                    "why": "" if ok else quick["explain"]})
        if q["numeric"] is not None:
            mine = _num(body.get("value"))
            good = close(mine, q["numeric"])
            want = f"{q['numeric']:g}" + (f" {q['unit']}" if q["unit"] else "")
            pts.append({"point": quick["final_label"] or "Final answer", "got": good, "quote": str(body.get("value") or "").strip()[:40],
                        "why": "" if good else f"It should be {want}. " + (quick["explain"] if ok else ""), "checked": True})
        score = sum(1 for p in pts if p["got"])
        head = "Yes. Clean." if score == len(pts) else "So close. Half of it is right." if score else "Not this time. Here is the slip."
        att = self.st.add_attempt(q["subject_id"], t["name"], "quick", q["text"], json.dumps({"option": opt, "value": body.get("value")}), score, len(pts),
                                  next((p["why"] for p in pts if not p["got"]), ""), q["scheme"], q["id"], hints, json.dumps(pts))
        return {"attempt": att["id"], "mode": "quick", "score": score, "max": len(pts), "points": pts, "headline": head,
                "answer": quick["correct"], "unsure": False}

    MARK = ("Mark this like a fair O-Level examiner, one scheme point at a time. For each point say whether his answer earns it: true, false, or "
            "null when you cannot read his writing or genuinely cannot tell (never guess). Quote the line of HIS answer that earned or lost "
            "the point, word for word (at most 90 characters, \"\" if there is none). Accept any valid method, not only the scheme's.\n"
            'Reply JSON: {"points": [{"i": index, "got": true/false/null, "quote": "...", "why": one short sentence, needed when got is not true}], '
            '"final": his final numeric answer as a plain number or null, "headline": one short warm sentence in a playful voice on how it went, '
            '"slip": his main mistake in under 12 words, or ""}')

    def _mark_full(self, q, t, answer, images, hints, recheck=False):
        if not answer and not images:
            raise StudyError("write an answer first")
        scheme = _j(q["scheme"], []) or [{"point": "Correct final answer: " + (q["answer"] or "see the worked solution"), "marks": q["marks"] or 1, "final": True}]
        prompt = (f"Question ({q['marks']} marks): {q['text']}\nScheme points: {json.dumps([{'i': i, 'point': p['point'], 'marks': p['marks']} for i, p in enumerate(scheme)])}\n"
                  + (f"Known final answer: {q['answer']}\n" if q["answer"] else "") + f"His answer: {answer or '(in the attached picture)'}\n\n" + self.MARK
                  + ("\nHe says your first marking was wrong. Look again from scratch and be fair: if his working is valid, give the marks." if recheck else ""))
        got = parse_json(self.ai.generate(SYSTEM + f" Subject: {self._subj(q['subject_id'])['name']}.", prompt, images))
        if not isinstance(got, dict) or not isinstance(got.get("points"), list):
            raise StudyError("I could not mark that, try again", 502)
        by = {(_int(p.get("i"), -1)): p for p in got["points"] if isinstance(p, dict)}
        pts = []
        for i, p in enumerate(scheme):
            g = by.get(i, {})
            v = g.get("got")
            pts.append({"point": p["point"], "marks": p["marks"], "got": v if isinstance(v, bool) else None,
                        "quote": str(g.get("quote") or "")[:120], "why": str(g.get("why") or "")[:240], "final": p.get("final", False)})
        if q["numeric"] is not None:                     # the final number is checked here, not taken on trust
            mine = _num(got.get("final")) if got.get("final") is not None else None
            if mine is None and answer:
                nums = re.findall(r"-?\d+(?:\.\d+)?", answer.replace(",", ""))
                mine = float(nums[-1]) if nums else None
            fin = next((p for p in pts if p["final"]), None)
            if fin is not None and mine is not None:
                good = close(mine, q["numeric"])
                fin["got"], fin["checked"] = good, True
                if not good:
                    fin["why"] = f"The final answer should be {q['numeric']:g}" + (f" {q['unit']}" if q["unit"] else "") + f", you have {mine:g}."
        unsure = [p for p in pts if p["got"] is None]
        score = sum(p["marks"] for p in pts if p["got"] is not False)       # an unreadable line gets the benefit of the doubt
        mx = sum(p["marks"] for p in pts)
        att = self.st.add_attempt(q["subject_id"], t["name"], "full", q["text"], answer or "(picture)", score, mx, str(got.get("slip") or "")[:200],
                                  q["scheme"], q["id"], hints, json.dumps(pts))
        if unsure:                                       # and the question comes back, so the doubt is settled later
            self.st.run("UPDATE attempts SET disputed=1 WHERE id=?", (att["id"],))
        return {"attempt": att["id"], "mode": "full", "score": score, "max": mx, "points": pts, "unsure": bool(unsure),
                "headline": str(got.get("headline") or ("Full marks." if score == mx else "Nearly."))[:160], "slip": str(got.get("slip") or "")[:200]}

    def dispute(self, aid) -> dict:
        """"I think I'm right": mark it again at once. If it is still not certain he gets the marks, and it comes back later."""
        a = self.st.one("SELECT * FROM attempts WHERE id=?", (aid,))
        if not a or not a["question_id"]:
            raise StudyError("nothing to look at again", 404)
        q = self.st.one("SELECT * FROM questions WHERE id=?", (a["question_id"],))
        if not q:
            raise StudyError("that question is gone", 404)
        was = a["score"]
        if a["kind"] != "full":                          # a tapped answer is checked by code: the key itself is in doubt, so give it
            self.st.run("UPDATE attempts SET score=max, disputed=1 WHERE id=?", (aid,))
            return {"changed": True, "score": a["max"], "max": a["max"], "points": [], "headline": "Fair. I will take your word and check my answer key. Mark given.",
                    "again": True}
        t = self._topic(q["topic_id"]) or {"name": "General"}
        res = self._mark_full(q, t, a["answer"], [], a["hints"], recheck=True)
        self.st.run("DELETE FROM attempts WHERE id=?", (res["attempt"],))
        score = res["score"]
        self.st.run("UPDATE attempts SET score=?, disputed=1, detail=? WHERE id=?", (max(was, score), json.dumps(res["points"]), aid))
        changed = score > was
        head = "I checked again. You are right, my bad. Mark given." if changed else "I looked again and I still get the same. It comes back in a few days so we can settle it."
        return {"changed": changed, "score": max(was, score), "max": res["max"], "points": res["points"], "headline": head, "again": True}

    def hint(self, sid, n=None) -> dict:
        """The next rung of the hint ladder for the question on screen. Free: it only makes the question return sooner."""
        s = self._session(sid)
        step = s["steps"][s["pos"]] if s["pos"] < len(s["steps"]) else None
        if not step or step["type"] != "question":
            raise StudyError("hints are for questions", 409)
        q = self.st.one("SELECT * FROM questions WHERE id=?", (step["question"],))
        have = step.setdefault("hints", [])
        i = len(have) if n is None else max(0, min(int(n), 3))
        if i < len(have):
            return {"hints": have, "last": len(have) >= 4}
        if i >= 4:
            return {"hints": have, "last": True}
        if i == 3 and q["answer"] and _j(q["scheme"], []):
            text = "\n".join(p["point"] for p in _j(q["scheme"], [])) + f"\n\nAnswer: {q['answer']}"
        else:
            text = self.svc.tutor(q["text"], q["subject_id"], i, "\n".join(have))["text"]
        have.append(text)
        self._save(s)
        return {"hints": have, "last": len(have) >= 4}

    # ---- flagging: it is his system, so he fixes it as he uses it -----------------------------------
    def flag_question(self, qid, reason) -> dict:
        q = self.st.one("SELECT * FROM questions WHERE id=?", (qid,))
        if not q:
            raise StudyError("no such question", 404)
        reason = reason if reason in FLAGS else "confusing"
        t = self._topic(q["topic_id"]) or {"name": "this topic"}
        rule = FLAGS[reason].replace("this topic", t["name"])
        self.st.run("UPDATE questions SET state='binned' WHERE id=?", (qid,))
        if not self._rule(q["topic_id"], reason):
            self.st.run("INSERT INTO rules VALUES(?,?,?,?,?,?)", (new_id("u"), q["subject_id"], q["topic_id"], reason, rule, now_iso()))
        if reason == "not_in_notes":                     # and the other ones Cyph wrote for this topic go too
            self.st.run("UPDATE questions SET state='binned' WHERE topic_id=? AND origin='cyph'", (q["topic_id"],))
        self.svc.changed("study")
        return {"binned": qid, "rule": rule, "topic": t["name"]}

    def flag_card(self, cid) -> dict:
        self.st.edit_card(cid, {"state": "suspended"})
        self.svc.changed("study")
        return {"binned": cid, "rule": "Binned that card. It will not come back."}

    # ---- debrief: explain it and Cyph finds the gaps ------------------------------------------------
    DEBRIEF = ("He is telling you what he learned at school today. From everything he has said so far, work out which topics he covered and how "
               "well he explained each. Ask at most ONE short follow-up question about the thinnest or wrong part, or none if there is nothing "
               "worth asking or he has already answered two. Reply JSON:\n"
               '{"topics": [{"subject": one of his subjects, "topic": syllabus topic, "state": "solid", "shaky" or "missed", "gap": what was missing, or ""}],\n'
               ' "cards": [{"subject": ..., "topic": ..., "front": a question on something he got wrong or missed, "back": the correct short answer}],\n'
               ' "ask": the follow-up question, or "", "reply": one or two short warm, playful sentences reacting to what he said}')

    def debrief(self, text, finish=False) -> dict:
        text = str(text or "").strip()[:4000]
        day = today().isoformat()
        d = self.st.one("SELECT * FROM debriefs WHERE day=? AND state='open' ORDER BY created DESC LIMIT 1", (day,))
        turns = _j(d["turns"], []) if d else []
        if not text and not turns:
            raise StudyError("tell me one thing from today")
        if text:
            turns.append({"who": "you", "text": text})
        asked = sum(1 for t in turns if t["who"] == "cyph")
        names = ", ".join(s["name"] for s in self.st.subjects())
        prompt = (self.DEBRIEF + f"\nHis subjects: {names}.\n" + ("He wants to finish now: ask nothing more.\n" if finish or asked >= 2 else "") +
                  "\nThe conversation so far:\n" + "\n".join(("HIM: " if t["who"] == "you" else "YOU: ") + t["text"] for t in turns))
        got = parse_json(self.ai.generate(SYSTEM, prompt))
        if not isinstance(got, dict):
            raise StudyError("I lost the thread, say that again", 502)
        ask = "" if finish or asked >= 2 else str(got.get("ask") or "").strip()[:300]
        reply = str(got.get("reply") or "").strip()[:400]
        turns.append({"who": "cyph", "text": (reply + " " + ask).strip() or "Got it."})
        topics = [{"subject": str(t.get("subject") or ""), "topic": str(t.get("topic") or "")[:80], "state": t.get("state") if t.get("state") in ("solid", "shaky", "missed") else "shaky",
                   "gap": str(t.get("gap") or "")[:200]} for t in (got.get("topics") or []) if isinstance(t, dict) and str(t.get("topic") or "").strip()][:8]
        result = {"topics": topics, "cards": 0, "done": not ask}
        did = d["id"] if d else new_id("d")
        if not d:
            self.st.run("INSERT INTO debriefs VALUES(?,?,?,?,?,?)", (did, day, "[]", "{}", "open", now_iso()))
        if not ask:                                      # finished: what he said becomes cards and strength
            for t in topics:
                subj = self._find_subject(t["subject"])
                if not subj:
                    continue
                self.st.add_attempt(subj["id"], t["topic"], "debrief", "Explained in a debrief", t["gap"], {"solid": 1, "shaky": 0.5, "missed": 0}[t["state"]], 1, t["gap"])
            for c in (got.get("cards") or [])[:6]:
                subj = self._find_subject(str(c.get("subject") or "")) if isinstance(c, dict) else None
                if subj and str(c.get("front") or "").strip() and str(c.get("back") or "").strip():
                    self.st.add_card(subj["id"], c["front"], c["back"], "approved", c.get("topic") or None)
                    result["cards"] += 1
        self.st.run("UPDATE debriefs SET turns=?, result=?, state=? WHERE id=?", (json.dumps(turns), json.dumps(result), "open" if ask else "done", did))
        self.svc.changed("study")
        return {"id": did, "turns": turns, "ask": ask, **result}

    # ---- homework, teacher marks -------------------------------------------------------------------
    def add_homework(self, subject_ref, title, due, topic=None) -> dict:
        subj = self.st.subject(subject_ref)
        if not subj:
            raise StudyError("which subject is it for?")
        try:
            date.fromisoformat(str(due))
        except ValueError:
            raise StudyError("dates look like 2026-11-20")
        title = " ".join(str(title or "").split())[:120]
        if not title:
            raise StudyError("what is the homework?")
        ev = None
        if self.svc.planner is not None:
            ev = self.svc.planner.create({"title": f"{subj['name']} homework: {title}", "date": str(due), "kind": "deadline", "project": "Study"}, today())["event"]["id"]
        hid = new_id("h")
        self.st.run("INSERT INTO homework VALUES(?,?,?,?,?,?,?,?)", (hid, subj["id"], self.st.topic(subj["id"], topic) if topic else None, title, str(due), ev, 0, now_iso()))
        self.svc.changed("study")
        self.svc.changed("plan")
        return {**self.st.one("SELECT * FROM homework WHERE id=?", (hid,)), "subject": subj["name"]}

    def done_homework(self, hid, done=True):
        if not self.st.one("SELECT 1 FROM homework WHERE id=?", (hid,)):
            raise StudyError("no such homework", 404)
        self.st.run("UPDATE homework SET done=? WHERE id=?", (1 if done else 0, hid))
        self.svc.changed("study")

    def teacher_mark(self, subject_ref, topic, score, mx, what="") -> dict:
        """A mark from a real teacher. It counts four times as much as anything Cyph gave."""
        subj = self.st.subject(subject_ref)
        if not subj or float(mx) <= 0:
            raise StudyError("I need the subject and a score out of something")
        self.st.add_attempt(subj["id"], topic or "General", "teacher", what or "Teacher's mark", "", max(0.0, min(float(score), float(mx))), float(mx), "From his teacher.")
        self.svc.changed("study")
        return {"subject": subj["name"], "topic": topic or "General", "score": float(score), "max": float(mx)}

    # ---- inbox and library -------------------------------------------------------------------------
    def inbox(self, limit=30) -> list[dict]:
        out = []
        for s in self.st.q("SELECT * FROM sources ORDER BY created DESC LIMIT ?", (limit,)):
            note = self.st.one("SELECT n.id, t.name AS topic FROM notes n LEFT JOIN topics t ON t.id=n.topic_id WHERE n.source_id=?", (s["id"],))
            out.append({"id": s["id"], "title": s["title"], "kind": s["kind"], "status": s["status"], "error": s["error"], "created": s["created"],
                        "subject": self._subj(s["subject_id"])["name"] if s["subject_id"] else "", "subject_id": s["subject_id"],
                        "topic": (note or {}).get("topic") or "", "note_id": (note or {}).get("id"),
                        "cards": self.st.one("SELECT COUNT(*) n FROM cards WHERE source_id=?", (s["id"],))["n"],
                        "questions": self.st.one("SELECT COUNT(*) n FROM questions WHERE source_id=? AND state='active'", (s["id"],))["n"]})
        return out

    def library(self) -> dict:
        subs = []
        for s in self.st.subjects():
            ts = self.topics(s["id"])
            scored = [t["score"] for t in ts if t["score"] is not None]
            avg = round(sum(scored) / len(scored)) if scored else None
            subs.append({**s, "score": avg, "label": "new" if avg is None else "weak" if avg < 45 else "okay" if avg < 72 else "solid",
                         "topics": sorted(ts, key=lambda t: (t["score"] is None, t["score"] if t["score"] is not None else 0)),
                         "due": len(self.st.due(s["id"], 5000))})
        subs.sort(key=lambda s: (s["score"] is None, s["score"] if s["score"] is not None else 0))
        return {"subjects": subs}

    def topic_detail(self, tid) -> dict:
        t = next((x for x in self.topics() if x["id"] == tid), None)
        if not t:
            raise StudyError("no such topic", 404)
        att = self.st.q("SELECT id, kind, prompt, score, max, feedback, at FROM attempts WHERE topic_id=? AND kind NOT IN ('lesson') ORDER BY at DESC LIMIT 12", (tid,))
        wrong = [a for a in att if a["score"] < a["max"]]
        slip = next((a["feedback"] for a in wrong if a["feedback"] and a["kind"] in ("full", "quick")), "")
        recent = att[:4]
        return {**t, "why": t.get("why") or "", "slip": slip,
                "record": f"Wrong {sum(1 for a in recent if a['score'] < a['max'])} of the last {len(recent)}." if recent else "",
                "notes": self.st.q("SELECT id, title, updated FROM notes WHERE topic_id=? ORDER BY updated DESC", (tid,)),
                "card_list": self.st.q("SELECT id, front, back, state, due FROM cards WHERE topic_id=? AND state!='suspended' ORDER BY due LIMIT 60", (tid,)),
                "question_list": [{"id": q["id"], "text": q["text"], "origin": q["origin"], "ref": q["ref"], "marks": q["marks"]}
                                  for q in self.st.q("SELECT * FROM questions WHERE topic_id=? AND state='active' ORDER BY origin DESC, created LIMIT 40", (tid,))],
                "attempts": att, "rules": [r["text"] for r in self.st.q("SELECT text FROM rules WHERE topic_id=?", (tid,))]}

    def add_syllabus(self, subject_ref, names) -> int:
        subj = self.st.subject(subject_ref)
        if not subj:
            raise StudyError("no such subject", 404)
        n = 0
        for name in names or []:
            if str(name).strip():
                self.st.topic(subj["id"], str(name))
                n += 1
        self.svc.changed("study")
        return n


def _secs(iso: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0
