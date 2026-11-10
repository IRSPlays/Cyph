"""The study engine: what a session holds, marking, strength, the debrief, the inbox, flags and reminders."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from cyph.clock import TZ
from cyph.core.app import Deps, create_app
from cyph.core.devices import DeviceStore
from cyph.core.limiter import Limiter
from cyph.core.planner import Planner
from cyph.core.session import close, quick_of, scheme_of, _num
from cyph.core.study import StudyError, StudyService, StudyStore, today
from cyph.core.toolbox import ToolBox
from cyph.memory import MemoryStore

Q4 = {"ref": "Q4", "text": "4.8 g of magnesium reacts with 0.30 mol of HCl. Which is limiting, and what mass of hydrogen forms?", "marks": 4,
      "topic": "Limiting reagent", "answer": "HCl is limiting; 0.30 g", "key": "printed",
      "scheme": [{"point": "Moles of Mg = 0.20", "marks": 1}, {"point": "HCl is limiting", "marks": 1}, {"point": "Moles of H2 = 0.15", "marks": 1},
                 {"point": "Mass of H2 = 0.30 g", "marks": 1, "final": True}],
      "numeric": 0.30, "unit": "g", "figure": False, "page": 1,
      "quick": {"ask": "Which runs out first?", "options": ["Magnesium", "Hydrochloric acid", "Neither"], "correct": 1, "final_label": "Mass of hydrogen",
                "explain": "0.20 mol Mg needs 0.40 mol HCl but there is only 0.30."}}


class FakeAI:
    def __init__(self):
        self.calls, self.marks, self.subject = [], None, "Chemistry"

    def embed(self, texts):
        return [[float(len(t))] for t in texts]

    def generate(self, system, prompt, parts=()):
        self.calls.append(prompt)
        if "Turn the attached study material" in prompt:
            return json.dumps({"title": "Chem WS 7", "kind": "worksheet", "subject": self.subject, "topic": "Limiting reagent",
                               "clean_notes": "The limiting reagent is used up first.\n\nConvert to moles, then compare with the equation.",
                               "summary": "Limiting reagents.", "key_terms": [], "study_guide": "", "cards": [{"front": "What is the limiting reagent?", "back": "The one used up first"},
                                                                                                  {"front": "First step?", "back": "Convert to moles"}],
                               "questions": [Q4, {"text": ""}]})
        if "Mark this like a fair O-Level examiner" in prompt:
            pts = self.marks or [{"i": 0, "got": True, "quote": "n(Mg) = 0.20"}, {"i": 1, "got": True, "quote": "HCl limiting"},
                                 {"i": 2, "got": False, "quote": "n(H2) = 0.20", "why": "Used Mg"}, {"i": 3, "got": True, "quote": "0.40 g"}]
            if "first marking was wrong" in prompt:
                pts = [{"i": i, "got": True, "quote": ""} for i in range(4)]
            return json.dumps({"points": pts, "final": None, "headline": "Nearly.", "slip": "used the wrong reagent"})
        if "Teach " in prompt:
            return json.dumps({"example": {"problem": "2 mol A with 1 mol B", "steps": ["moles", "ratio", "B limits"]},
                               "turn": {"ask": "3 mol A with 1 mol B?", "options": ["A", "B", "neither"], "correct": 1, "explain": "B runs out."},
                               "why": "Same sum as sizing your battery pack." if "battery" in prompt else ""})
        if "exam questions on the topic" in prompt:
            return json.dumps([{**Q4, "ref": "", "text": "Made-up question", "key": "cyph"}])
        if "what he learned at school today" in prompt:
            if "ask nothing more" in prompt or prompt.count("HIM:") > 1:
                return json.dumps({"topics": [{"subject": "Chemistry", "topic": "Titration", "state": "solid", "gap": ""},
                                              {"subject": "Physics", "topic": "Moments", "state": "missed", "gap": "perpendicular distance"},
                                              {"subject": "Alchemy", "topic": "Gold", "state": "solid"}],
                                   "cards": [{"subject": "Physics", "topic": "Moments", "front": "Distance measured how?", "back": "Perpendicular to the force"}],
                                   "ask": "", "reply": "Counts!"})
            return json.dumps({"topics": [{"subject": "Chemistry", "topic": "Titration", "state": "solid"}], "cards": [], "ask": "Why slowly near the end?", "reply": "Nice."})
        if "top-band answer" in prompt:
            return json.dumps(["Says who made it", "Cross-checks", "Judges it"])
        if "Stage:" in prompt:
            return "hint " + str(prompt.count("hint"))
        return "ok"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = StudyStore(self.root / "study")
        self.ai = FakeAI()
        self.planner = Planner(self.root / "schedule.json")
        self.svc = StudyService(self.store, self.ai, planner=self.planner, background=False)
        self.eng = self.svc.engine
        self.chem = self.store.subject("Chemistry")["id"]

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def load(self, subject=None):
        return self.svc.import_source(subject, "ws.txt", "text/plain", text="limiting reagent worksheet")

    def run_to(self, sid, kind):
        """Answer cards until the step of this kind is on screen."""
        v = self.eng.view(sid)
        while v["state"] == "open" and v["step"]["type"] != kind:
            if v["step"]["type"] == "card":
                self.eng.answer(sid, {"grade": 2})
            elif v["step"]["type"] == "lesson":
                self.eng.answer(sid, {"option": 1})
            v = self.eng.next(sid)
        return v


class TestHelpers(unittest.TestCase):
    def test_numbers_and_tolerance(self):
        self.assertEqual(_num("0.30 g"), 0.30)
        self.assertEqual(_num("3/4"), 0.75)
        self.assertIsNone(_num("no idea"))
        self.assertTrue(close(0.3, 0.30))
        self.assertTrue(close(0.298, 0.30))
        self.assertFalse(close(0.40, 0.30))
        self.assertFalse(close(None, 0.30))

    def test_scheme_and_quick_shapes(self):
        self.assertEqual([p["point"] for p in scheme_of("M = 18; 36 g")], ["M = 18", "36 g"])
        self.assertEqual(scheme_of([{"point": "x", "marks": "2", "final": 1}])[0], {"point": "x", "marks": 2, "final": True})
        self.assertIsNone(quick_of({"options": ["only one"], "correct": 0}))
        self.assertIsNone(quick_of({"options": ["a", "b"], "correct": 5}))


class TestImport(Base):
    def test_import_keeps_cards_and_real_questions_without_asking(self):
        self.load("Chemistry")
        self.assertEqual(self.store.stats()["approved"], 2)
        self.assertEqual(self.store.stats()["drafts"], 0)
        q = self.store.q("SELECT * FROM questions")
        self.assertEqual(len(q), 1)                         # the empty one is dropped
        self.assertEqual((q[0]["origin"], q[0]["key"], q[0]["ref"], q[0]["marks"], q[0]["numeric"]), ("source", "printed", "Q4", 4, 0.30))

    def test_no_subject_is_filed_by_the_material(self):
        self.ai.subject = "physics"
        src = self.load(None)
        self.assertEqual(self.store.source(src["id"])["subject_id"], self.store.subject("Physics")["id"])
        self.assertEqual(self.eng.inbox()[0]["subject"], "Physics")

    def test_undo_removes_everything_it_made(self):
        src = self.load("Chemistry")
        self.store.remove_source(src["id"])
        self.assertEqual(self.store.stats()["approved"], 0)
        self.assertEqual(self.store.q("SELECT * FROM questions"), [])

    def test_wrong_subject_moves_cards_questions_and_topic(self):
        src = self.load("Chemistry")
        phys = self.store.subject("Physics")["id"]
        self.store.move_source(src["id"], phys)
        self.assertEqual({c["subject_id"] for c in self.store.cards()}, {phys})
        self.assertEqual(self.store.q("SELECT subject_id FROM questions")[0]["subject_id"], phys)
        self.assertEqual([t["name"] for t in self.store.topics(phys)], ["Limiting reagent"])
        self.assertEqual(self.store.topics(self.chem), [])

    def test_reading_again_does_not_duplicate(self):
        src = self.load("Chemistry")
        self.svc.process(src["id"])
        self.assertEqual(self.store.stats()["approved"], 2)
        self.assertEqual(len(self.store.q("SELECT * FROM questions")), 1)


class TestNow(Base):
    def test_empty_then_ready(self):
        self.assertEqual(self.eng.now()["state"], "empty")
        self.assertIsNone(self.eng.nudge())
        self.load("Chemistry")
        n = self.eng.now("phone")
        self.assertEqual(n["state"], "ready")
        self.assertEqual([s["label"] for s in n["steps"]], ["2 cards", "Limiting reagent"])
        self.assertEqual(n["weak"][0]["label"], "new")
        self.assertIn("minutes of limiting reagent", self.eng.nudge())

    def test_test_tomorrow_turns_start_into_test_prep(self):
        self.load("Chemistry")
        self.svc.plan_test("Chemistry", "Moles", (today() + timedelta(days=1)).isoformat(), ["Limiting reagent"], blocks=())
        n = self.eng.now()
        self.assertEqual((n["kind"], n["minutes"]), ("prep", 25))
        self.assertEqual(n["test"]["days"], 1)
        self.assertIn("Limiting reagent", n["headline"])

    def test_week_away_gets_an_easy_comeback_and_no_backlog(self):
        self.load("Chemistry")
        old = (datetime.now(TZ) - timedelta(days=9)).isoformat(timespec="seconds")
        cid = self.store.cards()[0]["id"]
        self.store.run("INSERT INTO reviews(card_id, grade, at, interval) VALUES(?,?,?,?)", (cid, 2, old, 1))
        n = self.eng.now()
        self.assertEqual((n["kind"], n["minutes"]), ("comeback", 6))
        self.assertNotIn("due", json.dumps(n["steps"]).lower())

    def test_backlog_is_spread_not_shown(self):
        for i in range(60):
            self.store.add_card(self.chem, f"q{i}", "a", "approved", "Moles")
        self.eng.start("phone")
        self.assertLessEqual(len(self.store.due(None, 500)), 30)

    def test_homework_due_tomorrow_replaces_the_session(self):
        self.load("Chemistry")
        self.eng.add_homework("Amath", "Ex 7B", (today() + timedelta(days=1)).isoformat(), "Chain rule")
        n = self.eng.now()
        self.assertEqual(n["kind"], "homework")
        v = self.eng.start("desk")
        self.assertEqual(v["step"]["type"], "homework")
        self.eng.answer(v["id"], {})
        self.assertEqual(self.eng.next(v["id"])["state"], "done")
        self.assertTrue(self.eng.done_today())
        self.assertEqual(self.eng.now()["homework"], [])


class TestSession(Base):
    def test_phone_session_cards_lesson_then_a_tapped_question(self):
        self.load("Chemistry")
        v = self.eng.start("phone")
        self.assertEqual(v["types"], ["card", "card", "lesson", "question"])
        self.assertEqual(self.eng.answer(v["id"], {"grade": 2}), {"grade": 2})
        v = self.run_to(v["id"], "lesson")
        self.assertEqual(v["step"]["lesson"]["options"], ["A", "B", "neither"])
        self.assertTrue(self.eng.answer(v["id"], {"option": 1})["correct"])
        v = self.eng.next(v["id"])
        q = v["step"]["q"]
        self.assertEqual((q["mode"], q["origin"], q["ref"], q["source"], q["final_label"], q["unit"]), ("quick", "source", "Q4", "Chem WS 7", "Mass of hydrogen", "g"))
        self.assertNotIn("correct", q)
        r = self.eng.answer(v["id"], {"option": 1, "value": "0.40"})
        self.assertEqual((r["score"], r["max"]), (1, 2))
        self.assertIn("0.3 g", r["points"][1]["why"])
        done = self.eng.next(v["id"])
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["summary"]["cards"], 2)
        self.assertTrue(self.eng.done_today())
        self.assertEqual(self.eng.now()["state"], "done")

    def test_a_session_started_on_the_phone_continues_on_the_pc(self):
        self.load("Chemistry")
        v = self.eng.start("phone")
        self.eng.answer(v["id"], {"grade": 0})
        self.eng.next(v["id"])
        n = self.eng.now("desk")
        self.assertEqual((n["state"], n["session"]), ("open", v["id"]))
        self.assertEqual(self.eng.view(v["id"])["pos"], 1)

    def test_full_marking_ties_marks_to_lines_and_checks_the_number_itself(self):
        self.load("Chemistry")
        v = self.run_to(self.eng.start("desk")["id"], "question")
        self.assertEqual(v["step"]["q"]["mode"], "full")
        r = self.eng.answer(v["id"], {"answer": "n(Mg) = 0.20\nHCl limiting\nn(H2) = 0.20\nmass = 0.40 g"})
        self.assertEqual((r["score"], r["max"]), (2, 4))       # the model said the last line was right; the sum says it is not
        self.assertEqual(r["points"][0]["quote"], "n(Mg) = 0.20")
        self.assertTrue(r["points"][3]["checked"])
        self.assertIn("should be 0.3 g", r["points"][3]["why"])

    def test_unreadable_line_is_not_guessed(self):
        self.load("Chemistry")
        self.ai.marks = [{"i": 0, "got": True}, {"i": 1, "got": True}, {"i": 2, "got": None, "why": "cannot read"}, {"i": 3, "got": True}]
        v = self.run_to(self.eng.start("desk")["id"], "question")
        r = self.eng.answer(v["id"], {"answer": "working ... 0.30 g"})
        self.assertTrue(r["unsure"])
        self.assertIsNone(r["points"][2]["got"])
        self.assertEqual(r["score"], 4)                        # benefit of the doubt, and it comes back
        self.assertEqual(self.store.one("SELECT disputed FROM attempts WHERE id=?", (r["attempt"],))["disputed"], 1)

    def test_i_think_i_am_right_marks_again_and_never_lowers(self):
        self.load("Chemistry")
        v = self.run_to(self.eng.start("desk")["id"], "question")
        r = self.eng.answer(v["id"], {"answer": "n(H2) = 0.15 so 0.30 g"})
        self.assertEqual(r["score"], 3)
        d = self.eng.dispute(r["attempt"])
        self.assertTrue(d["changed"])
        self.assertEqual(d["score"], 4)
        self.assertEqual(len(self.store.q("SELECT * FROM attempts WHERE kind='full'")), 1)

    def test_hints_are_free_and_end_with_the_real_solution(self):
        self.load("Chemistry")
        v = self.run_to(self.eng.start("desk")["id"], "question")
        for n in (1, 2, 3):
            self.assertEqual(len(self.eng.hint(v["id"])["hints"]), n)
        last = self.eng.hint(v["id"])
        self.assertTrue(last["last"])
        self.assertIn("Answer: HCl is limiting; 0.30 g", last["hints"][3])
        self.assertEqual(len(self.eng.view(v["id"])["step"]["hints"]), 4)

    def test_ending_early_counts_only_if_something_was_done(self):
        self.load("Chemistry")
        v = self.eng.start("phone")
        self.assertEqual(self.eng.end(v["id"])["state"], "ended")
        self.assertFalse(self.eng.done_today())
        v = self.eng.start("phone")
        self.eng.answer(v["id"], {"grade": 2})
        self.assertEqual(self.eng.end(v["id"])["state"], "done")

    def test_just_three_cards(self):
        for i in range(5):
            self.store.add_card(self.chem, f"q{i}", "a", "approved", "Moles")
        v = self.eng.start("phone", "mini")
        self.assertEqual(v["types"], ["card"] * 3)

    def test_drilling_one_topic(self):
        self.load("Chemistry")
        tid = self.store.topics(self.chem)[0]["id"]
        v = self.eng.start("desk", "topic", tid)
        self.assertEqual(v["kind"], "topic")
        self.assertIn("question", v["types"])


class TestFlags(Base):
    def test_bad_question_is_binned_and_changes_what_cyph_asks(self):
        self.load("Chemistry")
        t = self.eng.topics(self.chem)[0]
        made = self.eng._write_questions(t, 1)
        self.assertEqual(len(made), 1)
        r = self.eng.flag_question(made[0], "not_in_notes")
        self.assertIn("only ask from your own worksheets", r["rule"])
        self.assertEqual(self.eng._write_questions(t, 1), [])   # never again for this topic
        self.assertEqual([q["origin"] for q in self.store.q("SELECT origin FROM questions WHERE state='active'")], ["source"])
        self.assertEqual(self.eng.topic_detail(t["id"])["rules"], [r["rule"]])

    def test_bad_card_never_comes_back(self):
        self.load("Chemistry")
        cid = self.store.cards()[0]["id"]
        self.eng.flag_card(cid)
        self.assertNotIn(cid, [c["id"] for c in self.store.due()])


class TestStrength(Base):
    def test_words_not_numbers(self):
        self.load("Chemistry")
        t = self.eng.topics(self.chem)[0]
        self.assertEqual(self.eng.strength(t["id"]), {"score": None, "label": "new"})
        self.store.add_attempt(self.chem, "Limiting reagent", "full", "q", "a", 1, 4, "")
        self.assertEqual(self.eng.strength(t["id"])["label"], "weak")
        for _ in range(7):
            self.store.add_attempt(self.chem, "Limiting reagent", "full", "q", "a", 4, 4, "")
        self.assertEqual(self.eng.strength(t["id"])["label"], "solid")

    def test_teacher_mark_outweighs_cyph(self):
        self.store.add_attempt(self.chem, "Titration", "full", "q", "a", 4, 4, "")
        self.eng.teacher_mark("Chemistry", "Titration", 3, 10)
        tid = self.store.topic(self.chem, "Titration")
        self.assertEqual(self.eng.strength(tid)["label"], "weak")

    def test_library_and_topic_detail(self):
        self.load("Chemistry")
        lib = self.eng.library()
        chem = next(s for s in lib["subjects"] if s["name"] == "Chemistry")
        self.assertEqual((chem["label"], chem["topics"][0]["cards"], chem["topics"][0]["questions"]), ("new", 2, 1))
        d = self.eng.topic_detail(chem["topics"][0]["id"])
        self.assertEqual((len(d["card_list"]), d["question_list"][0]["ref"], len(d["notes"])), (2, "Q4", 1))


class TestDebrief(Base):
    def test_one_line_then_one_follow_up_then_cards(self):
        r = self.eng.debrief("chem was titration")
        self.assertEqual((r["ask"], r["done"]), ("Why slowly near the end?", False))
        self.assertTrue(self.eng.now()["debrief"]["done"])       # one line already counts
        r = self.eng.debrief("so you dont overshoot. physics was moments")
        self.assertTrue(r["done"])
        self.assertEqual(r["cards"], 1)                          # the made-up subject is skipped, not an error
        phys = self.store.subject("Physics")["id"]
        self.assertEqual(self.eng.strength(self.store.topic(phys, "Moments"))["label"], "weak")
        self.assertEqual(self.eng.strength(self.store.topic(self.chem, "Titration"))["label"], "solid")
        self.assertEqual(self.store.stats()["approved"], 1)

    def test_empty_debrief_is_refused(self):
        with self.assertRaises(StudyError):
            self.eng.debrief("")


class TestPurpose(Base):
    def test_project_link_only_when_there_is_one(self):
        self.load("Chemistry")
        t = self.eng.topics(self.chem)[0]
        self.eng.lesson(t)
        self.assertEqual(self.store.one("SELECT why FROM topics WHERE id=?", (t["id"],))["why"], "")
        self.eng.projects = lambda: "Building a battery pack for the arm."
        self.eng.lesson(t, fresh=True)
        self.assertIn("battery", self.store.one("SELECT why FROM topics WHERE id=?", (t["id"],))["why"])
        v = self.eng.start("phone")
        self.assertIn("battery", v["step"]["card"]["why"])


class TestWriting(Base):
    def test_ss_is_planned_and_self_checked_never_marked(self):
        ss = self.store.subject("SS")["id"]
        self.ai.subject = "SS"
        self.load("SS")
        v = self.run_to(self.eng.start("phone")["id"], "question")
        self.assertEqual(v["step"]["q"]["mode"], "plan")
        self.assertEqual(len(v["step"]["q"]["plan"]), 4)
        n = len(self.ai.calls)
        self.assertEqual(self.eng.answer(v["id"], {"answer": "1 who 2 why", "had": False})["had"], False)
        self.assertEqual(len(self.ai.calls), n)                   # no model call: Cyph does not pretend to mark it
        self.assertEqual(self.store.q("SELECT kind FROM attempts WHERE subject_id=?", (ss,))[-1]["kind"], "plan")

    def test_weekly_paragraph_on_the_desk_uses_a_real_question(self):
        self.ai.subject = "SS"
        self.load("SS")
        p = self.eng.plan("desk")
        self.assertIsNotNone(p["paragraph"])
        v = self.run_to(self.eng.start("desk")["id"], "paragraph")
        self.assertEqual(v["step"]["q"]["mode"], "paragraph")
        r = self.eng.answer(v["id"], {"ticks": [True, True, False, False]})
        self.assertEqual((r["ticked"], r["of"]), (2, 4))
        self.assertIsNone(self.eng.plan("desk")["paragraph"])     # once a week


class TestReminders(Base):
    def test_chat_nudge_is_said_once_a_day_and_not_after_the_session(self):
        self.load("Chemistry")
        first = self.eng.chat_nudge()
        self.assertIn("never a condition", first)
        self.assertEqual(self.eng.chat_nudge(), "")

    def test_proactive_sends_the_session_line_once(self):
        from cyph.proactive import Proactive
        self.load("Chemistry")
        sent = []
        p = Proactive(self.planner, sent.append, self.root / "c.json", self.root / "st.json")
        p.study, p.study_nudge = self.store, self.eng.nudge
        now = datetime.now(TZ).replace(hour=16, minute=40)
        if now.weekday() >= 5 or True:
            p.update({"school": ["08:00", "15:00"]})
        self.assertEqual(p.tick(now), 1)
        self.assertIn("Home yet?", sent[0])
        self.assertEqual(p.tick(now), 0)


class TestChatTools(Base):
    def test_tests_homework_marks_and_debrief_from_chat(self):
        tb = ToolBox(self.planner, study=self.svc)
        day = (today() + timedelta(days=3)).isoformat()
        r = tb.run("study_add_test", {"subject": "Chemistry", "title": "Moles", "date": day, "topics": ["Limiting reagent"]})
        self.assertEqual(r["added"]["date"], day)
        self.assertEqual(self.store.tests()[0]["blocks"], [])     # no revision blocks cluttering the plan
        r = tb.run("study_add_homework", {"subject": "Amath", "title": "Ex 7B", "due": day})
        self.assertEqual(r["added"]["subject"], "Amath")
        self.assertEqual(tb.run("study_teacher_mark", {"subject": "Chemistry", "topic": "Titration", "score": 7, "max": 10})["summary"], "7/10")
        self.assertEqual(tb.run("study_debrief", {"text": "chem was titration"})["ask_him"], "Why slowly near the end?")
        self.assertEqual(tb.run("study_now", {})["state"], "empty")
        self.assertIn("error", tb.run("study_add_homework", {"subject": "Alchemy", "title": "x", "due": day}))


class TestRoutes(Base):
    def setUp(self):
        super().setUp()
        class Hub:
            def publish(self, ev): pass
        self.devices = DeviceStore(self.root / "devices.json")
        deps = Deps(devices=self.devices, planner=self.planner, store=MemoryStore(self.root / "mem"), brain=None, log=lambda *a: None,
                    pair_limiter=Limiter(5, 600, 900), auth_limiter=Limiter(30, 60, 300), hub=Hub(), study=self.svc)
        self.c = TestClient(create_app(deps))
        self.h = {"Authorization": "Bearer " + self.c.post("/pair", json={"code": self.devices.new_pair_code(), "name": "t"}).json()["token"]}

    def test_the_whole_loop_over_http(self):
        self.assertEqual(self.c.get("/study/now").status_code, 401)
        self.assertEqual(self.c.get("/study/now", headers=self.h).json()["state"], "empty")
        self.assertEqual(self.c.post("/study/session", json={"device": "phone"}, headers=self.h).status_code, 409)
        r = self.c.post("/study/inbox", json={"title": "ws.txt", "mime": "text/plain", "text": "worksheet"}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        item = self.c.get("/study/inbox", headers=self.h).json()["items"][0]
        self.assertEqual((item["subject"], item["topic"], item["cards"], item["questions"], item["status"]), ("Chemistry", "Limiting reagent", 2, 1, "ready"))
        v = self.c.post("/study/session", json={"device": "phone"}, headers=self.h).json()
        zid = v["id"]
        for _ in range(2):
            self.assertEqual(self.c.post(f"/study/session/{zid}/answer", json={"grade": 1}, headers=self.h).json()["grade"], 2)
            v = self.c.post(f"/study/session/{zid}/next", headers=self.h).json()
        self.assertEqual(v["step"]["type"], "lesson")
        self.c.post(f"/study/session/{zid}/answer", json={"option": 0}, headers=self.h)
        v = self.c.post(f"/study/session/{zid}/next", headers=self.h).json()
        qid = v["step"]["q"]["id"]
        self.assertEqual(len(self.c.post(f"/study/session/{zid}/hint", json={}, headers=self.h).json()["hints"]), 1)
        a = self.c.post(f"/study/session/{zid}/answer", json={"option": 0, "value": "0.3"}, headers=self.h).json()
        self.assertEqual(a["score"], 1)
        self.assertTrue(self.c.post(f"/study/attempts/{a['attempt']}/dispute", headers=self.h).json()["changed"])
        self.assertIn("Binned", self.c.post(f"/study/questions/{qid}/flag", json={"reason": "wrong"}, headers=self.h).json()["rule"])
        self.assertEqual(self.c.post(f"/study/session/{zid}/next", headers=self.h).json()["state"], "done")
        lib = self.c.get("/study/library", headers=self.h).json()
        tid = next(s for s in lib["subjects"] if s["name"] == "Chemistry")["topics"][0]["id"]
        self.assertEqual(self.c.get(f"/study/topics/{tid}", headers=self.h).json()["name"], "Limiting reagent")
        self.assertEqual(self.c.post(f"/study/sources/{item['id']}/move", json={"subject": "Physics"}, headers=self.h).status_code, 200)
        self.assertEqual(self.c.post("/study/sources/nope/move", json={"subject": "Physics"}, headers=self.h).status_code, 404)
        self.assertEqual(self.c.post("/study/debrief", json={"text": "chem was titration"}, headers=self.h).json()["ask"], "Why slowly near the end?")
        self.assertEqual(self.c.post("/study/syllabus", json={"subject": "Geog", "topics": ["Plate tectonics", "Coasts"]}, headers=self.h).json()["added"], 2)
        self.assertEqual(self.c.post("/study/homework", json={"subject": "Geog", "title": "Map", "due": "2030-01-01"}, headers=self.h).status_code, 200)


if __name__ == "__main__":
    unittest.main()
