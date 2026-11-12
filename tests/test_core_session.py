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
from cyph.core.study import StudyError, StudyService, StudyStore, parse_json, today
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
        self.calls, self.marks, self.subject, self.key_ok = [], None, "Chemistry", False
        self.final, self.parts, self.solve, self.written = None, [], None, None

    def embed(self, texts):
        return [[float(len(t))] for t in texts]

    def generate(self, system, prompt, parts=()):
        self.calls.append(prompt)
        self.parts = list(parts)
        if "Solve this fully" in prompt:
            return json.dumps({"final": self.solve})
        if "exam questions on the topic" in prompt and self.written:
            return json.dumps(self.written)
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
            return json.dumps({"points": pts, "final": self.final, "headline": "Nearly.", "slip": "used the wrong reagent"})
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
        if "Is this answer key right" in prompt:
            return json.dumps({"key_ok": self.key_ok, "why": "x"})
        if "Stage:" in prompt:
            return "hint " + str(prompt.count("hint"))
        return "ok"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = StudyStore(self.root / "study")
        self.store.run("DELETE FROM topics")                   # the seeded maths topics have their own tests (TestMaths)
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
        self.assertEqual(scheme_of([{"point": "x", "marks": "2", "final": 1}])[0], {"point": "x", "marks": 2, "final": True, "type": ""})
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
        self.assertEqual(r["score"], 3)                        # a line that cannot be read earns nothing yet, and the question comes back
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
        with self.assertRaises(StudyError):                    # the full solution waits until he has had a go
            self.eng.hint(v["id"])
        self.eng.answer(v["id"], {"answer": "my try 0.30 g"})
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


QUAD = {"text": "Solve x^2 - 5x + 6 = 0.", "marks": 3, "topic": "Quadratic equations and functions", "answer": "x = 2 or x = 3", "key": "cyph",
        "scheme": [{"point": "Factorise (x-2)(x-3)", "marks": 1, "type": "M"}, {"point": "Set each factor to zero", "marks": 1, "type": "M"},
                   {"point": "x = 2 or x = 3", "marks": 1, "type": "A", "final": True}],
        "answers": {"kind": "set", "values": [2, 3], "text": "x = 2 or 3"}}
ROOTS = [{"i": 0, "got": True, "quote": "(x-2)(x-3)"}, {"i": 1, "got": True, "quote": "x-2=0"}, {"i": 2, "got": True, "quote": "x=2"}]


class TestMaths(Base):
    def setUp(self):
        super().setUp()
        self.math = self.store.subject("Math")["id"]
        self.topic = self.store.topic(self.math, "Quadratic equations and functions")

    def ask(self, spec=QUAD, **extra):
        from cyph.core.session import save_questions
        qid = save_questions(self.store, {"id": None, "subject_id": self.math, "title": ""}, [{**spec, **extra}], "Quadratics", origin="cyph")[0]
        self.store.run("UPDATE questions SET topic_id=? WHERE id=?", (self.topic, qid))
        return self.store.one("SELECT * FROM questions WHERE id=?", (qid,))

    def mark(self, q, answer, marks=None, final=None):
        self.ai.marks, self.ai.final = marks or ROOTS, final
        return self.eng._mark_full(q, {"name": "Quadratics"}, answer, [], 0)

    def test_fresh_store_has_maths_topics_and_only_those(self):
        with tempfile.TemporaryDirectory() as d:
            st = StudyStore(Path(d))
            names = lambda sub: [t["name"] for t in st.topics(st.subject(sub)["id"])]      # noqa: E731
            self.assertIn("Surds", names("Amath"))
            self.assertIn("Bearings", names("Math"))
            self.assertEqual(names("Chemistry"), [])
            st.run("DELETE FROM topics WHERE name='Surds'")
            st.seed()
            self.assertNotIn("Surds", names("Amath"))          # what he removed stays removed
            st.close()

    def test_topic_names_do_not_drift(self):
        self.assertEqual(self.store.topic(self.math, "Quadratic Equations and Functions"), self.topic)
        self.assertEqual(self.store.topic(self.math, "quadratic functions"), self.store.topic(self.math, "Quadratic function"))

    def test_both_roots_are_needed(self):
        q = self.ask()
        self.assertEqual(self.mark(q, "x = 2")["score"], 2)                 # the final A mark is checked in code: one root is not both
        r = self.mark(q, "(x-2)(x-3)=0 so x = 2 or 3")
        self.assertEqual(r["score"], 3)

    def test_a_mark_needs_its_method_mark(self):
        q = self.ask()
        marks = [{"i": 0, "got": False}, {"i": 1, "got": False}, {"i": 2, "got": True}]
        r = self.mark(q, "x = 2 or 3", marks)
        self.assertEqual(r["score"], 0)
        self.assertIn("No method", r["points"][2]["why"])

    def test_nothing_usable_in_the_reply_earns_nothing(self):
        q = self.ask()
        with self.assertRaises(StudyError):
            self.mark(q, "I don't know", [{"i": 9, "got": True}])
        r = self.mark(q, "I don't know, x = 7", [{"i": 0, "got": "false"}, {"i": 1, "got": "no"}, {"i": 2, "got": None}])
        self.assertEqual(r["score"], 0)
        self.assertTrue(r["unsure"])

    def test_no_final_flag_still_checks_the_answer(self):
        spec = {**QUAD, "scheme": [{"point": "Factorise", "marks": 1}, {"point": "Roots", "marks": 1}]}
        r = self.mark(self.ask(spec), "x = 5", [{"i": 0, "got": True}, {"i": 1, "got": True}])
        self.assertEqual(r["score"], 1)                                      # the last point is the answer, and 5 is not 2 or 3

    def test_show_that_is_not_given_for_the_result(self):
        spec = {**QUAD, "text": "Show that x = 2 is a root of x^2 - 5x + 6 = 0.", "show": True, "answers": {"kind": "number", "values": [2]}}
        r = self.mark(self.ask(spec), "x = 5 hence shown", [{"i": 0, "got": False}, {"i": 1, "got": False}, {"i": 2, "got": False}])
        self.assertEqual(r["score"], 0)

    def test_answer_is_marked_once_and_disputed_once(self):
        self.store.add_card(self.math, "f", "b", "approved", "Quadratic equations and functions")
        q = self.ask()
        r = self.mark(q, "x = 2")
        self.assertEqual(r["score"], 2)
        self.ai.marks = [{"i": 0, "got": True}, {"i": 1, "got": True}, {"i": 2, "got": True}]
        d = self.eng.dispute(r["attempt"])
        self.assertEqual(d["score"], 2)                                       # code still says one root is missing, so nothing is given away
        again = self.eng.dispute(r["attempt"])
        self.assertFalse(again["changed"])
        self.assertIn("twice", again["headline"])

    def test_tapped_answer_dispute_checks_the_key(self):
        q = self.ask(quick={"ask": "x?", "options": ["2", "3", "4"], "correct": 2, "explain": "e"})
        r = self.eng._mark_quick(q, {"name": "Q"}, {"option": 0}, 0)
        self.ai.key_ok = True
        self.assertFalse(self.eng.dispute(r["attempt"])["changed"])
        r2 = self.eng._mark_quick(q, {"name": "Q"}, {"option": 0}, 0)
        self.ai.key_ok = False
        self.assertTrue(self.eng.dispute(r2["attempt"])["changed"])
        self.assertEqual(self.store.one("SELECT state FROM questions WHERE id=?", (q["id"],))["state"], "binned")

    def test_the_figure_reaches_the_model(self):
        fid = self.store.put_file("fig.png", "image/png", b"\x89PNG")
        q = self.ask()
        self.store.run("UPDATE questions SET figure=? WHERE id=?", (fid, q["id"]))
        q = self.store.one("SELECT * FROM questions WHERE id=?", (q["id"],))
        self.mark(q, "x = 2 or 3")
        self.assertEqual(self.ai.parts[0]["mime"], "image/png")

    def test_hint_ladder_is_server_ordered(self):
        self.store.run("UPDATE questions SET state='binned'")
        q = self.ask()
        self.store.add_card(self.math, "f", "b", "approved", "Quadratic equations and functions")
        v = self.eng.start("desk")
        sid = v["id"]
        v = self.run_to(sid, "question")
        with self.assertRaises(StudyError):
            for _ in range(4):
                self.eng.hint(sid, 3)                                          # asking for the answer first just gives the next rung
        self.assertEqual(len(self.eng.view(sid)["step"]["hints"]), 3)

    def test_maths_needs_no_notes_and_wrong_keys_are_binned(self):
        topic = next(t for t in self.eng.topics(self.math) if t["id"] == self.topic)
        self.ai.written = [{**QUAD, "text": "Solve x^2 - 5x + 6 = 0 (good)"}]
        self.ai.solve = "x = 2 or 3"
        self.assertEqual(len(self.eng._write_questions(topic, 1)), 1)
        self.ai.written = [{**QUAD, "text": "Solve a different one"}]
        self.ai.solve = "x = 9"                                                # an independent solve disagrees with the key
        self.assertEqual(self.eng._write_questions(topic, 1), [])
        self.assertEqual(len(self.store.q("SELECT 1 FROM questions WHERE state='binned'")), 1)

    def test_old_results_count_for_less_and_solid_does_not_last_forever(self):
        t = self.topic
        for i in range(3):
            self.store.add_attempt(self.math, "Quadratic equations and functions", "full", "q", "a", 3, 3, "")
        fresh = self.eng.strength(t)
        self.assertEqual(fresh["label"], "solid")
        self.store.run("UPDATE attempts SET at=?", ((datetime.now(TZ) - timedelta(days=80)).isoformat(timespec="seconds"),))
        old = self.eng.strength(t)
        self.assertLess(old["score"], fresh["score"])
        self.store.add_attempt(self.math, "Quadratic equations and functions", "quick", "q", "a", 1, 1, "")
        self.store.add_attempt(self.math, "Quadratic equations and functions", "quick", "q", "a", 0, 1, "")
        self.assertIn(self.eng.strength(t)["label"], ("solid", "okay"))

    def test_a_quiet_subject_gets_its_turn(self):
        amath = self.store.subject("Amath")["id"]
        self.store.topic(amath, "Surds")
        self.store.add_attempt(self.math, "Quadratic equations and functions", "full", "q", "a", 0, 3, "")     # Math is weak and busy
        self.store.add_attempt(self.math, "Quadratic equations and functions", "full", "q", "a", 0, 3, "")
        t = self.eng._pick_topic()
        self.assertEqual(t["subject_id"], amath)                              # Amath has been quiet for days, so it goes first

    def test_cards_are_not_scheduled_past_the_next_test(self):
        c = self.store.add_card(self.math, "f", "b", "approved", "Quadratic equations and functions")
        for _ in range(3):
            c = self.store.review(c["id"], 2)
        self.assertGreater(c["interval"], 1)
        self.store.add_test(self.math, "Prelim", (today() + timedelta(days=6)).isoformat(), [])
        c = self.store.review(c["id"], 2)
        self.assertLessEqual(c["due"], (today() + timedelta(days=5)).isoformat())

    def test_latex_survives_json(self):
        got = parse_json(r'{"text": "$\frac{1}{2} \times \pi r^2$ and \right"}')
        self.assertEqual(got["text"], r"$\frac{1}{2} \times \pi r^2$ and \right")


class TestEverySubject(Base):
    """All seven subjects get their own marking rules and a working path from question to mark."""
    def test_rules_per_subject(self):
        from cyph.core.study import SUBJECTS, subject_rules, sysfor
        for name, _ in SUBJECTS:
            self.assertTrue(subject_rules(name), name)
            self.assertIn(name, sysfor(name))
        self.assertIn("Additional Mathematics", subject_rules("Amath"))
        self.assertIn("Elementary Mathematics", subject_rules("Math"))
        self.assertIn("3 significant figures", subject_rules("Amath"))
        self.assertIn("command words", subject_rules("Chemistry"))
        self.assertIn("provenance", subject_rules("SS"))
        self.assertIn("place-specific", subject_rules("Geog"))
        self.assertIn("task fulfilment", subject_rules("English"))

    def test_a_session_runs_in_each_subject(self):
        from cyph.core.session import save_questions
        for name, _ in __import__("cyph.core.study", fromlist=["SUBJECTS"]).SUBJECTS:
            sid = self.store.subject(name)["id"]
            q = save_questions(self.store, {"id": None, "subject_id": sid, "title": ""}, [{**Q4, "topic": "T " + name, "numeric": None, "answers": None}],
                               "T " + name, origin="cyph")[0]
            tid = self.store.one("SELECT topic_id FROM questions WHERE id=?", (q,))["topic_id"]
            self.store.add_note(sid, "notes", "some notes", "T " + name) if hasattr(self.store, "add_note") else None
            v = self.eng.start("desk", "topic", tid)
            v = self.run_to(v["id"], "question")
            self.assertEqual(v["state"], "open", name)
            mode = v["step"]["q"]["mode"]
            self.assertEqual(mode, "plan" if name in ("English", "SS") else "full", name)
            r = self.eng.answer(v["id"], {"had": True} if mode == "plan" else {"answer": "working and an answer 0.30 g"})
            self.assertIn("mode", r)
            self.eng.end(v["id"])
