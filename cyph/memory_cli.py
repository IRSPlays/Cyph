"""`python -m cyph memory` (show what Cyph knows) and `python -m cyph memory-import --path EXPORT`
(learn from a Gemini app export). The import is reviewed before it touches live memory:

    memory-import --path X            writes imports/draft/*.md only; read them, edit them
    memory-import --path X --apply    also adds the draft entries to live memory (same checks as chat)

The export is read locally and sent to Gemini Flash-Lite (the key from GEMINI_API_KEY / cyph/.env) in
chunks, only your own prompts plus a short slice of each reply. Contents are never printed here."""
from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

from .memory import FILES, Curator, MemoryStore, parse_json

CHUNK = 40_000          # characters per Gemini call
MAX_CHUNKS = 40         # safety: a huge export is sampled, and you are told
REPLY_KEEP = 300


def _strip(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", s, flags=re.I)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


_DATE = re.compile(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4}), \d{1,2}:\d{2}:\d{2}\s?[AP]M [A-Z]{2,4}")
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
PROMPT_KEEP = 1500


def parse_takeout_activity(raw: str) -> list[str]:
    """Google Takeout 'Gemini Apps/MyActivity.html': one outer-cell per exchange; the first content cell is
    'Prompted <what he typed>', optional 'Attached N files', the date, then the reply as HTML."""
    out = []
    for cell in raw.split('<div class="outer-cell')[1:]:
        try:
            first = cell.split('<div class="content-cell', 1)[1].split("</div>", 1)[0]
        except IndexError:
            continue
        first = first.split(">", 1)[1] if ">" in first else first
        m = _DATE.search(first)
        head, tail = (first[:m.start()], first[m.end():]) if m else (first, "")
        text = _strip(head)
        if not text.startswith("Prompted"):
            continue
        prompt = re.sub(r"\s*Attached \d+ files?.*$", "", text[len("Prompted"):].strip(), flags=re.S)
        if not prompt:
            continue
        when = f"{m.group(3)}-{_MONTHS.get(m.group(1), 0):02d}-{int(m.group(2)):02d}" if m else ""
        reply = _strip(tail)[:150]
        out.append(f"[{when}] Haziq: {prompt[:PROMPT_KEEP]}" + (f"\nGemini (excerpt): {reply}" if reply else ""))
    return out


def parse_export(path: Path) -> list[str]:
    """Takeout 'My Activity' JSON or HTML, or any text/markdown/JSON dump -> one text per exchange."""
    files = [path] if path.is_file() else sorted(
        p for p in path.rglob("*") if p.suffix.lower() in (".json", ".html", ".htm", ".md", ".txt"))
    out = []
    for f in files:
        raw = f.read_text(encoding="utf-8", errors="ignore")
        if f.suffix.lower() == ".json":
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            for it in data if isinstance(data, list) else [data]:
                if not isinstance(it, dict):
                    continue
                title = str(it.get("title") or "")
                if not title and not it.get("safeHtmlItem"):
                    continue
                prompt = re.sub(r"^(Prompted|Asked)\s+", "", title)
                reply = " ".join(_strip(x.get("html", "")) for x in it.get("safeHtmlItem") or []
                                 if isinstance(x, dict))
                when = str(it.get("time") or "")[:10]
                out.append(f"[{when}] Haziq: {prompt}" + (f"\nGemini (excerpt): {reply[:REPLY_KEEP]}" if reply else ""))
        elif f.suffix.lower() in (".html", ".htm"):
            if 'class="outer-cell' in raw:
                out += parse_takeout_activity(raw)
            elif "gems" in f.name.lower():
                out.append("Gemini Gems Haziq set up (custom assistants and their instructions): "
                           + _strip(raw)[:30000])
            else:
                out += [t for t in re.split(r"(?=Prompted )", _strip(raw)) if len(t) > 20]
        else:
            out.append(raw)
    return out


def chunks(items: list[str]) -> list[str]:
    out, cur = [], ""
    for it in items:
        if len(cur) + len(it) > CHUNK and cur:
            out.append(cur)
            cur = ""
        cur += it[:CHUNK] + "\n\n"
    if cur.strip():
        out.append(cur)
    return out


EXTRACT = """You are reading an export of Haziq's old chats with an AI assistant. List durable facts about
HIM that a friend/assistant should remember: identity, school, projects, skills, hardware, preferences,
habits, goals, people and pets in his life, how he likes to be answered. Only from what Haziq wrote, never
from the assistant's replies. Skip one-off questions, homework answers, and anything temporary. Never
include passwords, keys, tokens, or ID/card numbers. One short standalone sentence each, third person
("Haziq ..."), with the month when it may go stale. Output ONLY JSON:
{"facts": [{"file": "user|people|memory", "text": "..."}]}
(user = about him, including his projects and hardware; people = others in his life; memory = only how he
wants the assistant to behave.)

EXPORT
"""

MERGE = """Merge these candidate facts about Haziq into final memory files. Drop duplicates, merge related
facts, keep the most specific and most recent version of any fact that changed, drop the least useful
ones if needed to fit. Never invent anything. Limits: {caps}. Output ONLY JSON:
{{"user": ["..."], "people": ["..."], "memory": ["..."]}}

CANDIDATES
"""


def _caps() -> str:
    return ", ".join(f"{k} under {int(FILES[k][2] * 0.85)} chars total" for k in FILES)


def run_import(store: MemoryStore, curator: Curator, path: Path, apply: bool, say=print) -> dict:
    items = parse_export(path)
    if not items:
        raise SystemExit(f"nothing readable found in {path} (looked for .json .html .md .txt)")
    parts = chunks(items)
    say(f"read {len(items)} exchanges from {path.name}, {len(parts)} chunk(s) for Gemini")
    if len(parts) > MAX_CHUNKS:
        step = len(parts) / MAX_CHUNKS
        parts = [parts[int(i * step)] for i in range(MAX_CHUNKS)]
        say(f"  that is a lot: sampling {MAX_CHUNKS} evenly spread chunks")
    dest = store.root / "imports" / "gemini"
    dest.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, dest / path.name)               # raw copy: searchable later, never merged
    cands: list[dict] = []
    for i, part in enumerate(parts, 1):
        try:
            out = parse_json(curator._gen(EXTRACT + part))
        except Exception as e:
            say(f"  chunk {i}/{len(parts)} failed ({type(e).__name__}); skipped")
            continue
        got = [f for f in out.get("facts") or [] if isinstance(f, dict) and f.get("file") in FILES]
        cands += got
        say(f"  chunk {i}/{len(parts)}: {len(got)} candidate fact(s)")
    if not cands:
        raise SystemExit("no facts came out; nothing written")
    merged = parse_json(curator._gen(MERGE.format(caps=_caps()) + "\n".join(
        f"- [{c['file']}] {c.get('text', '')}" for c in cands)))
    draft = store.root / "imports" / "draft"
    draft.mkdir(parents=True, exist_ok=True)
    summary = {}
    for key in FILES:
        lines = []
        for e in merged.get(key) or []:
            try:
                lines.append(store.clean(e))
            except Exception:
                pass                                       # too long / looks like a secret: dropped
        (draft / FILES[key][0]).write_text("".join(f"- {l}\n" for l in lines), encoding="utf-8")
        summary[key] = lines
        say(f"  draft {FILES[key][0]}: {len(lines)} entries")
    say(f"review and edit the drafts in {draft}")
    if apply:
        ops = [{"file": k, "action": "add", "text": t} for k, v in summary.items() for t in v]
        res = store.apply(ops, "gemini-import")
        say(f"applied {len(res['applied'])} entries to live memory, skipped {len(res['rejected'])}")
    else:
        say("not applied. Re-run with --apply to add them to live memory, or copy the lines you want.")
    return summary


def show(store: MemoryStore, say=print):
    say(f"memory folder: {store.root}")
    for key, (fname, _h, cap) in FILES.items():
        used, _ = store.usage(key)
        say(f"\n{fname}  ({used}/{cap} chars)")
        for e in store.entries(key):
            say("  - " + e)
    n = len(store.pending_sessions())
    say(f"\n{n} conversation(s) waiting to be curated" if n else "\nno conversations waiting")


def run(a):
    store = MemoryStore()
    if a.command == "memory":
        show(store)
        return
    if a.apply and not a.path:                      # apply the reviewed (maybe hand-edited) drafts as they are
        draft = store.root / "imports" / "draft"
        ops = []
        for key, (fname, _h, _cap) in FILES.items():
            f = draft / fname
            if f.exists():
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("- ") and line.strip()[2:].strip():
                        ops.append({"file": key, "action": "add", "text": line.strip()[2:]})
        if not ops:
            raise SystemExit(f"no drafts in {draft}; run memory-import --path <export> first")
        res = store.apply(ops, "gemini-import")
        print(f"applied {len(res['applied'])} of {len(ops)} draft entries to live memory")
        for op, why in res["rejected"]:
            print(f"  skipped ({why}): {op['text'][:70]}")
        show(store)
        return
    if not a.path:
        raise SystemExit("memory-import needs --path <the Gemini export file or folder>")
    p = Path(a.path).expanduser()
    if not p.exists():
        raise SystemExit(f"not found: {p}")
    run_import(store, Curator(store, model=a.memory_model), p, a.apply)
