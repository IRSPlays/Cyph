"""Study mode: the store (SQLite), the spaced-repetition scheduler and the service that turns a photo or a PDF into
notes, cards and practice with Gemini. Everything the model does goes through a small `ai` object with two methods,
`generate(system, prompt, parts=()) -> str` and `embed(texts) -> list[list[float]]`, so the tests use a fake and the
real one (GeminiStudyAI) is only built when the server runs. Problems come back as StudyError with a message that is
safe to show."""
from __future__ import annotations

import io
import json
import math
import os
import re
import secrets
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from ..clock import TZ

SUBJECTS = [("Math", "#d9984a"), ("Chemistry", "#2f9d8f"), ("Physics", "#5dff7a"), ("Amath", "#f08a73"),
            ("English", "#b79df0"), ("SS", "#e0c36a"), ("Geog", "#7fb4e8")]
# Topic names for the two maths subjects, so a fresh install has something to practise before any worksheet is photographed.
# Names only (no exam year or paper codes); the other subjects get their topics from the material he adds, or from POST /study/syllabus.
SEED_TOPICS = {
    "math": ["Numbers and the four operations", "Ratio and proportion", "Percentage", "Rate and speed", "Indices and standard form",
             "Algebraic manipulation and formulae", "Linear and simultaneous equations", "Quadratic equations and functions", "Inequalities",
             "Graphs of functions", "Set language and notation", "Angles, triangles and polygons", "Congruence and similarity",
             "Properties of circles", "Pythagoras and trigonometry", "Bearings", "Mensuration", "Coordinate geometry", "Vectors in two dimensions",
             "Statistics", "Probability"],
    "amath": ["Quadratic functions and equations", "Equations and inequalities", "Surds", "Polynomials and partial fractions", "Binomial expansion",
              "Exponential and logarithmic functions", "Trigonometric functions and identities", "Trigonometric equations", "Coordinate geometry",
              "Linear law", "Differentiation and its applications", "Integration and its applications", "Kinematics", "Permutations and combinations",
              "Proofs in plane geometry", "Simultaneous equations"],
}
GRADES = {0: "again", 1: "hard", 2: "good"}
CARD_STATES = ("draft", "approved", "suspended")
PRACTICE_KINDS = ("worked", "structured", "sbq", "essay", "paper")
HINT_STEPS = ("restate", "nudge", "first_step", "solution")
MAX_FILE = 25 * 1024 * 1024
MIME_OK = {"application/pdf", "image/png", "image/jpeg", "image/webp", "text/plain"}
EMBED_MODEL = os.environ.get("CYPH_EMBED_MODEL", "gemini-embedding-2")
STUDY_MODEL = os.environ.get("CYPH_STUDY_MODEL", "gemini-3.8-flash")


class StudyError(Exception):
    def __init__(self, msg: str, code: int = 422):
        super().__init__(msg)
        self.code = code


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def today() -> date:
    return datetime.now(TZ).date()


# ---- the scheduler ---------------------------------------------------------------------------------------
def schedule(card: dict, grade: int, on: date) -> dict:
    """The card's next state after a grade (0 again, 1 hard, 2 got it). Pure: returns interval, ease, reps, lapses, due."""
    if grade not in GRADES:
        raise StudyError("grade must be 0, 1 or 2")
    interval, ease, reps, lapses = card["interval"], card["ease"], card["reps"], card["lapses"]
    if grade == 0:
        reps, lapses, ease, interval = 0, lapses + 1, max(1.3, ease - 0.2), 0
    elif grade == 1:
        ease = max(1.3, ease - 0.15)
        interval = 1 if reps == 0 else max(1, round(interval * 1.2))
        reps += 1
    else:
        interval = 1 if reps == 0 else 3 if reps == 1 else max(1, round(interval * ease))
        ease = min(3.0, ease + 0.05)
        reps += 1
    return {"interval": interval, "ease": round(ease, 3), "reps": reps, "lapses": lapses,
            "due": (on + timedelta(days=interval)).isoformat()}


# ---- text helpers ----------------------------------------------------------------------------------------
def chunk_text(text: str, size: int = 700) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    out, cur = [], ""
    for p in paras:
        while len(p) > size * 1.5:                    # an enormous paragraph: cut at a sentence end if there is one
            cut = p.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 3 else size
            if cur:
                out.append(cur)
                cur = ""
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if cur and len(cur) + len(p) > size:
            out.append(cur)
            cur = ""
        cur = (cur + "\n\n" + p).strip()
    if cur:
        out.append(cur)
    return out


_TEX = ("frac|dfrac|tfrac|sqrt|times|theta|text|right|left|beta|rho|nu|neq|ne|le|leq|ge|geq|lambda|log|ln|cdot|circ|pi|sigma|sin|cos|tan|"
        "int|sum|lim|infty|pm|div|vec|overrightarrow|angle|triangle|binom|alpha|gamma|delta|omega|mu|phi|Delta|ce|rightarrow|to|approx|equiv|"
        "bar|hat|overline|underline|mathrm|mathbf|quad|ldots|dots|perp|parallel|therefore|because|cap|cup|in|subset|forall|exists")


def _protect_tex(t: str) -> str:
    """A model that writes LaTeX inside a JSON string often forgets to double the backslash: \\frac would read as a form feed.
    Double every backslash that starts a known LaTeX command and is not already doubled."""
    return re.sub(r"(?<!\\)\\(?=(?:" + _TEX + r")(?![A-Za-z]))", r"\\\\", t)


def parse_json(text: str):
    """The first JSON value in a model answer, with or without a code fence; None if there is none."""
    t = _protect_tex((text or "").strip())
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        a, b = t.find(opener), t.rfind(closer)
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except ValueError:
                continue
    return None


def _topic_key(name: str) -> str:
    w = [re.sub(r"ies$", "y", x) if x.endswith("ies") else x[:-1] if x.endswith("s") and not x.endswith("ss") and len(x) > 3 else x
         for x in re.findall(r"[a-z0-9]+", str(name).lower())]
    w = [x for x in w if x not in ("and", "of", "the", "equation", "function", "in", "to", "a")]
    return " ".join(sorted(w))


def _int(v, default: int) -> int:
    try:
        return int(float(str(v).strip().split()[0]))
    except (ValueError, IndexError):
        return default


def _vec(v: list[float]) -> bytes:
    import numpy as np
    return np.asarray(v, dtype="float32").tobytes()


def _unvec(b: bytes):
    import numpy as np
    return np.frombuffer(b, dtype="float32")


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2}


SCHEMA = """
CREATE TABLE IF NOT EXISTS subjects(id TEXT PRIMARY KEY, name TEXT UNIQUE, color TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, name TEXT, mime TEXT, size INTEGER, created TEXT);
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, subject_id TEXT, title TEXT, kind TEXT, file_id TEXT,
  status TEXT, error TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS source_outputs(source_id TEXT, kind TEXT, body TEXT, PRIMARY KEY(source_id, kind));
CREATE TABLE IF NOT EXISTS topics(id TEXT PRIMARY KEY, subject_id TEXT, name TEXT, created TEXT, UNIQUE(subject_id, name));
CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, subject_id TEXT, source_id TEXT, topic_id TEXT, title TEXT,
  body TEXT, kind TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY AUTOINCREMENT, note_id TEXT, subject_id TEXT, text TEXT,
  embedding BLOB);
CREATE TABLE IF NOT EXISTS cards(id TEXT PRIMARY KEY, subject_id TEXT, topic_id TEXT, note_id TEXT, front TEXT,
  back TEXT, image_id TEXT, state TEXT, due TEXT, interval INTEGER DEFAULT 0, ease REAL DEFAULT 2.5,
  reps INTEGER DEFAULT 0, lapses INTEGER DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT, grade INTEGER, at TEXT,
  interval INTEGER);
CREATE TABLE IF NOT EXISTS practice(id TEXT PRIMARY KEY, body TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, subject_id TEXT, topic_id TEXT, kind TEXT, prompt TEXT,
  answer TEXT, score REAL, max REAL, feedback TEXT, scheme TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS tests(id TEXT PRIMARY KEY, subject_id TEXT, title TEXT, date TEXT, plan_event_id TEXT,
  topics TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS notebooks(id TEXT PRIMARY KEY, subject_id TEXT, title TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS pages(id TEXT PRIMARY KEY, notebook_id TEXT, idx INTEGER, background TEXT, strokes TEXT,
  updated TEXT);
CREATE TABLE IF NOT EXISTS boards(id TEXT PRIMARY KEY, subject_id TEXT, title TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS board_items(id TEXT PRIMARY KEY, board_id TEXT, kind TEXT, x REAL, y REAL, w REAL, h REAL,
  data TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS questions(id TEXT PRIMARY KEY, subject_id TEXT, topic_id TEXT, source_id TEXT, origin TEXT, ref TEXT,
  text TEXT, marks INTEGER, scheme TEXT, answer TEXT, numeric REAL, unit TEXT, key TEXT, quick TEXT, figure TEXT, state TEXT,
  created TEXT);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, day TEXT, kind TEXT, device TEXT, steps TEXT, pos INTEGER, state TEXT,
  started TEXT, finished TEXT, info TEXT);
CREATE TABLE IF NOT EXISTS lessons(topic_id TEXT PRIMARY KEY, body TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY, subject_id TEXT, topic_id TEXT, kind TEXT, text TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS debriefs(id TEXT PRIMARY KEY, day TEXT, turns TEXT, result TEXT, state TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS homework(id TEXT PRIMARY KEY, subject_id TEXT, topic_id TEXT, title TEXT, due TEXT, plan_event_id TEXT,
  done INTEGER DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS i_cards_due ON cards(state, due);
CREATE INDEX IF NOT EXISTS i_chunks_subject ON chunks(subject_id);
"""


def new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(4)


class StudyStore:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "files").mkdir(exist_ok=True)
        self.db = sqlite3.connect(str(self.folder / "study.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            if not self.db.execute("SELECT 1 FROM subjects").fetchone():
                for name, color in SUBJECTS:
                    self.db.execute("INSERT INTO subjects VALUES(?,?,?,?)", (new_id("s"), name, color, now_iso()))
            if not any(r[1] == 'blocks' for r in self.db.execute("PRAGMA table_info(tests)")):
                self.db.execute("ALTER TABLE tests ADD COLUMN blocks TEXT DEFAULT '[]'")      # revision blocks made with the test
            for table, col, kind in (("attempts", "question_id", "TEXT"), ("attempts", "hints", "INTEGER DEFAULT 0"),
                                     ("attempts", "disputed", "INTEGER DEFAULT 0"), ("attempts", "detail", "TEXT DEFAULT ''"),
                                     ("topics", "why", "TEXT"), ("cards", "source_id", "TEXT"),
                                     ("questions", "keyspec", "TEXT DEFAULT ''"), ("attempts", "redo", "INTEGER DEFAULT 0"),
                                     ("attempts", "tag", "TEXT DEFAULT ''")):
                if not any(r[1] == col for r in self.db.execute(f"PRAGMA table_info({table})")):
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
            # an import cut short by a restart would read "processing" forever
            self.db.execute("UPDATE sources SET status='failed', error='interrupted, try again' WHERE status='processing'")
            self.db.commit()
        self.seed()

    def seed(self):
        """Maths topics once, for a subject with none yet (so deleting one he does not want is not undone)."""
        for sub in self.q("SELECT id, name FROM subjects"):
            names = SEED_TOPICS.get(sub["name"].strip().lower().replace("-", "").replace(" ", ""))
            if names and not self.one("SELECT 1 FROM topics WHERE subject_id=?", (sub["id"],)) and not self.one("SELECT 1 FROM kv WHERE k=?", ("seeded:" + sub["id"],)):
                for n in names:
                    self.topic(sub["id"], n)
                self.run("INSERT INTO kv(k, v) VALUES(?,?)", ("seeded:" + sub["id"], "1"))

    def close(self):
        with self.lock:
            self.db.close()

    # -- plumbing
    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def run(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # -- practice questions waiting for an answer (kept on disk so a restart does not expire them)
    def put_practice(self, item: dict):
        self.run("INSERT OR REPLACE INTO practice VALUES(?,?,?)", (item["id"], json.dumps(item), now_iso()))
        self.run("DELETE FROM practice WHERE id NOT IN (SELECT id FROM practice ORDER BY created DESC, rowid DESC LIMIT 200)")

    def get_practice(self, pid: str):
        row = self.one("SELECT body FROM practice WHERE id=?", (pid,))
        return json.loads(row["body"]) if row else None

    def drop_practice(self, pid: str):
        self.run("DELETE FROM practice WHERE id=?", (pid,))

    # -- subjects
    def subjects(self):
        return self.q("SELECT * FROM subjects ORDER BY rowid")

    def subject(self, ref: str | None):
        """A subject by id or (case-insensitive) name; None for no reference; StudyError if it does not exist."""
        if not ref:
            return None
        got = self.one("SELECT * FROM subjects WHERE id=? OR lower(name)=lower(?)", (ref, ref))
        if not got:
            raise StudyError("no such subject", 404)
        return got

    def add_subject(self, name: str, color: str = "#d9984a"):
        name = " ".join(str(name or "").split())[:40]
        if not name:
            raise StudyError("a subject needs a name")
        if self.one("SELECT 1 FROM subjects WHERE lower(name)=lower(?)", (name,)):
            raise StudyError("that subject exists", 409)
        sid = new_id("s")
        self.run("INSERT INTO subjects VALUES(?,?,?,?)", (sid, name, color, now_iso()))
        return self.one("SELECT * FROM subjects WHERE id=?", (sid,))

    def remove_subject(self, sid: str):
        self.subject(sid)
        for src in self.q("SELECT id FROM sources WHERE subject_id=?", (sid,)):
            self.remove_source(src["id"])
        self.run("DELETE FROM reviews WHERE card_id IN (SELECT id FROM cards WHERE subject_id=?)", (sid,))
        for t in ("cards", "notes", "chunks", "topics", "attempts", "tests"):
            self.run(f"DELETE FROM {t} WHERE subject_id=?", (sid,))
        for t in ("notebooks", "boards"):                  # pen work is kept, just no longer filed under a subject
            self.run(f"UPDATE {t} SET subject_id=NULL WHERE subject_id=?", (sid,))
        self.run("DELETE FROM subjects WHERE id=?", (sid,))

    # -- files
    def put_file(self, name: str, mime: str, data: bytes) -> str:
        if len(data) > MAX_FILE:
            raise StudyError("that file is too big (25 MB limit)", 413)
        fid = new_id("f")
        (self.folder / "files" / fid).write_bytes(data)
        self.run("INSERT INTO files VALUES(?,?,?,?,?)", (fid, name[:120], mime, len(data), now_iso()))
        return fid

    def get_file(self, fid: str):
        meta = self.one("SELECT * FROM files WHERE id=?", (fid,))
        if not meta:
            return None
        try:
            return meta, (self.folder / "files" / fid).read_bytes()
        except OSError:
            return None

    # -- topics
    def topic(self, subject_id: str, name: str) -> str:
        name = " ".join(str(name or "").split())[:80] or "General"
        got = self.one("SELECT id FROM topics WHERE subject_id=? AND lower(name)=lower(?)", (subject_id, name))
        if got:
            return got["id"]
        key = _topic_key(name)                          # "Quadratics" and "Quadratic equations" are one topic, not two
        for r in self.q("SELECT id, name FROM topics WHERE subject_id=?", (subject_id,)):
            if _topic_key(r["name"]) == key:
                return r["id"]
        tid = new_id("t")
        self.run("INSERT INTO topics(id,subject_id,name,created) VALUES(?,?,?,?)", (tid, subject_id, name, now_iso()))
        return tid

    def topics(self, subject_id: str | None = None):
        sql = ("SELECT t.*, (SELECT COUNT(*) FROM cards c WHERE c.topic_id=t.id AND c.state='approved') AS cards, "
               "(SELECT COUNT(*) FROM notes n WHERE n.topic_id=t.id) AS notes FROM topics t")
        rows = self.q(sql + (" WHERE t.subject_id=?" if subject_id else "") + " ORDER BY t.name", (subject_id,) if subject_id else ())
        weak = {w["id"]: w for w in self.weak_topics(subject_id, limit=1000, include_ok=True)}
        for r in rows:
            r["weakness"] = round(weak.get(r["id"], {}).get("weakness", 0.0), 3)
        return rows

    def rename_topic(self, tid: str, name: str):
        t = self.one("SELECT * FROM topics WHERE id=?", (tid,))
        if not t:
            raise StudyError("no such topic", 404)
        name = " ".join(str(name or "").split())[:80]
        if not name:
            raise StudyError("a topic needs a name")
        other = self.one("SELECT id FROM topics WHERE subject_id=? AND lower(name)=lower(?) AND id!=?", (t["subject_id"], name, tid))
        if other:                                   # renaming onto an existing topic merges the two
            self.merge_topics(tid, other["id"])
            return self.one("SELECT * FROM topics WHERE id=?", (other["id"],))
        self.run("UPDATE topics SET name=? WHERE id=?", (name, tid))
        return self.one("SELECT * FROM topics WHERE id=?", (tid,))

    def merge_topics(self, src: str, dst: str):
        a, b = (self.one("SELECT * FROM topics WHERE id=?", (x,)) for x in (src, dst))
        if not a or not b or a["subject_id"] != b["subject_id"] or src == dst:
            raise StudyError("those topics cannot be merged")
        for t in ("cards", "notes", "attempts", "questions", "homework"):
            self.run(f"UPDATE {t} SET topic_id=? WHERE topic_id=?", (dst, src))
        self.run("DELETE FROM topics WHERE id=?", (src,))

    def weak_topics(self, subject_id: str | None = None, limit: int = 5, include_ok: bool = False):
        """Topics ranked by how badly they are going: low practice scores, cards forgotten, cards overdue."""
        out = []
        today_s = today().isoformat()
        for t in self.q("SELECT * FROM topics" + (" WHERE subject_id=?" if subject_id else ""), (subject_id,) if subject_id else ()):
            att = self.q("SELECT score, max FROM attempts WHERE topic_id=? AND max>0 ORDER BY at DESC LIMIT 10", (t["id"],))
            miss = (1 - sum(a["score"] for a in att) / sum(a["max"] for a in att)) if att else None
            cards = self.q("SELECT reps, lapses, due FROM cards WHERE topic_id=? AND state='approved'", (t["id"],))
            lapse = (sum(c["lapses"] for c in cards) / max(1, sum(c["lapses"] + c["reps"] for c in cards))) if cards else None
            overdue = (sum(1 for c in cards if c["due"] and c["due"] < today_s) / len(cards)) if cards else None
            parts = [(0.6, miss), (0.3, lapse), (0.1, overdue)]
            have = [(w, v) for w, v in parts if v is not None]
            if not have:
                score = 0.0
            else:
                score = sum(w * v for w, v in have) / sum(w for w, _ in have)
            if score > 0.15 or include_ok:
                out.append({"id": t["id"], "subject_id": t["subject_id"], "name": t["name"], "weakness": score,
                            "attempts": len(att), "cards": len(cards)})
        out.sort(key=lambda x: -x["weakness"])
        return out[:limit]

    # -- notes and chunks
    def add_note(self, subject_id: str, title: str, body: str, kind: str = "typed", source_id: str | None = None,
                 topic: str | None = None, embeddings: list | None = None):
        title = " ".join(str(title or "").split())[:120] or "Untitled"
        body = str(body or "").strip()
        if not body:
            raise StudyError("a note needs some text")
        nid = new_id("n")
        tid = self.topic(subject_id, topic) if topic else None
        ts = now_iso()
        self.run("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?)", (nid, subject_id, source_id, tid, title, body[:60000], kind, ts, ts))
        self.set_chunks(nid, subject_id, body, embeddings)
        return self.note(nid)

    def set_chunks(self, nid: str, subject_id: str, body: str, embeddings=None, pieces=None):
        pieces = pieces or chunk_text(body)
        with self.lock:
            self.db.execute("DELETE FROM chunks WHERE note_id=?", (nid,))
            for i, p in enumerate(pieces):
                emb = _vec(embeddings[i]) if embeddings and i < len(embeddings) and embeddings[i] else None
                self.db.execute("INSERT INTO chunks(note_id, subject_id, text, embedding) VALUES(?,?,?,?)", (nid, subject_id, p, emb))
            self.db.commit()

    def note(self, nid: str):
        n = self.one("SELECT n.*, t.name AS topic FROM notes n LEFT JOIN topics t ON t.id=n.topic_id WHERE n.id=?", (nid,))
        if not n:
            raise StudyError("no such note", 404)
        return n

    def notes(self, subject_id: str | None = None, q: str = ""):
        sql = "SELECT n.id, n.subject_id, n.source_id, n.title, n.kind, n.updated, n.topic_id, t.name AS topic, substr(n.body,1,160) AS preview FROM notes n LEFT JOIN topics t ON t.id=n.topic_id"
        rows = self.q(sql + (" WHERE n.subject_id=?" if subject_id else "") + " ORDER BY n.updated DESC", (subject_id,) if subject_id else ())
        if q:
            w = words(q)
            rows = [r for r in rows if w & words(r["title"] + " " + r["preview"] + " " + (r["topic"] or ""))]
        return rows

    def edit_note(self, nid: str, patch: dict, embeddings=None):
        n = self.note(nid)
        title = " ".join(str(patch.get("title", n["title"])).split())[:120] or n["title"]
        body = str(patch.get("body", n["body"])).strip() or n["body"]
        tid = n["topic_id"]
        if "topic" in patch:
            tid = self.topic(n["subject_id"], patch["topic"]) if patch["topic"] else None
        self.run("UPDATE notes SET title=?, body=?, topic_id=?, updated=? WHERE id=?", (title, body[:60000], tid, now_iso(), nid))
        if body != n["body"]:
            self.set_chunks(nid, n["subject_id"], body, embeddings)
        return self.note(nid)

    def remove_note(self, nid: str):
        self.note(nid)
        self.run("DELETE FROM chunks WHERE note_id=?", (nid,))
        self.run("DELETE FROM notes WHERE id=?", (nid,))

    # -- cards
    def add_card(self, subject_id: str, front: str, back: str, state: str = "draft", topic: str | None = None,
                 note_id: str | None = None, image_id: str | None = None, source_id: str | None = None):
        front, back = str(front or "").strip()[:1500], str(back or "").strip()[:3000]
        if not front or not back:
            raise StudyError("a card needs a front and a back")
        if state not in CARD_STATES:
            raise StudyError("bad card state")
        cid = new_id("c")
        tid = self.topic(subject_id, topic) if topic else None
        self.run("INSERT INTO cards(id,subject_id,topic_id,note_id,front,back,image_id,state,due,created,source_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (cid, subject_id, tid, note_id, front, back, image_id, state, today().isoformat(), now_iso(), source_id))
        return self.card(cid)

    def card(self, cid: str):
        c = self.one("SELECT c.*, t.name AS topic FROM cards c LEFT JOIN topics t ON t.id=c.topic_id WHERE c.id=?", (cid,))
        if not c:
            raise StudyError("no such card", 404)
        return c

    def cards(self, subject_id=None, state=None, limit=500):
        sql, args = "SELECT c.*, t.name AS topic FROM cards c LEFT JOIN topics t ON t.id=c.topic_id WHERE 1=1", []
        if subject_id:
            sql += " AND c.subject_id=?"
            args.append(subject_id)
        if state:
            sql += " AND c.state=?"
            args.append(state)
        return self.q(sql + " ORDER BY c.created DESC LIMIT ?", (*args, limit))

    def edit_card(self, cid: str, patch: dict):
        c = self.card(cid)
        front = str(patch.get("front", c["front"])).strip() or c["front"]
        back = str(patch.get("back", c["back"])).strip() or c["back"]
        state = patch.get("state", c["state"])
        if state not in CARD_STATES:
            raise StudyError("bad card state")
        tid = c["topic_id"]
        if "topic" in patch:
            tid = self.topic(c["subject_id"], patch["topic"]) if patch["topic"] else None
        image = patch.get("image_id", c["image_id"])
        self.run("UPDATE cards SET front=?, back=?, state=?, topic_id=?, image_id=? WHERE id=?", (front[:1500], back[:3000], state, tid, image, cid))
        return self.card(cid)

    def remove_card(self, cid: str):
        self.card(cid)
        self.run("DELETE FROM reviews WHERE card_id=?", (cid,))
        self.run("DELETE FROM cards WHERE id=?", (cid,))

    def due(self, subject_id=None, limit=50, on: date | None = None):
        on = on or today()
        sql, args = "SELECT c.*, t.name AS topic FROM cards c LEFT JOIN topics t ON t.id=c.topic_id WHERE c.state='approved' AND c.due<=?", [on.isoformat()]
        if subject_id:
            sql += " AND c.subject_id=?"
            args.append(subject_id)
        return self.q(sql + " ORDER BY c.due, c.created LIMIT ?", (*args, limit))

    def review(self, cid: str, grade: int, on: date | None = None):
        c = self.card(cid)
        if c["state"] != "approved":
            raise StudyError("only approved cards can be reviewed", 409)
        nxt = schedule(c, int(grade), on or today())
        ahead = [t["date"] for t in self.tests(c["subject_id"]) if t["date"] > (on or today()).isoformat()]
        if ahead:                                        # never push a card beyond the next test of its subject: see it once more before
            last = date.fromisoformat(min(ahead)) - timedelta(days=1)
            due = min(date.fromisoformat(nxt["due"]), max(last, (on or today()) + timedelta(days=1)))
            nxt["due"], nxt["interval"] = due.isoformat(), max(1, (due - (on or today())).days) if grade else 0
        self.run("UPDATE cards SET interval=?, ease=?, reps=?, lapses=?, due=? WHERE id=?",
                 (nxt["interval"], nxt["ease"], nxt["reps"], nxt["lapses"], nxt["due"], cid))
        self.run("INSERT INTO reviews(card_id, grade, at, interval) VALUES(?,?,?,?)", (cid, int(grade), now_iso(), nxt["interval"]))
        return self.card(cid)

    def stats(self, subject_id=None, on: date | None = None):
        on = on or today()
        w, a = (" AND subject_id=?", (subject_id,)) if subject_id else ("", ())
        cnt = lambda state: self.one(f"SELECT COUNT(*) n FROM cards WHERE state=?{w}", (state, *a))["n"]  # noqa: E731
        since = (on - timedelta(days=30)).isoformat()
        rv = self.q("SELECT r.grade, r.at FROM reviews r JOIN cards c ON c.id=r.card_id WHERE r.at>=?" + w.replace("subject_id", "c.subject_id"), (since, *a))
        done_today = sum(1 for r in rv if r["at"][:10] == on.isoformat())
        days = {r["at"][:10] for r in rv}
        streak, d = 0, on
        while d.isoformat() in days or (d == on and streak == 0 and (on - timedelta(days=1)).isoformat() in days):
            if d.isoformat() in days:
                streak += 1
            d -= timedelta(days=1)
        return {"approved": cnt("approved"), "drafts": cnt("draft"), "suspended": cnt("suspended"),
                "due": len(self.due(subject_id, 10000, on)), "reviewed_today": done_today, "streak": streak,
                "retention": round(sum(1 for r in rv if r["grade"] >= 1) / len(rv), 3) if rv else None,
                "reviews_30d": len(rv)}

    # -- attempts
    def add_attempt(self, subject_id, topic, kind, prompt, answer, score, mx, feedback, scheme="", question_id=None,
                    hints=0, detail=""):
        aid = new_id("a")
        tid = self.topic(subject_id, topic) if topic else None
        self.run("INSERT INTO attempts(id,subject_id,topic_id,kind,prompt,answer,score,max,feedback,scheme,at,question_id,hints,detail) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (aid, subject_id, tid, kind, prompt[:4000], answer[:8000], float(score), float(mx), feedback[:4000], scheme[:4000],
                  now_iso(), question_id, int(hints), detail[:8000]))
        return self.one("SELECT * FROM attempts WHERE id=?", (aid,))

    def attempts(self, subject_id=None, limit=50):
        return self.q("SELECT a.*, t.name AS topic FROM attempts a LEFT JOIN topics t ON t.id=a.topic_id" +
                      (" WHERE a.subject_id=?" if subject_id else "") + " ORDER BY a.at DESC LIMIT ?", ((subject_id,) if subject_id else ()) + (limit,))

    # -- tests
    def add_test(self, subject_id, title, day, topics, plan_event_id=None):
        tid = new_id("x")
        self.run("INSERT INTO tests(id,subject_id,title,date,plan_event_id,topics,created) VALUES(?,?,?,?,?,?,?)",
                 (tid, subject_id, title[:120], day, plan_event_id, json.dumps(topics), now_iso()))
        return self.test(tid)

    def set_test_blocks(self, tid, blocks):
        self.run("UPDATE tests SET blocks=? WHERE id=?", (json.dumps([{"id": b["id"], "date": b["date"]} for b in blocks]), tid))

    def test(self, tid):
        t = self.one("SELECT * FROM tests WHERE id=?", (tid,))
        if not t:
            raise StudyError("no such test", 404)
        t["topics"] = json.loads(t["topics"] or "[]")
        t["blocks"] = json.loads(t.get("blocks") or "[]")
        return t

    def tests(self, subject_id=None):
        rows = self.q("SELECT id FROM tests" + (" WHERE subject_id=?" if subject_id else "") + " ORDER BY date", (subject_id,) if subject_id else ())
        return [self.test(r["id"]) for r in rows]

    def remove_test(self, tid):
        self.test(tid)
        self.run("DELETE FROM tests WHERE id=?", (tid,))

    # -- notebooks and boards (ink is stored as JSON strokes; the pen device owns the drawing)
    def add_notebook(self, subject_id, title):
        nid = new_id("b")
        ts = now_iso()
        self.run("INSERT INTO notebooks VALUES(?,?,?,?,?)", (nid, subject_id, " ".join(str(title or "Notebook").split())[:80], ts, ts))
        self.add_page(nid)
        return self.notebook(nid)

    def notebook(self, nid):
        nb = self.one("SELECT * FROM notebooks WHERE id=?", (nid,))
        if not nb:
            raise StudyError("no such notebook", 404)
        nb["pages"] = [{"id": p["id"], "idx": p["idx"], "background": p["background"]}
                       for p in self.q("SELECT id, idx, background FROM pages WHERE notebook_id=? ORDER BY idx", (nid,))]
        return nb

    def notebooks(self, subject_id=None):
        rows = self.q("SELECT id FROM notebooks" + (" WHERE subject_id=?" if subject_id else "") + " ORDER BY updated DESC", (subject_id,) if subject_id else ())
        return [self.notebook(r["id"]) for r in rows]

    def add_page(self, nid, background="lined"):
        self.notebook_exists(nid)
        if background not in ("lined", "grid", "blank", "dots"):
            raise StudyError("background: lined, grid, dots or blank")
        idx = (self.one("SELECT MAX(idx) m FROM pages WHERE notebook_id=?", (nid,))["m"] or 0) + 1
        pid = new_id("p")
        self.run("INSERT INTO pages VALUES(?,?,?,?,?,?)", (pid, nid, idx, background, "[]", now_iso()))
        return pid

    def notebook_exists(self, nid):
        if not self.one("SELECT 1 FROM notebooks WHERE id=?", (nid,)):
            raise StudyError("no such notebook", 404)

    def page(self, pid):
        p = self.one("SELECT * FROM pages WHERE id=?", (pid,))
        if not p:
            raise StudyError("no such page", 404)
        p["strokes"] = json.loads(p["strokes"] or "[]")
        return p

    def save_page(self, pid, strokes, background=None):
        self.page(pid)
        if not isinstance(strokes, list) or len(strokes) > 20000:
            raise StudyError("strokes must be a list")
        clean = []
        for s in strokes:
            pts = s.get("pts") if isinstance(s, dict) else None
            if not isinstance(pts, list) or not pts:
                continue
            try:
                clean.append({"c": str(s.get("c", "#e8e4d8"))[:9], "w": float(s.get("w", 2)),
                              "pts": [[float(p[0]), float(p[1])] for p in pts[:5000]], "k": str(s.get("k", "pen"))[:8]})
            except (TypeError, ValueError, IndexError, KeyError):
                raise StudyError("a stroke is malformed")
        self.run("UPDATE pages SET strokes=?, updated=? WHERE id=?", (json.dumps(clean), now_iso(), pid))
        if background:
            if background not in ("lined", "grid", "blank", "dots"):
                raise StudyError("background: lined, grid, dots or blank")
            self.run("UPDATE pages SET background=? WHERE id=?", (background, pid))
        nb = self.page(pid)["notebook_id"]
        self.run("UPDATE notebooks SET updated=? WHERE id=?", (now_iso(), nb))
        return self.page(pid)

    def remove_page(self, pid):
        p = self.page(pid)
        if self.one("SELECT COUNT(*) n FROM pages WHERE notebook_id=?", (p["notebook_id"],))["n"] <= 1:
            raise StudyError("a notebook keeps at least one page", 409)
        self.run("DELETE FROM pages WHERE id=?", (pid,))

    def remove_notebook(self, nid):
        self.notebook_exists(nid)
        self.run("DELETE FROM pages WHERE notebook_id=?", (nid,))
        self.run("DELETE FROM notebooks WHERE id=?", (nid,))

    def add_board(self, subject_id, title):
        bid = new_id("d")
        ts = now_iso()
        self.run("INSERT INTO boards VALUES(?,?,?,?,?)", (bid, subject_id, " ".join(str(title or "Board").split())[:80], ts, ts))
        return self.board(bid)

    BOARD_KINDS = ("stroke", "text", "sticky", "shape", "connector", "image", "pdf_page", "link")

    def board(self, bid):
        b = self.one("SELECT * FROM boards WHERE id=?", (bid,))
        if not b:
            raise StudyError("no such board", 404)
        b["items"] = [{**i, "data": json.loads(i["data"] or "{}")} for i in self.q("SELECT * FROM board_items WHERE board_id=? ORDER BY rowid", (bid,))]
        return b

    def boards(self, subject_id=None):
        return self.q("SELECT id, subject_id, title, updated FROM boards" + (" WHERE subject_id=?" if subject_id else "") + " ORDER BY updated DESC", (subject_id,) if subject_id else ())

    def put_item(self, bid, item: dict):
        self.board(bid)
        kind = item.get("kind")
        if kind not in self.BOARD_KINDS:
            raise StudyError("item kind: " + ", ".join(self.BOARD_KINDS))
        iid = str(item.get("id") or new_id("i"))[:20]
        other = self.one("SELECT board_id FROM board_items WHERE id=?", (iid,))
        if other and other["board_id"] != bid:             # ids are global: never move another board's item
            raise StudyError("that item belongs to another board", 409)
        data = json.dumps(item.get("data") or {})
        if len(data) > 400000:
            raise StudyError("that item is too big")
        try:
            vals = [float(item.get(k, 0) or 0) for k in ("x", "y", "w", "h")]
        except (TypeError, ValueError):
            raise StudyError("bad item size or position")
        self.run("INSERT OR REPLACE INTO board_items VALUES(?,?,?,?,?,?,?,?,?)", (iid, bid, kind, *vals, data, now_iso()))
        self.run("UPDATE boards SET updated=? WHERE id=?", (now_iso(), bid))
        return next(i for i in self.board(bid)["items"] if i["id"] == iid)

    def remove_item(self, bid, iid):
        self.board(bid)
        self.run("DELETE FROM board_items WHERE id=? AND board_id=?", (iid, bid))

    def remove_board(self, bid):
        self.board(bid)
        self.run("DELETE FROM board_items WHERE board_id=?", (bid,))
        self.run("DELETE FROM boards WHERE id=?", (bid,))

    # -- sources
    def add_source(self, subject_id, title, kind, file_id):
        sid = new_id("r")
        self.run("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)", (sid, subject_id, title[:120], kind, file_id, "processing", "", now_iso()))
        return self.source(sid)

    def source(self, sid):
        s = self.one("SELECT * FROM sources WHERE id=?", (sid,))
        if not s:
            raise StudyError("no such source", 404)
        return s

    def sources(self, subject_id=None):
        return self.q("SELECT * FROM sources" + (" WHERE subject_id=?" if subject_id else "") + " ORDER BY created DESC", (subject_id,) if subject_id else ())

    def outputs(self, sid):
        self.source(sid)
        return {r["kind"]: r["body"] for r in self.q("SELECT kind, body FROM source_outputs WHERE source_id=?", (sid,))}

    def set_output(self, sid, kind, body):
        self.run("INSERT OR REPLACE INTO source_outputs VALUES(?,?,?)", (sid, kind, body))

    def remove_source(self, sid):
        s = self.source(sid)
        for n in self.q("SELECT id FROM notes WHERE source_id=?", (sid,)):
            self.remove_note(n["id"])
        self.drop_made(sid)
        self.run("DELETE FROM source_outputs WHERE source_id=?", (sid,))
        self.run("DELETE FROM sources WHERE id=?", (sid,))
        if s.get("file_id"):
            self.run("DELETE FROM files WHERE id=?", (s["file_id"],))
            (self.folder / "files" / s["file_id"]).unlink(missing_ok=True)

    def drop_made(self, sid):
        """The cards and questions an import made (undoing it, or reading it again)."""
        for c in self.q("SELECT id FROM cards WHERE source_id=?", (sid,)):
            self.remove_card(c["id"])
        for q in self.q("SELECT figure FROM questions WHERE source_id=? AND figure IS NOT NULL AND figure!=''", (sid,)):
            src = self.one("SELECT file_id FROM sources WHERE id=?", (sid,))
            if not src or q["figure"] != src["file_id"]:
                self.run("DELETE FROM files WHERE id=?", (q["figure"],))
                (self.folder / "files" / q["figure"]).unlink(missing_ok=True)
        self.run("DELETE FROM questions WHERE source_id=?", (sid,))

    def move_source(self, sid, subject_id):
        """Wrong subject: the source and everything it made go to another subject, keeping their topic names."""
        self.source(sid)
        if not self.one("SELECT 1 FROM subjects WHERE id=?", (subject_id,)):
            raise StudyError("no such subject", 404)
        old = set()
        def moved(tid):
            t = self.one("SELECT name FROM topics WHERE id=?", (tid,)) if tid else None
            if t:
                old.add(tid)
            return self.topic(subject_id, t["name"]) if t else None
        for table, where in (("notes", "source_id=?"), ("cards", "source_id=?"), ("questions", "source_id=?")):
            for r in self.q(f"SELECT id, topic_id FROM {table} WHERE {where}", (sid,)):
                self.run(f"UPDATE {table} SET subject_id=?, topic_id=? WHERE id=?", (subject_id, moved(r["topic_id"]), r["id"]))
        self.run("UPDATE chunks SET subject_id=? WHERE note_id IN (SELECT id FROM notes WHERE source_id=?)", (subject_id, sid))
        self.run("UPDATE sources SET subject_id=? WHERE id=?", (subject_id, sid))
        for tid in old:                                 # a topic the move emptied does not stay behind as a ghost
            if not any(self.one(f"SELECT 1 FROM {t} WHERE topic_id=?", (tid,)) for t in ("notes", "cards", "questions", "attempts", "homework")):
                self.run("DELETE FROM topics WHERE id=?", (tid,))
        return self.source(sid)

    # -- search
    def search(self, qvec, text: str, subject_id=None, k=5):
        """Chunks ranked by cosine similarity to the query vector; plain word overlap when there is no vector."""
        rows = self.q("SELECT ch.id, ch.note_id, ch.text, ch.embedding, n.title, n.subject_id FROM chunks ch JOIN notes n ON n.id=ch.note_id" +
                      (" WHERE ch.subject_id=?" if subject_id else ""), (subject_id,) if subject_id else ())
        scored = []
        qw = words(text)
        if qvec is not None:
            import numpy as np
            qv = np.asarray(qvec, dtype="float32")
            qn = float(np.linalg.norm(qv)) or 1.0
        for r in rows:
            s = 0.0
            if qvec is not None and r["embedding"]:
                v = _unvec(r["embedding"])
                if v.shape == qv.shape:
                    s = float(np.dot(qv, v) / (qn * (float(np.linalg.norm(v)) or 1.0)))
            elif qw:
                s = len(qw & words(r["text"])) / math.sqrt(len(qw) * max(1, len(words(r["text"]))))
            if s > 0:
                scored.append({"note_id": r["note_id"], "title": r["title"], "text": r["text"], "score": round(s, 4), "subject_id": r["subject_id"]})
        scored.sort(key=lambda x: -x["score"])
        return scored[:k]


# ---- Gemini ----------------------------------------------------------------------------------------------
class GeminiStudyAI:
    """The real model. Built lazily so importing the server never needs a key."""

    def __init__(self, model=STUDY_MODEL, embed_model=EMBED_MODEL, client=None):
        self.model, self.embed_model, self._client = model, embed_model, client

    def _cl(self):
        if self._client is None:
            from google import genai
            from ..keys import load_gemini_key
            key = load_gemini_key()
            if not key:
                raise StudyError("Gemini is not set up on the server (no GEMINI_API_KEY)", 503)
            self._client = genai.Client(api_key=key)
        return self._client

    def generate(self, system: str, prompt: str, parts=()) -> str:
        from google.genai import types
        import time
        contents = [types.Part.from_bytes(data=p["data"], mime_type=p["mime"]) for p in parts] + [prompt]
        last = None
        for attempt in range(3):                       # a rate limit or a hiccup is retried, so a typed answer is not lost to it
            try:
                res = self._cl().models.generate_content(model=self.model, contents=contents,
                                                         config=types.GenerateContentConfig(system_instruction=system, max_output_tokens=8192,
                                                                                            http_options=types.HttpOptions(timeout=90_000)))
                return (res.text or "").strip()
            except StudyError:
                raise
            except Exception as e:
                last = e
                msg = str(e)
                retryable = any(c in msg for c in ("429", "500", "502", "503", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "timed out", "Timeout"))
                if not retryable or attempt == 2:
                    break
                time.sleep(2 * (attempt + 1))
        raise StudyError(f"Gemini could not answer ({type(last).__name__}). Try again in a minute.", 502)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for i in range(0, len(texts), 64):
            try:
                res = self._cl().models.embed_content(model=self.embed_model, contents=texts[i:i + 64])
            except StudyError:
                raise
            except Exception as e:
                raise StudyError(f"embedding failed ({type(e).__name__})", 502)
            out += [list(e.values) for e in res.embeddings]
        return out


SYSTEM = ("You are Cyph's study engine for a Singapore secondary school student (Sec 3, working towards O-Level). "
          "Be accurate and syllabus-aware. Use plain text; write maths as LaTeX between $...$ and chemistry with \\ce{...}. "
          "Reply with exactly the format asked for and nothing else.")

MATH_COMMON = (" Singapore O-Level maths conventions: give a non-exact answer to 3 significant figures, an angle in degrees to 1 decimal place and money to "
               "2 decimal places, unless the question says otherwise; keep exact values (surds, fractions, multiples of pi) when asked; always give units. "
               "Mark scheme types: M = method, A = accuracy (only earned if the M before it is earned), B = independent mark. Follow-through (ft) is allowed: "
               "a correct method on a wrong earlier value still earns its M marks and an ft A mark. Geometry answers need the reason in words (for example "
               "angles in the same segment). Bearings are three figures. Never accept a final answer without working when the question says show or find.")
EMATH = " This is O-Level Elementary Mathematics (E-Math)."
AMATH = (" This is O-Level Additional Mathematics (A-Math). Exact forms are expected (surds, ln, e, pi). Give angles in radians when the question uses pi, "
         "otherwise degrees. For a 'show that' or 'prove' question the final result is GIVEN, so mark only the working that gets to it.")
SCIENCE = (" Science marking: units are required, numerical answers to 2 or 3 significant figures, command words matter (state = one fact; describe = what "
           "happens; explain = cause and effect with the science idea; calculate = formula, substitution, answer with unit). Chemical equations must balance "
           "with state symbols when asked.")
HUMANITIES = (" Humanities marking: structure each answer as point, evidence, explanation, link back to the question. In source-based questions use the "
              "source itself, its provenance (who, when, why), cross-reference with other sources, and a final judgement. In geography, place-specific case study "
              "facts earn marks; vague general statements do not. Never invent a source, a quotation or a case study.")
ENGLISH = " English marking: judge task fulfilment, organisation, language accuracy and style; give the level and one concrete next step. Never invent quotations."


def subject_rules(name: str) -> str:
    n = str(name or "").strip().lower().replace("-", "").replace(" ", "")
    if n in ("amath", "additionalmathematics", "addmaths", "amaths"):
        return MATH_COMMON + AMATH
    if "math" in n:
        return MATH_COMMON + EMATH
    if n in ("chemistry", "physics", "biology", "science", "chem", "phy", "bio"):
        return SCIENCE
    if n in ("ss", "socialstudies", "geog", "geography", "history", "hist"):
        return HUMANITIES
    if n.startswith("english") or n in ("eng", "el", "literature"):
        return ENGLISH
    return ""


def sysfor(name: str = "") -> str:
    """The system prompt for one subject: the general one plus that subject's marking conventions."""
    return SYSTEM + (f" Subject: {name}." if name else "") + subject_rules(name)


QUESTION_SHAPE = """{"ref": its number as printed ("Q4", "2(b)") or "", "text": the question, "marks": total marks, "topic": its syllabus topic,
   "answer": the final answer, "key": "printed" if the answer comes from an answer key or worked solution in the material, else "cyph",
   "scheme": [{"point": one thing that earns marks, "marks": 1, "type": "M" method, "A" accuracy or "B" independent (maths and sciences; else ""),
                "final": true only on the point that is the final answer}],
   "answers": {"kind": "number" (one value), "set" (several values, e.g. both roots), "interval" (an inequality), "expression" (algebra) or "text",
               "values": [every correct final value as a plain number: sqrt(12) is 3.4641, pi/6 is 0.5236; [] if it is not a number],
               "text": the final answer as written, like "x < -1 or x > 3" or "3x(x-2)", "sf": significant figures asked (3 if the question says nothing and
               the answer is not exact) or null, "dp": decimal places asked or null, "exact": true if an exact form is wanted},
   "show": true if it is a show that / prove / verify question,
   "numeric": the final answer as a plain number or null, "unit": its unit or "", "figure": true if it cannot be answered without a
   diagram, graph, map or picture, "page": the page it is on (1 for a photo),
   "quick": {"ask": the same question cut down so it can be answered by tapping, "options": 3 or 4 short options, "correct": index of the
   right option, "final_label": what the numeric answer is called ("Mass of hydrogen") or "", "explain": one sentence on why}}"""

IMPORT_PROMPT = """Turn the attached study material into revision material. Reply with ONE JSON object:
{"title": short title, "kind": "notes", "worksheet" or "paper", "subject": the school subject it belongs to,
 "topic": the single syllabus topic it belongs to,
 "clean_notes": the content rewritten as clear, well organised notes in markdown (keep every fact, drop the clutter),
 "summary": five sentences at most,
 "key_terms": [{"term": ..., "definition": ...}],
 "study_guide": a short step by step plan to learn it,
 "cards": up to 12 flashcards [{"front": a question testing ONE fact or step, "back": the short answer}], facts from the material only,
 "questions": every question PRINTED in the material (at most 20), copied word for word, never invented, each as
  """ + QUESTION_SHAPE + """}
If the material prints no questions, "questions" is []. Never make up a source, a quotation or a case study."""


class StudyService:
    def __init__(self, store: StudyStore, ai, planner=None, changed=None, background=True):
        self.store, self.ai, self.planner = store, ai, planner
        self.changed = changed or (lambda what: None)
        self.background = background
        from .session import Engine
        self.engine = Engine(self)                      # sessions, marking, strength: the one START button
        self._active: set[str] = set()                  # sources being read right now: one read at a time per source
        self._pool = ThreadPoolExecutor(max_workers=2)  # at most two model reads at once, the rest wait

    # -- import
    def import_source(self, subject_ref, title, mime, data: bytes | None = None, text: str = ""):
        subj = self.store.subject(subject_ref) or {"id": ""}      # no subject given: the import works it out
        if mime not in MIME_OK:
            raise StudyError("PDF, picture (PNG/JPEG/WebP) or text only")
        if mime == "text/plain":
            data = (text or (data or b"").decode("utf-8", "replace")).encode()
        if not data:
            raise StudyError("that file is empty")
        fid = self.store.put_file(title or "upload", mime, data)
        kind = "pdf" if mime == "application/pdf" else "text" if mime == "text/plain" else "photo"
        src = self.store.add_source(subj["id"], title or "Untitled source", kind, fid)
        self._start(src["id"])
        return self.store.source(src["id"])

    def _start(self, sid: str):
        if self.background:
            self._pool.submit(self.process, sid)
        else:
            self.process(sid)

    def retry(self, sid: str):
        """Read a failed (or finished) source again. A source already being read is left alone."""
        self.store.source(sid)
        with self.store.lock:
            if sid in self._active:
                raise StudyError("that one is still being read", 409)
            self.store.run("UPDATE sources SET status='processing', error='' WHERE id=?", (sid,))
        self.changed("study")
        self._start(sid)
        return self.store.source(sid)

    def process(self, sid: str):
        with self.store.lock:
            if sid in self._active:
                return None
            self._active.add(sid)
        try:
            return self._process(sid)
        finally:
            self._active.discard(sid)

    def _process(self, sid: str):
        st = self.store
        src = st.source(sid)
        try:
            meta, data = st.get_file(src["file_id"])
            parts = [] if meta["mime"] == "text/plain" else [{"mime": meta["mime"], "data": data}]
            prompt = IMPORT_PROMPT + ("\n\nMaterial:\n" + data.decode("utf-8", "replace")[:30000] if not parts else "")
            names = [x["name"] for x in st.subjects()]
            known = st.one("SELECT name FROM subjects WHERE id=?", (src["subject_id"],))
            raw = self.ai.generate(sysfor(known["name"]) if known else SYSTEM + " His subjects: " + ", ".join(names) + ".", prompt, parts)
            got = parse_json(raw)
            if not isinstance(got, dict) or not str(got.get("clean_notes") or "").strip():
                raise StudyError("I could not read that material")
            if not known:                                # filed by what the material says, else the first subject
                guess = str(got.get("subject") or "").strip().lower()
                pick = next((x for x in st.subjects() if x["name"].lower() == guess), None) or \
                    next((x for x in st.subjects() if guess and (guess in x["name"].lower() or x["name"].lower() in guess)), None) or st.subjects()[0]
                st.run("UPDATE sources SET subject_id=? WHERE id=?", (pick["id"], sid))
                src = st.source(sid)
            for k in ("summary", "study_guide"):
                st.set_output(sid, k, str(got.get(k) or ""))
            st.set_output(sid, "key_terms", json.dumps(got.get("key_terms") or []))
            st.set_output(sid, "likely_questions", json.dumps(got.get("likely_questions") or []))
            st.set_output(sid, "clean_notes", str(got["clean_notes"]))
            title = str(got.get("title") or src["title"])
            pieces = chunk_text(str(got["clean_notes"]))
            try:
                embs = self.ai.embed(pieces)
            except StudyError:
                embs = None                             # notes are still saved; search falls back to words
            for old in st.q("SELECT id FROM notes WHERE source_id=?", (sid,)):      # a retry replaces, never duplicates
                st.remove_note(old["id"])
            st.drop_made(sid)
            note = st.add_note(src["subject_id"], title, str(got["clean_notes"]), "import", sid, got.get("topic"), embs)
            for c in (got.get("cards") or [])[:12]:      # kept straight away: nothing waits for a yes
                if isinstance(c, dict) and str(c.get("front") or "").strip() and str(c.get("back") or "").strip():
                    st.add_card(src["subject_id"], c["front"], c["back"], "approved", got.get("topic"), note["id"], source_id=sid)
            from .session import save_questions
            save_questions(st, src, got.get("questions") or [], got.get("topic"), meta, data)
            st.run("UPDATE sources SET status='ready', error='', title=? WHERE id=?", (title, sid))
            self.changed("study")
            return note
        except Exception as e:                           # a failed import is shown on the source, not raised
            msg = str(e) if isinstance(e, StudyError) else f"import failed ({type(e).__name__})"
            st.run("UPDATE sources SET status='failed', error=? WHERE id=?", (msg[:300], sid))
            self.changed("study")
            return None

    # -- notes
    def save_note(self, subject_ref, title, body, kind="typed", topic=None):
        subj = self.store.subject(subject_ref)
        if not subj:
            raise StudyError("pick a subject first")
        pieces = chunk_text(body)
        embs = self._embed_quiet(pieces)
        note = self.store.add_note(subj["id"], title, body, kind, None, topic, embs)
        self.changed("study")
        return note

    def update_note(self, nid, patch):
        body = patch.get("body")
        embs = self._embed_quiet(chunk_text(body)) if body else None
        out = self.store.edit_note(nid, patch, embs)
        self.changed("study")
        return out

    def _embed_quiet(self, pieces):
        try:
            return self.ai.embed(pieces) if pieces else None
        except StudyError:
            return None

    # -- search
    def search(self, query, subject_ref=None, k=5):
        query = str(query or "").strip()
        if not query:
            raise StudyError("ask for something")
        subj = self.store.subject(subject_ref)
        vec = None
        try:
            vec = self.ai.embed([query])[0]
        except StudyError:
            pass
        return self.store.search(vec, query, subj["id"] if subj else None, max(1, min(int(k), 20)))

    def reindex(self):
        """Embed every chunk that has no vector (after an outage or a model change). Returns how many were done."""
        rows = self.store.q("SELECT id, text FROM chunks WHERE embedding IS NULL")
        done = 0
        for i in range(0, len(rows), 64):
            batch = rows[i:i + 64]
            vecs = self.ai.embed([r["text"] for r in batch])
            for r, v in zip(batch, vecs):
                self.store.run("UPDATE chunks SET embedding=? WHERE id=?", (_vec(v), r["id"]))
                done += 1
        return done

    # -- cards
    def draft_cards(self, subject_ref=None, note_id=None, text="", n=8, topic=None):
        n = max(1, min(int(n), 20))
        if note_id:
            note = self.store.note(note_id)
            text, subject_id, topic = note["body"], note["subject_id"], topic or note["topic"]
        else:
            subj = self.store.subject(subject_ref)
            if not subj or not str(text).strip():
                raise StudyError("give a note or some text")
            subject_id = subj["id"]
        prompt = (f"Write up to {n} flashcards from this material. Each tests ONE fact or step, front is a question, back is "
                  'the short answer. Reply with a JSON array of {"front": ..., "back": ...}.\n\nMaterial:\n' + str(text)[:12000])
        got = parse_json(self.ai.generate(SYSTEM, prompt))
        if not isinstance(got, list):
            raise StudyError("I could not make cards from that")
        out = []
        for c in got[:n]:
            if isinstance(c, dict) and str(c.get("front", "")).strip() and str(c.get("back", "")).strip():
                out.append(self.store.add_card(subject_id, c["front"], c["back"], "draft", topic, note_id))
        if not out:
            raise StudyError("I could not make cards from that")
        self.changed("study")
        return out

    # -- tutoring: the hint ladder
    def tutor(self, question, subject_ref=None, step=0, history="", images=(), key=""):
        step = max(0, min(int(step), len(HINT_STEPS) - 1))
        question = str(question or "").strip()
        if not question and not images:
            raise StudyError("ask a question")
        if not question:
            question = "(the question is in the attached photo: read it first)"
        subj = self.store.subject(subject_ref)
        cites = self.search(question, subject_ref, 3) if self.store.q("SELECT 1 FROM chunks LIMIT 1") else []
        ctx = "\n\n".join(f"[{i + 1}] {c['title']}: {c['text']}" for i, c in enumerate(cites))
        stage = {"restate": "Restate what the question is really asking in one or two sentences and say what topic it tests. Do NOT hint at the method.",
                 "nudge": "Give ONE nudge: the idea or formula to think about. Do not do any working.",
                 "first_step": "Show only the first step of the working and then stop, asking him to do the next one.",
                 "solution": "Give the full worked solution, step by step, and name the topic."}[HINT_STEPS[step]]
        prompt = (f"Question from the student: {question}\n" + (f"Earlier hints:\n{history}\n" if history else "") +
                  (f"His own notes (cite as [n] when used):\n{ctx}\n" if ctx else "") + (f"\nThe verified answer is: {key}. Use it to stay correct, and NEVER state it before the solution stage.\n" if key else "") +
                  f"\nStage: {stage}\nKeep it under 120 words unless it is the solution.")
        reply = self.ai.generate(sysfor(subj["name"] if subj else ""), prompt, list(images))
        return {"step": step, "stage": HINT_STEPS[step], "text": reply, "last": step == len(HINT_STEPS) - 1,
                "cites": [{"n": i + 1, "note_id": c["note_id"], "title": c["title"]} for i, c in enumerate(cites)]}

    # -- practice and marking
    KIND_HELP = {"worked": "a worked-solution style question with a clear final answer",
                 "structured": "a structured question with parts (a), (b), (c) and mark allocations",
                 "sbq": "a source-based question: include a short source (quoted text) and 2-3 questions on it",
                 "essay": "an essay question of the kind set at O-Level",
                 "paper": "one question in the style of a past O-Level paper"}

    def practice(self, subject_ref, topic=None, kind="structured"):
        if kind not in PRACTICE_KINDS:
            raise StudyError("kind: " + ", ".join(PRACTICE_KINDS))
        subj = self.store.subject(subject_ref)
        if not subj:
            raise StudyError("pick a subject")
        if not topic:                                   # Cyph decides: the weakest topic, else any
            weak = self.store.weak_topics(subj["id"], 1)
            topic = weak[0]["name"] if weak else None
        notes = [n["body"][:1500] for n in self.store.q("SELECT body FROM notes WHERE subject_id=? ORDER BY updated DESC LIMIT 2", (subj["id"],))] if topic else []
        prompt = (f"Write {self.KIND_HELP[kind]} for {subj['name']}" + (f", topic: {topic}" if topic else "") +
                  ". Reply with JSON: {\"question\": ..., \"marks\": total marks, \"scheme\": the marking scheme (points that earn marks), \"topic\": topic name}."
                  + ("\nHis notes, for the level of detail:\n" + "\n---\n".join(notes) if notes else ""))
        got = parse_json(self.ai.generate(SYSTEM, prompt))
        if not isinstance(got, dict) or not str(got.get("question", "")).strip():
            raise StudyError("I could not set a question")
        pid = new_id("q")
        item = {"id": pid, "subject_id": subj["id"], "kind": kind, "question": str(got["question"]),
                "marks": max(1, min(100, _int(got.get("marks"), 4))), "scheme": str(got.get("scheme") or ""),
                "topic": str(got.get("topic") or topic or "General")}
        self.store.put_practice(item)
        return {k: v for k, v in item.items() if k != "scheme"}

    def mark(self, pid, answer, images=(), scheme_override=""):
        item = self.store.get_practice(pid)
        if not item:
            raise StudyError("that question has expired; ask for a new one", 404)
        answer = str(answer or "").strip()
        if not answer and not images:
            raise StudyError("write an answer first")
        scheme = scheme_override or item["scheme"]
        prompt = (f"Question ({item['marks']} marks): {item['question']}\nMarking scheme: {scheme}\nStudent answer: {answer or '(see the picture)'}\n"
                  'Mark it like an O-Level examiner. Reply JSON: {"score": marks awarded (number), "feedback": what earned and lost marks, in at most 80 words, "next": one thing to do next}.')
        got = parse_json(self.ai.generate(SYSTEM, prompt, list(images)))
        if not isinstance(got, dict) or not isinstance(got.get("score"), (int, float)):
            raise StudyError("I could not mark that, try again")
        score = max(0.0, min(float(got["score"]), float(item["marks"])))
        att = self.store.add_attempt(item["subject_id"], item["topic"], item["kind"], item["question"], answer,
                                     score, item["marks"], str(got.get("feedback") or ""), scheme)
        self.store.drop_practice(pid)
        self.changed("study")
        return {"attempt": att, "score": score, "marks": item["marks"], "feedback": att["feedback"], "next": str(got.get("next") or "")}

    def mark_paper(self, subject_ref, question, answer, scheme, marks=4, topic=None, images=()):
        """Past paper question typed or photographed by him, marked against the scheme he uploaded (if any)."""
        subj = self.store.subject(subject_ref)
        if not subj:
            raise StudyError("pick a subject")
        pid = new_id("q")
        self.store.put_practice({"id": pid, "subject_id": subj["id"], "kind": "paper", "question": str(question)[:4000],
                                    "marks": max(1, int(marks)), "scheme": str(scheme or "Use the usual O-Level scheme for this question."),
                                    "topic": topic or "General"})
        return self.mark(pid, answer, images)

    # -- tests and revision blocks
    def plan_test(self, subject_ref, title, day, topics=(), blocks=(7, 3, 1), time="16:00", minutes=45):
        subj = self.store.subject(subject_ref)
        if not subj:
            raise StudyError("pick a subject")
        if self.planner is None:
            raise StudyError("the planner is not available", 503)
        try:
            when = date.fromisoformat(day)
        except ValueError:
            raise StudyError("dates look like 2026-11-20")
        topics = [t for t in (topics or []) if str(t).strip()] or [w["name"] for w in self.store.weak_topics(subj["id"], 3)]
        ev = self.planner.create({"title": f"{subj['name']} test: {title}", "date": day, "kind": "test", "project": "Study",
                                  "notes": ", ".join(topics)[:400]}, today())["event"]
        test = self.store.add_test(subj["id"], title, day, list(topics), ev["id"])
        made = []
        for b in blocks:
            d = when - timedelta(days=int(b))
            if d < today():
                continue
            focus = topics[len(made) % len(topics)] if topics else "everything"
            res = self.planner.create({"title": f"Revise {subj['name']}: {focus}", "date": d.isoformat(), "time": time,
                                       "duration_min": minutes, "kind": "block", "project": "Study"}, today())
            made.append({"id": res["event"]["id"], "date": d.isoformat(), "conflicts": res["conflicts"]})
        self.store.set_test_blocks(test["id"], made)
        self.changed("study")
        self.changed("plan")
        return {"test": self.store.test(test["id"]), "blocks": made}

    def remove_test(self, tid):
        t = self.store.test(tid)
        if self.planner is not None and t.get("plan_event_id"):
            self.planner.remove([t["plan_event_id"], *[b["id"] for b in t.get("blocks", [])]])
        self.store.remove_test(tid)
        self.changed("study")
        self.changed("plan")

    # -- the front page of Study
    def today(self):
        out = []
        for s in self.store.subjects():
            st = self.store.stats(s["id"])
            out.append({**s, "due": st["due"], "drafts": st["drafts"], "approved": st["approved"]})
        up = [t for t in self.store.tests() if t["date"] >= today().isoformat()]
        return {"subjects": out, "stats": self.store.stats(), "weak": self.store.weak_topics(None, 5), "tests": up[:5]}

    # -- notebook export
    def notebook_pdf(self, nid) -> bytes:
        from PIL import Image, ImageColor, ImageDraw
        nb = self.store.notebook(nid)
        W, H = 1000, 1414
        imgs = []
        for p in nb["pages"]:
            page = self.store.page(p["id"])
            im = Image.new("RGB", (W, H), "white")
            dr = ImageDraw.Draw(im)
            bg = page["background"]
            if bg in ("lined", "grid"):
                for y in range(80, H, 40):
                    dr.line([(0, y), (W, y)], fill="#cfd8e3", width=1)
            if bg == "grid":
                for x in range(0, W, 40):
                    dr.line([(x, 0), (x, H)], fill="#cfd8e3", width=1)
            if bg == "dots":
                for y in range(40, H, 40):
                    for x in range(40, W, 40):
                        dr.ellipse([x - 1, y - 1, x + 1, y + 1], fill="#9aa5b1")
            for s in page["strokes"]:
                pts = [tuple(q) for q in s["pts"]]
                col = s["c"] if s["c"].lower() not in ("#e8e4d8", "#fff", "#ffffff") else "#222222"
                try:
                    ImageColor.getrgb(col)
                except ValueError:
                    col = "#222222"
                if len(pts) == 1:
                    x, y = pts[0]
                    dr.ellipse([x - s["w"], y - s["w"], x + s["w"], y + s["w"]], fill=col)
                else:
                    dr.line(pts, fill=col, width=max(1, int(s["w"])), joint="curve")
            imgs.append(im)
        buf = io.BytesIO()
        imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:], resolution=100)
        return buf.getvalue()
