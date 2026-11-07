"""The /study routes. Every one needs a device token (the `auth` dependency of the main app). Uploads are base64 in
JSON, like pictures in chat, so no multipart parser is needed."""
from __future__ import annotations

import base64
import binascii
from typing import Any, Optional

from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel

from .study import GRADES, StudyError


class SubjectIn(BaseModel):
    name: str
    color: str = "#d9984a"


class SourceIn(BaseModel):
    subject: str
    title: str = ""
    mime: str
    data: str = ""            # base64
    text: str = ""


class NoteIn(BaseModel):
    subject: Optional[str] = None
    title: str = ""
    body: str = ""
    topic: Optional[str] = None


class NotePatch(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    topic: Optional[str] = None


class CardIn(BaseModel):
    subject: str
    front: str
    back: str
    topic: Optional[str] = None
    state: str = "approved"


class CardPatch(BaseModel):
    front: Optional[str] = None
    back: Optional[str] = None
    topic: Optional[str] = None
    state: Optional[str] = None


class DraftIn(BaseModel):
    subject: Optional[str] = None
    note_id: Optional[str] = None
    text: str = ""
    n: int = 8


class ReviewIn(BaseModel):
    grade: int


class TutorIn(BaseModel):
    question: str = ""
    subject: Optional[str] = None
    step: int = 0
    history: str = ""
    images: list[dict] = []


class PracticeIn(BaseModel):
    subject: str
    topic: Optional[str] = None
    kind: str = "structured"


class AnswerIn(BaseModel):
    answer: str = ""
    images: list[dict] = []


class PaperIn(BaseModel):
    subject: str
    question: str
    answer: str = ""
    scheme: str = ""
    marks: int = 4
    topic: Optional[str] = None
    images: list[dict] = []


class TestIn(BaseModel):
    subject: str
    title: str
    date: str
    topics: list[str] = []
    blocks: list[int] = [7, 3, 1]
    time: str = "16:00"
    minutes: int = 45


class TopicPatch(BaseModel):
    name: Optional[str] = None
    merge_into: Optional[str] = None


class TitleIn(BaseModel):
    subject: Optional[str] = None
    title: str = ""


class PageIn(BaseModel):
    strokes: list[dict]
    background: Optional[str] = None


class BgIn(BaseModel):
    background: str = "lined"


class ItemIn(BaseModel):
    id: Optional[str] = None
    kind: str
    x: float = 0
    y: float = 0
    w: float = 0
    h: float = 0
    data: dict[str, Any] = {}


def pics(raw: list[dict]) -> list[dict]:
    out = []
    for it in raw[:4]:
        try:
            data = base64.b64decode(str(it.get("data", "")), validate=True)
        except (binascii.Error, ValueError):
            raise StudyError("that picture could not be read")
        if str(it.get("mime", "")).lower() not in ("image/png", "image/jpeg", "image/webp") or not data or len(data) > 8 * 1024 * 1024:
            raise StudyError("PNG, JPEG or WebP pictures up to 8 MB")
        out.append({"mime": it["mime"].lower(), "data": data})
    return out


def add_study_routes(app, d, auth, changed):
    def svc():
        if d.study is None:
            raise HTTPException(503, "study is not available on this server")
        return d.study

    def guard(fn):
        try:
            return fn()
        except StudyError as e:
            raise HTTPException(e.code, str(e))

    def sid(ref):                                       # a subject id or name -> id, or None
        s = svc().store.subject(ref)
        return s["id"] if s else None

    # ---- front page, subjects --------------------------------------------------------------
    @app.get("/study/today")
    def study_today(dev: dict = Depends(auth)):
        return guard(lambda: svc().today())

    @app.get("/study/subjects")
    def subjects_get(dev: dict = Depends(auth)):
        return {"subjects": svc().store.subjects()}

    @app.post("/study/subjects")
    def subjects_add(body: SubjectIn, dev: dict = Depends(auth)):
        got = guard(lambda: svc().store.add_subject(body.name, body.color))
        changed("study")
        return got

    @app.delete("/study/subjects/{ref}")
    def subjects_del(ref: str, dev: dict = Depends(auth)):
        guard(lambda: svc().store.remove_subject(svc().store.subject(ref)["id"]))
        changed("study")
        return {"ok": True}

    # ---- sources (imports) ----------------------------------------------------------------------
    @app.post("/study/sources")
    def source_add(body: SourceIn, dev: dict = Depends(auth)):
        try:
            data = base64.b64decode(body.data, validate=True) if body.data else None
        except (binascii.Error, ValueError):
            raise HTTPException(422, "that file could not be read")
        got = guard(lambda: svc().import_source(body.subject, body.title, body.mime, data, body.text))
        changed("study")
        return got

    @app.get("/study/sources")
    def sources_get(subject: Optional[str] = None, dev: dict = Depends(auth)):
        return {"sources": svc().store.sources(guard(lambda: sid(subject)))}

    @app.get("/study/sources/{rid}")
    def source_get(rid: str, dev: dict = Depends(auth)):
        return guard(lambda: {**svc().store.source(rid), "outputs": svc().store.outputs(rid)})

    @app.get("/study/sources/{rid}/original")
    def source_original(rid: str, dev: dict = Depends(auth)):
        src = guard(lambda: svc().store.source(rid))
        got = svc().store.get_file(src["file_id"])
        if not got:
            raise HTTPException(404, "the original is gone")
        return Response(got[1], media_type=got[0]["mime"], headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/study/sources/{rid}/retry")
    def source_retry(rid: str, dev: dict = Depends(auth)):
        return guard(lambda: svc().retry(rid))

    @app.delete("/study/sources/{rid}")
    def source_del(rid: str, dev: dict = Depends(auth)):
        guard(lambda: svc().store.remove_source(rid))
        changed("study")
        return {"ok": True}

    def as_json(mime: str, data: bytes):
        return {"mime": mime, "data": base64.b64encode(data).decode()}

    @app.get("/study/files/{fid}/data")
    def file_data(fid: str, dev: dict = Depends(auth)):
        """The same bytes as JSON, for the desktop app whose bridge carries JSON only."""
        got = svc().store.get_file(fid)
        if not got:
            raise HTTPException(404, "no such file")
        return as_json(got[0]["mime"], got[1])

    @app.get("/study/notebooks/{nid}/pdf/data")
    def nb_pdf_data(nid: str, dev: dict = Depends(auth)):
        return as_json("application/pdf", guard(lambda: svc().notebook_pdf(nid)))

    @app.get("/study/files/{fid}")
    def file_get(fid: str, dev: dict = Depends(auth)):
        got = svc().store.get_file(fid)
        if not got:
            raise HTTPException(404, "no such file")
        return Response(got[1], media_type=got[0]["mime"], headers={"Cache-Control": "private, max-age=3600"})

    # ---- notes and search -------------------------------------------------------------------------
    @app.get("/study/notes")
    def notes_get(subject: Optional[str] = None, q: str = "", dev: dict = Depends(auth)):
        return {"notes": svc().store.notes(guard(lambda: sid(subject)), q)}

    @app.post("/study/notes")
    def notes_add(body: NoteIn, dev: dict = Depends(auth)):
        return guard(lambda: svc().save_note(body.subject, body.title, body.body, "typed", body.topic))

    @app.get("/study/notes/{nid}")
    def note_get(nid: str, dev: dict = Depends(auth)):
        return guard(lambda: svc().store.note(nid))

    @app.patch("/study/notes/{nid}")
    def note_patch(nid: str, body: NotePatch, dev: dict = Depends(auth)):
        return guard(lambda: svc().update_note(nid, body.model_dump(exclude_unset=True)))

    @app.delete("/study/notes/{nid}")
    def note_del(nid: str, dev: dict = Depends(auth)):
        guard(lambda: svc().store.remove_note(nid))
        changed("study")
        return {"ok": True}

    @app.get("/study/search")
    def search(q: str, subject: Optional[str] = None, k: int = 5, dev: dict = Depends(auth)):
        return {"results": guard(lambda: svc().search(q, subject, k))}

    @app.post("/study/reindex")
    def reindex(dev: dict = Depends(auth)):
        return {"embedded": guard(lambda: svc().reindex())}

    # ---- topics -----------------------------------------------------------------------------------
    @app.get("/study/topics")
    def topics_get(subject: Optional[str] = None, dev: dict = Depends(auth)):
        return {"topics": svc().store.topics(guard(lambda: sid(subject))), "weak": svc().store.weak_topics(guard(lambda: sid(subject)))}

    @app.patch("/study/topics/{tid}")
    def topic_patch(tid: str, body: TopicPatch, dev: dict = Depends(auth)):
        def go():
            if body.merge_into:
                svc().store.merge_topics(tid, body.merge_into)
                return {"ok": True}
            return svc().store.rename_topic(tid, body.name or "")
        got = guard(go)
        changed("study")
        return got

    # ---- cards ------------------------------------------------------------------------------------------
    @app.get("/study/cards")
    def cards_get(subject: Optional[str] = None, state: Optional[str] = None, dev: dict = Depends(auth)):
        return {"cards": svc().store.cards(guard(lambda: sid(subject)), state)}

    @app.post("/study/cards")
    def cards_add(body: CardIn, dev: dict = Depends(auth)):
        subject_id = guard(lambda: sid(body.subject))
        if not subject_id:
            raise HTTPException(422, "pick a subject first")
        got = guard(lambda: svc().store.add_card(subject_id, body.front, body.back, body.state, body.topic))
        changed("study")
        return got

    @app.post("/study/cards/draft")
    def cards_draft(body: DraftIn, dev: dict = Depends(auth)):
        return {"cards": guard(lambda: svc().draft_cards(body.subject, body.note_id, body.text, body.n))}

    @app.patch("/study/cards/{cid}")
    def card_patch(cid: str, body: CardPatch, dev: dict = Depends(auth)):
        got = guard(lambda: svc().store.edit_card(cid, body.model_dump(exclude_unset=True)))
        changed("study")
        return got

    @app.post("/study/cards/{cid}/approve")
    def card_approve(cid: str, dev: dict = Depends(auth)):
        got = guard(lambda: svc().store.edit_card(cid, {"state": "approved"}))
        changed("study")
        return got

    @app.post("/study/cards/{cid}/review")
    def card_review(cid: str, body: ReviewIn, dev: dict = Depends(auth)):
        if body.grade not in GRADES:
            raise HTTPException(422, "grade must be 0, 1 or 2")
        got = guard(lambda: svc().store.review(cid, body.grade))
        changed("study")
        return got

    @app.delete("/study/cards/{cid}")
    def card_del(cid: str, dev: dict = Depends(auth)):
        guard(lambda: svc().store.remove_card(cid))
        changed("study")
        return {"ok": True}

    @app.get("/study/due")
    def due(subject: Optional[str] = None, limit: int = 30, dev: dict = Depends(auth)):
        return {"cards": svc().store.due(guard(lambda: sid(subject)), max(1, min(limit, 200)))}

    @app.get("/study/stats")
    def stats(subject: Optional[str] = None, dev: dict = Depends(auth)):
        return guard(lambda: svc().store.stats(sid(subject)))

    # ---- tutor, practice, papers ----------------------------------------------------------------------
    @app.post("/study/tutor")
    def tutor(body: TutorIn, dev: dict = Depends(auth)):
        return guard(lambda: svc().tutor(body.question, body.subject, body.step, body.history, pics(body.images)))

    @app.post("/study/practice")
    def practice(body: PracticeIn, dev: dict = Depends(auth)):
        return guard(lambda: svc().practice(body.subject, body.topic, body.kind))

    @app.post("/study/practice/{pid}/answer")
    def practice_answer(pid: str, body: AnswerIn, dev: dict = Depends(auth)):
        return guard(lambda: svc().mark(pid, body.answer, pics(body.images)))

    @app.post("/study/papers/mark")
    def paper_mark(body: PaperIn, dev: dict = Depends(auth)):
        return guard(lambda: svc().mark_paper(body.subject, body.question, body.answer, body.scheme, body.marks, body.topic, pics(body.images)))

    @app.get("/study/attempts")
    def attempts(subject: Optional[str] = None, dev: dict = Depends(auth)):
        return {"attempts": svc().store.attempts(guard(lambda: sid(subject)))}

    # ---- tests ------------------------------------------------------------------------------------------
    @app.get("/study/tests")
    def tests_get(subject: Optional[str] = None, dev: dict = Depends(auth)):
        return {"tests": svc().store.tests(guard(lambda: sid(subject)))}

    @app.post("/study/tests")
    def tests_add(body: TestIn, dev: dict = Depends(auth)):
        return guard(lambda: svc().plan_test(body.subject, body.title, body.date, body.topics, body.blocks, body.time, body.minutes))

    @app.delete("/study/tests/{tid}")
    def tests_del(tid: str, dev: dict = Depends(auth)):
        guard(lambda: svc().remove_test(tid))
        return {"ok": True}

    # ---- notebooks ----------------------------------------------------------------------------------------
    @app.get("/study/notebooks")
    def nb_list(subject: Optional[str] = None, dev: dict = Depends(auth)):
        return {"notebooks": svc().store.notebooks(guard(lambda: sid(subject)))}

    @app.post("/study/notebooks")
    def nb_add(body: TitleIn, dev: dict = Depends(auth)):
        got = guard(lambda: svc().store.add_notebook(sid(body.subject), body.title))
        changed("study")
        return got

    @app.get("/study/notebooks/{nid}")
    def nb_get(nid: str, dev: dict = Depends(auth)):
        return guard(lambda: svc().store.notebook(nid))

    @app.delete("/study/notebooks/{nid}")
    def nb_del(nid: str, dev: dict = Depends(auth)):
        guard(lambda: svc().store.remove_notebook(nid))
        changed("study")
        return {"ok": True}

    @app.post("/study/notebooks/{nid}/pages")
    def page_add(nid: str, body: BgIn, dev: dict = Depends(auth)):
        pid = guard(lambda: svc().store.add_page(nid, body.background))
        changed("study")
        return guard(lambda: svc().store.page(pid))

    @app.get("/study/notebooks/{nid}/pdf")
    def nb_pdf(nid: str, dev: dict = Depends(auth)):
        return Response(guard(lambda: svc().notebook_pdf(nid)), media_type="application/pdf")

    @app.get("/study/pages/{pid}")
    def page_get(pid: str, dev: dict = Depends(auth)):
        return guard(lambda: svc().store.page(pid))

    @app.put("/study/pages/{pid}")
    def page_put(pid: str, body: PageIn, dev: dict = Depends(auth)):
        got = guard(lambda: svc().store.save_page(pid, body.strokes, body.background))
        changed("study")
        return got

    @app.delete("/study/pages/{pid}")
    def page_del(pid: str, dev: dict = Depends(auth)):
        guard(lambda: svc().store.remove_page(pid))
        changed("study")
        return {"ok": True}

    # ---- boards ---------------------------------------------------------------------------------------------
    @app.get("/study/boards")
    def boards_list(subject: Optional[str] = None, dev: dict = Depends(auth)):
        return {"boards": svc().store.boards(guard(lambda: sid(subject)))}

    @app.post("/study/boards")
    def board_add(body: TitleIn, dev: dict = Depends(auth)):
        got = guard(lambda: svc().store.add_board(sid(body.subject), body.title))
        changed("study")
        return got

    @app.get("/study/boards/{bid}")
    def board_get(bid: str, dev: dict = Depends(auth)):
        return guard(lambda: svc().store.board(bid))

    @app.put("/study/boards/{bid}/items")
    def board_put(bid: str, body: ItemIn, dev: dict = Depends(auth)):
        got = guard(lambda: svc().store.put_item(bid, body.model_dump()))
        changed("study")
        return got

    @app.delete("/study/boards/{bid}/items/{iid}")
    def board_item_del(bid: str, iid: str, dev: dict = Depends(auth)):
        guard(lambda: svc().store.remove_item(bid, iid))
        changed("study")
        return {"ok": True}

    @app.delete("/study/boards/{bid}")
    def board_del(bid: str, dev: dict = Depends(auth)):
        guard(lambda: svc().store.remove_board(bid))
        changed("study")
        return {"ok": True}
