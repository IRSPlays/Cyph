from pathlib import Path

from tests.test_core_sync import SyncCase

from cyph.core.proposals import Proposals
from cyph.core.toolbox import ToolBox
from cyph.memory import Rejected


class TestProposals(SyncCase):
    def setUp(self):
        super().setUp()
        self.props = Proposals(Path(self.tmp.name) / "proposals.json")
        self.deps.proposals = self.props

    def test_model_proposes_and_a_person_decides(self):
        tb = ToolBox(self.planner, proposals=self.props)
        self.assertIn("memory_propose", [t["function"]["name"] for t in tb.tools()])
        self.assertNotIn("memory_propose", [t["function"]["name"] for t in ToolBox(self.planner).tools()])
        res = tb.run("memory_propose", {"file": "user", "text": "Plays Valorant on Fridays", "why": "he said so"})
        self.assertEqual(res["proposed"], "Plays Valorant on Fridays")
        self.assertIn("error", tb.run("memory_propose", {"file": "user", "text": "my api key is AIzaSyA1234567890123456789012345"}))
        self.assertIn("error", tb.run("memory_propose", {"file": "nope", "text": "x"}))
        h = self.headers()
        items = self.client.get("/memory/proposals", headers=h).json()["proposals"]
        self.assertEqual([p["text"] for p in items], ["Plays Valorant on Fridays"])
        self.assertEqual(self.client.get("/memory/user", headers=h).json()["entries"], [])      # nothing saved yet
        r = self.client.post(f"/memory/proposals/{items[0]['id']}/accept", headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get("/memory/user", headers=h).json()["entries"], ["Plays Valorant on Fridays"])
        self.assertEqual(self.client.get("/memory/proposals", headers=h).json()["proposals"], [])
        self.assertEqual(self.client.post(f"/memory/proposals/{items[0]['id']}/accept", headers=h).status_code, 404)

    def test_reject_drops_it_and_duplicates_are_not_stacked(self):
        a = self.props.add("people", "Mochi is his cat")
        with self.assertRaises(Rejected):
            self.props.add("people", "mochi is his cat")
        self.assertEqual(self.client.post(f"/memory/proposals/{a['id']}/reject", headers=self.headers()).json()["applied"], 0)
        self.assertEqual(self.props.list(), [])
