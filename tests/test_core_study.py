import base64
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from cyph.clock import TZ
from cyph.core.app import Deps, create_app
from cyph.core.devices import DeviceStore
from cyph.core.limiter import Limiter
from cyph.core.planner import Planner
from cyph.core.study import StudyError, StudyService, StudyStore, chunk_text, parse_json, schedule
from cyph.core.toolbox import ToolBox
from cyph.memory import MemoryStore


def vec(text):
    """A tiny deterministic embedding: letter counts, so similar words land close together."""
    v = [0.0] * 26
    for ch in text.lower():
        if "a" <= ch <= "z":
            v[ord(ch) - 97] += 1
    return v


class FakeAI:
    def __init__(self):
        self.calls = []
        self.fail_embed = False

    def embed(self, texts):
        if self.fail_embed:
            raise StudyError("embedding failed", 502)
        return [vec(t) for t in texts]

    def generate(self, system, prompt, parts=()):
        self.calls.append((prompt, len(parts)))
        if "Turn the attached study material" in prompt:
            return "```json\n" + json.dumps({"title": "Moles", "topic": "Mole concept", "clean_notes": "A mole is 6.02e23 particles.\n\nMolar mass is grams per mole.",
                    "summary": "Moles count particles.", "key_terms": [{"term": "mole", "definition": "6.02e23"}],
                    "study_guide": "Read, then do problems.", "likely_questions": ["Define a mole."]}) + "\n```"
        if "flashcards" in prompt:
            return json.dumps([{"front": "What is a mole?", "back": "6.02e23 particles"}, {"front": "Molar mass unit?", "back": "g/mol"}, {"front": "", "back": "x"}])
        if "Write " in prompt and "marking scheme" in prompt:
            return json.dumps({"question": "Find the mass of 2 mol of water.", "marks": 4, "scheme": "M=18; 36 g", "topic": "Mole concept"})
        if "Mark it like an O-Level examiner" in prompt:
            return json.dumps({"score": 1, "feedback": "Forgot the units", "next": "Redo with units"})
        if "Stage:" in prompt:
            return "hint: " + prompt.split("Stage: ")[1][:20]
        return "ok"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = StudyStore(self.root / "study")
        self.ai = FakeAI()
        self.planner = Planner(self.root / "schedule.json")
        self.events = []
        self.svc = StudyService(self.store, self.ai, planner=self.planner, changed=self.events.append, background=False)
        self.chem = self.store.subject("Chemistry")["id"]

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


class TestScheduler(unittest.TestCase):
    def card(self, **k):
        return {"interval": 0, "ease": 2.5, "reps": 0, "lapses": 0, **k}

    def test_new_card_ladder(self):
        on = date(2026, 11, 7)
        a = schedule(self.card(), 2, on)
        self.assertEqual((a["interval"], a["reps"], a["due"]), (1, 1, "2026-11-08"))
        b = schedule({**self.card(), **a}, 2, on)
        self.assertEqual(b["interval"], 3)
        c = schedule({**self.card(), **b}, 2, on)
        self.assertEqual(c["interval"], round(3 * b["ease"]))

    def test_again_resets_and_counts_a_lapse(self):
        got = schedule(self.card(interval=20, reps=5, ease=2.0), 0, date(2026, 11, 7))
        self.assertEqual((got["interval"], got["reps"], got["lapses"], got["due"]), (0, 0, 1, "2026-11-07"))
        self.assertAlmostEqual(got["ease"], 1.8)

    def test_hard_grows_slowly_and_ease_floor(self):
        got = schedule(self.card(interval=10, reps=3, ease=1.3), 1, date(2026, 11, 7))
        self.assertEqual(got["interval"], 12)
        self.assertEqual(got["ease"], 1.3)

    def test_bad_grade(self):
        with self.assertRaises(StudyError):
            schedule(self.card(), 5, date(2026, 11, 7))


class TestHelpers(unittest.TestCase):
    def test_chunking(self):
        text = "\n\n".join(["para %d " % i + "word " * 60 for i in range(6)])
        chunks = chunk_text(text, 700)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) < 1100 for c in chunks))
        self.assertEqual(chunk_text(""), [])
        self.assertTrue(all(len(c) < 1100 for c in chunk_text("x. " * 2000, 700)))

    def test_parse_json(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json('sure! [1, 2]'), [1, 2])
        self.assertIsNone(parse_json("no json here"))


class TestStore(Base):
    def test_seven_subjects_seeded_once(self):
        self.assertEqual([s["name"] for s in self.store.subjects()], ["Math", "Chemistry", "Physics", "Amath", "English", "SS", "Geog"])
        again = StudyStore(self.root / "study")
        self.assertEqual(len(again.subjects()), 7)
        again.close()

    def test_subject_lookup_by_name(self):
        self.assertEqual(self.store.subject("chemistry")["id"], self.chem)
        with self.assertRaises(StudyError):
            self.store.subject("Latin")

    def test_cards_flow(self):
        c = self.store.add_card(self.chem, "Q", "A", topic="Moles")
        self.assertEqual(c["state"], "draft")
        self.assertEqual(self.store.due(), [])
        with self.assertRaises(StudyError):
            self.store.review(c["id"], 2)
        self.store.edit_card(c["id"], {"state": "approved", "front": "Q2"})
        self.assertEqual([x["front"] for x in self.store.due()], ["Q2"])
        done = self.store.review(c["id"], 2)
        self.assertEqual(done["interval"], 1)
        self.assertEqual(self.store.due(), [])
        self.assertEqual(len(self.store.due(on=date.today() + timedelta(days=3))), 1)
        st = self.store.stats()
        self.assertEqual((st["approved"], st["reviewed_today"], st["retention"]), (1, 1, 1.0))
        self.store.remove_card(c["id"])
        self.assertEqual(self.store.cards(), [])
        with self.assertRaises(StudyError):
            self.store.add_card(self.chem, "", "A")

    def test_topics_rename_and_merge(self):
        a = self.store.topic(self.chem, "Moles")
        b = self.store.topic(self.chem, "Mole concept")
        self.store.add_card(self.chem, "Q", "A", topic="Moles")
        merged = self.store.rename_topic(a, "mole concept")
        self.assertEqual(merged["id"], b)
        self.assertEqual(self.store.card(self.store.cards()[0]["id"])["topic_id"], b)
        self.assertEqual(len(self.store.topics(self.chem)), 1)
        self.assertEqual(self.store.rename_topic(b, "Stoichiometry")["name"], "Stoichiometry")

    def test_weak_topics_rank_by_marks(self):
        self.store.add_attempt(self.chem, "Acids", "structured", "q", "a", 1, 4, "")
        self.store.add_attempt(self.chem, "Bonding", "structured", "q", "a", 4, 4, "")
        weak = self.store.weak_topics(self.chem)
        self.assertEqual([w["name"] for w in weak], ["Acids"])
        self.assertAlmostEqual(weak[0]["weakness"], 0.75, places=2)

    def test_notebook_and_pages(self):
        nb = self.store.add_notebook(self.chem, "Chem")
        pid = nb["pages"][0]["id"]
        page = self.store.save_page(pid, [{"c": "#fff", "w": 2, "pts": [[1, 1], [5, 5]]}, {"pts": []}, {"pts": [[0, 0]]}], "grid")
        self.assertEqual(len(page["strokes"]), 2)
        self.assertEqual(page["background"], "grid")
        p2 = self.store.add_page(nb["id"], "dots")
        self.assertEqual(len(self.store.notebook(nb["id"])["pages"]), 2)
        self.store.remove_page(p2)
        with self.assertRaises(StudyError):
            self.store.remove_page(pid)
        with self.assertRaises(StudyError):
            self.store.add_page(nb["id"], "pink")

    def test_board_items(self):
        b = self.store.add_board(self.chem, "Map")
        it = self.store.put_item(b["id"], {"kind": "sticky", "x": 10, "y": 20, "w": 100, "h": 80, "data": {"text": "hi"}})
        self.store.put_item(b["id"], {"id": it["id"], "kind": "sticky", "x": 50, "data": {"text": "moved"}})
        got = self.store.board(b["id"])["items"]
        self.assertEqual((len(got), got[0]["x"], got[0]["data"]["text"]), (1, 50.0, "moved"))
        with self.assertRaises(StudyError):
            self.store.put_item(b["id"], {"kind": "laser"})
        self.store.remove_item(b["id"], it["id"])
        self.assertEqual(self.store.board(b["id"])["items"], [])


class TestService(Base):
    def test_import_makes_notes_outputs_and_search(self):
        src = self.svc.import_source("Chemistry", "photo", "application/pdf", b"%PDF-fake")
        self.assertEqual(src["status"], "ready")
        self.assertEqual(src["title"], "Moles")
        out = self.store.outputs(src["id"])
        self.assertEqual(set(out), {"summary", "study_guide", "key_terms", "likely_questions", "clean_notes"})
        notes = self.store.notes(self.chem)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["topic"], "Mole concept")
        self.assertEqual(self.ai.calls[0][1], 1)                     # the PDF went to the model as a part
        hits = self.svc.search("molar mass in grams", "Chemistry")
        self.assertTrue(hits and "Molar mass" in hits[0]["text"] or "mole" in hits[0]["text"])
        self.assertIn("study", self.events)

    def test_failed_import_is_recorded_not_raised(self):
        self.ai.generate = lambda *a, **k: "garbage"
        src = self.svc.import_source("Chemistry", "x", "image/png", b"\x89PNG")
        self.assertEqual(src["status"], "failed")
        self.assertTrue(src["error"])
        self.ai.generate = FakeAI().generate
        self.assertEqual(self.svc.process(src["id"])["title"], "Moles")

    def test_import_validation(self):
        with self.assertRaises(StudyError):
            self.svc.import_source("Chemistry", "x", "application/zip", b"zz")
        with self.assertRaises(StudyError):
            self.svc.import_source("Chemistry", "x", "image/png", b"")
        with self.assertRaises(StudyError):
            self.svc.import_source("Latin", "x", "image/png", b"1")

    def test_search_falls_back_to_words_without_embeddings(self):
        self.ai.fail_embed = True
        self.svc.save_note("Chemistry", "Acids", "Hydrochloric acid is a strong acid")
        self.assertEqual(self.svc.search("strong acid")[0]["title"], "Acids")
        self.ai.fail_embed = False
        self.assertEqual(self.svc.reindex(), 1)

    def test_draft_cards_skip_bad_ones(self):
        note = self.svc.save_note("Chemistry", "Moles", "A mole is 6.02e23 particles", topic="Moles")
        cards = self.svc.draft_cards(note_id=note["id"])
        self.assertEqual(len(cards), 2)
        self.assertTrue(all(c["state"] == "draft" and c["topic"] == "Moles" for c in cards))

    def test_tutor_ladder_and_cites(self):
        self.svc.save_note("Chemistry", "Moles", "A mole is 6.02e23 particles")
        steps = [self.svc.tutor("what is a mole", "Chemistry", i) for i in range(4)]
        self.assertEqual([s["stage"] for s in steps], ["restate", "nudge", "first_step", "solution"])
        self.assertTrue(steps[3]["last"] and not steps[0]["last"])
        self.assertEqual(steps[0]["cites"][0]["title"], "Moles")
        with self.assertRaises(StudyError):
            self.svc.tutor("  ")

    def test_practice_mark_and_weakness(self):
        q = self.svc.practice("Chemistry", kind="structured")
        self.assertNotIn("scheme", q)
        res = self.svc.mark(q["id"], "36")
        self.assertEqual((res["score"], res["marks"]), (1.0, 4))
        with self.assertRaises(StudyError):                          # a question is marked once
            self.svc.mark(q["id"], "36")
        self.assertEqual(self.store.weak_topics(self.chem)[0]["name"], "Mole concept")
        # Cyph picks the weak topic for the next question
        self.assertEqual(self.svc.practice("Chemistry")["topic"], "Mole concept")
        with self.assertRaises(StudyError):
            self.svc.practice("Chemistry", kind="haiku")

    def test_paper_marked_against_uploaded_scheme(self):
        res = self.svc.mark_paper("Chemistry", "Q1", "36 g", "M=18, answer 36 g", 4, "Moles")
        self.assertEqual(res["attempt"]["scheme"], "M=18, answer 36 g")
        self.assertIn("Marking scheme: M=18", self.ai.calls[-1][0])

    def test_plan_test_makes_event_and_revision_blocks(self):
        day = (date.today() + timedelta(days=10)).isoformat()
        out = self.svc.plan_test("Chemistry", "Ch 5", day, ["Acids", "Bonding"])
        self.assertEqual(len(out["blocks"]), 3)
        kinds = sorted(e["kind"] for e in self.planner.range(date.today(), 12))
        self.assertEqual(kinds, ["block", "block", "block", "test"])
        self.svc.remove_test(out["test"]["id"])
        self.assertEqual([e["kind"] for e in self.planner.range(date.today(), 12)].count("test"), 0)

    def test_plan_test_skips_blocks_in_the_past(self):
        day = (date.today() + timedelta(days=2)).isoformat()
        self.assertEqual(len(self.svc.plan_test("Chemistry", "Quiz", day, ["x"])["blocks"]), 1)

    def test_today_summary(self):
        c = self.store.add_card(self.chem, "Q", "A", "approved")
        t = self.svc.today()
        self.assertEqual(t["stats"]["due"], 1)
        self.assertEqual(next(s for s in t["subjects"] if s["name"] == "Chemistry")["due"], 1)

    def test_notebook_pdf(self):
        nb = self.store.add_notebook(self.chem, "Chem")
        self.store.save_page(nb["pages"][0]["id"], [{"c": "#e8e4d8", "w": 3, "pts": [[10, 10], [200, 200]]}])
        self.store.add_page(nb["id"], "grid")
        pdf = self.svc.notebook_pdf(nb["id"])
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page "), 2) if b"/Type /Page " in pdf else None


class TestToolbox(Base):
    def test_study_tools(self):
        tb = ToolBox(self.planner, study=self.svc)
        names = {t["function"]["name"] for t in tb.tools()}
        self.assertTrue({"study_search", "study_draft_cards", "study_due", "study_weak_topics", "study_practice"} <= names)
        note = self.svc.save_note("Chemistry", "Moles", "A mole is 6.02e23 particles")
        self.assertEqual(tb.run("study_search", {"query": "mole particles"})["results"][0]["note"], "Moles")
        self.assertEqual(tb.run("study_draft_cards", {"note_id": note["id"]})["drafted"], 2)
        self.assertEqual(tb.run("study_due", {})["due"], 0)
        self.assertEqual(tb.run("study_practice", {"subject": "Chemistry"})["marks"], 4)
        self.assertIn("error", tb.run("study_practice", {"subject": "Latin"}))
        self.assertIn("error", tb.run("study_search", {"query": " "}))
        self.assertEqual(tb.run("study_weak_topics", {})["weak"], [])

    def test_no_study_tools_without_study(self):
        self.assertFalse(any(t["function"]["name"].startswith("study_") for t in ToolBox(self.planner).tools()))


class TestApi(Base):
    def setUp(self):
        super().setUp()
        self.devices = DeviceStore(self.root / "devices.db")
        self.published = []
        class Hub:
            def publish(s, m): self.published.append(m)
        class Brain:
            def reply(s, text, attachments=(), channel="tg", on_text=None, on_event=None, mode="", subject=""):
                on_text(f"[happy] {mode}/{subject}")
                return f"[happy] {mode}/{subject}"
        deps = Deps(devices=self.devices, planner=self.planner, store=MemoryStore(self.root / "mem"), brain=Brain(),
                    log=lambda *a: None, pair_limiter=Limiter(5, 600, 900), auth_limiter=Limiter(30, 60, 300), hub=Hub(), study=self.svc)
        self.client = TestClient(create_app(deps))
        r = self.client.post("/pair", json={"code": self.devices.new_pair_code(), "name": "t"})
        self.h = {"Authorization": "Bearer " + r.json()["token"]}

    def tearDown(self):
        self.devices.close()
        super().tearDown()

    def test_needs_token(self):
        self.assertEqual(self.client.get("/study/today").status_code, 401)

    def test_end_to_end(self):
        c, h = self.client, self.h
        self.assertEqual(len(c.get("/study/subjects", headers=h).json()["subjects"]), 7)
        data = base64.b64encode(b"%PDF-1.4 fake").decode()
        src = c.post("/study/sources", headers=h, json={"subject": "Chemistry", "title": "p", "mime": "application/pdf", "data": data}).json()
        self.assertEqual(src["status"], "ready")
        detail = c.get(f"/study/sources/{src['id']}", headers=h).json()
        self.assertIn("summary", detail["outputs"])
        self.assertEqual(c.get(f"/study/sources/{src['id']}/original", headers=h).content, b"%PDF-1.4 fake")
        fid = c.get(f"/study/sources/{src['id']}", headers=h).json()["file_id"]
        self.assertEqual(base64.b64decode(c.get(f"/study/files/{fid}/data", headers=h).json()["data"]), b"%PDF-1.4 fake")
        notes = c.get("/study/notes?subject=Chemistry", headers=h).json()["notes"]
        self.assertEqual(len(notes), 1)
        self.assertIn("mole", c.get(f"/study/notes/{notes[0]['id']}", headers=h).json()["body"].lower())
        self.assertTrue(c.get("/study/search?q=molar mass", headers=h).json()["results"])
        drafts = c.post("/study/cards/draft", headers=h, json={"note_id": notes[0]["id"]}).json()["cards"]
        self.assertEqual(c.get("/study/due", headers=h).json()["cards"], [])
        cid = drafts[0]["id"]
        self.assertEqual(c.patch(f"/study/cards/{cid}", headers=h, json={"front": "Edited"}).json()["front"], "Edited")
        self.assertEqual(c.post(f"/study/cards/{cid}/approve", headers=h).json()["state"], "approved")
        self.assertEqual(len(c.get("/study/due", headers=h).json()["cards"]), 1)
        self.assertEqual(c.post(f"/study/cards/{cid}/review", headers=h, json={"grade": 7}).status_code, 422)
        self.assertEqual(c.post(f"/study/cards/{cid}/review", headers=h, json={"grade": 2}).json()["interval"], 1)
        self.assertEqual(c.get("/study/stats", headers=h).json()["reviewed_today"], 1)
        self.assertEqual(c.delete(f"/study/cards/{drafts[1]['id']}", headers=h).status_code, 200)
        self.assertIn({"type": "changed", "what": "study"}, self.published)

    def test_tutor_practice_tests(self):
        c, h = self.client, self.h
        self.assertEqual(c.post("/study/tutor", headers=h, json={"question": "why", "step": 1}).json()["stage"], "nudge")
        q = c.post("/study/practice", headers=h, json={"subject": "Chemistry"}).json()
        r = c.post(f"/study/practice/{q['id']}/answer", headers=h, json={"answer": "36"}).json()
        self.assertEqual(r["score"], 1.0)
        self.assertEqual(c.post(f"/study/practice/{q['id']}/answer", headers=h, json={"answer": "36"}).status_code, 404)
        self.assertEqual(c.get("/study/topics?subject=Chemistry", headers=h).json()["weak"][0]["name"], "Mole concept")
        day = (date.today() + timedelta(days=9)).isoformat()
        t = c.post("/study/tests", headers=h, json={"subject": "Chemistry", "title": "Ch 5", "date": day}).json()
        self.assertEqual(len(t["blocks"]), 3)
        self.assertEqual(len(c.get("/study/tests", headers=h).json()["tests"]), 1)
        self.assertEqual(c.delete(f"/study/tests/{t['test']['id']}", headers=h).status_code, 200)
        self.assertEqual(c.get("/study/attempts", headers=h).json()["attempts"][0]["topic"], "Mole concept")

    def test_errors_map_to_http(self):
        c, h = self.client, self.h
        self.assertEqual(c.get("/study/notes?subject=Latin", headers=h).status_code, 404)
        self.assertEqual(c.post("/study/sources", headers=h, json={"subject": "Chemistry", "mime": "image/png", "data": "!!"}).status_code, 422)
        self.assertEqual(c.get("/study/cards/nope", headers=h).status_code, 405)
        self.assertEqual(c.patch("/study/cards/nope", headers=h, json={"front": "x"}).status_code, 404)

    def test_notebooks_boards_and_pdf(self):
        c, h = self.client, self.h
        nb = c.post("/study/notebooks", headers=h, json={"subject": "Chemistry", "title": "Lab"}).json()
        pid = nb["pages"][0]["id"]
        put = c.put(f"/study/pages/{pid}", headers=h, json={"strokes": [{"c": "#5dff7a", "w": 2, "pts": [[1, 2], [3, 4]]}], "background": "grid"})
        self.assertEqual(put.json()["background"], "grid")
        self.assertEqual(c.get(f"/study/pages/{pid}", headers=h).json()["strokes"][0]["pts"], [[1.0, 2.0], [3.0, 4.0]])
        self.assertEqual(len(c.post(f"/study/notebooks/{nb['id']}/pages", headers=h, json={"background": "dots"}).json()["strokes"]), 0)
        self.assertTrue(c.get(f"/study/notebooks/{nb['id']}/pdf", headers=h).content.startswith(b"%PDF"))
        self.assertTrue(base64.b64decode(c.get(f"/study/notebooks/{nb['id']}/pdf/data", headers=h).json()["data"]).startswith(b"%PDF"))
        self.assertEqual(c.delete(f"/study/pages/{pid}", headers=h).status_code, 200)
        self.assertEqual(c.delete(f"/study/pages/{c.get(f'/study/notebooks/{nb['id']}', headers=h).json()['pages'][0]['id']}", headers=h).status_code, 409)
        b = c.post("/study/boards", headers=h, json={"title": "Map"}).json()
        it = c.put(f"/study/boards/{b['id']}/items", headers=h, json={"kind": "sticky", "x": 5, "data": {"text": "a"}}).json()
        self.assertEqual(c.get(f"/study/boards/{b['id']}", headers=h).json()["items"][0]["id"], it["id"])
        self.assertEqual(c.put(f"/study/boards/{b['id']}/items", headers=h, json={"kind": "nope"}).status_code, 422)
        self.assertEqual(c.delete(f"/study/boards/{b['id']}/items/{it['id']}", headers=h).status_code, 200)

    def test_big_upload_allowed_only_on_study_paths(self):
        big = base64.b64encode(b"x" * (2 * 1024 * 1024)).decode()
        ok = self.client.post("/study/sources", headers=self.h, json={"subject": "Chemistry", "mime": "image/png", "data": big})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.client.post("/plan", headers=self.h, json={"title": "x", "date": "2026-11-20", "notes": "y" * 100000}).status_code, 413)

    def test_chat_passes_study_mode(self):
        with self.client.websocket_connect("/chat") as ws:
            ws.send_json({"type": "auth", "token": self.h["Authorization"][7:]})
            self.assertEqual(ws.receive_json()["type"], "ready")
            ws.send_json({"type": "say", "text": "help", "mode": "study", "subject": "Chemistry"})
            seen = []
            while (m := ws.receive_json())["type"] != "final":
                seen.append(m)
            self.assertEqual(m["text"], "[happy] study/Chemistry")


if __name__ == "__main__":
    unittest.main()


class TestWebRoutes(unittest.TestCase):
    def make(self, web=None, cert=None):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.devices = DeviceStore(root / "devices.db")
        deps = Deps(devices=self.devices, planner=Planner(root / "s.json"), store=MemoryStore(root / "m"), brain=None,
                    log=lambda *a: None, pair_limiter=Limiter(5, 600, 900), auth_limiter=Limiter(30, 60, 300), web_root=web, cert_path=cert)
        return TestClient(create_app(deps))

    def tearDown(self):
        self.devices.close()
        self.tmp.cleanup()

    def test_serves_the_built_app_and_the_certificate_without_login(self):
        web = Path(tempfile.mkdtemp())
        (web / "assets").mkdir()
        (web / "index.html").write_text("<html>cyph</html>")
        (web / "assets" / "a.js").write_text("1")
        cert = web / "cert.pem"
        cert.write_text("-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n")
        c = self.make(web, cert)
        self.assertEqual(c.get("/").text, "<html>cyph</html>")
        self.assertEqual(c.get("/assets/a.js").text, "1")
        r = c.get("/cert.pem")
        self.assertEqual(r.headers["content-type"], "application/x-x509-ca-cert")
        self.assertIn("BEGIN CERTIFICATE", r.text)
        self.assertEqual(c.get("/plan").status_code, 401)             # data is still behind the token

    def test_nothing_is_served_when_not_built(self):
        c = self.make(None, None)
        self.assertEqual(c.get("/").status_code, 404)
        self.assertEqual(c.get("/cert.pem").status_code, 404)
