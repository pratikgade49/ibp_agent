import tempfile
import unittest
from pathlib import Path

from customer_base_agent.run_store import ActiveRunExists, RunNotFound, RunStore


class RunStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = RunStore(str(Path(self.temp_dir.name) / "runs.sqlite3"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_only_one_active_run_per_scenario(self):
        self.store.create("PLAN_A", "SCENARIO_A", "planner-a", {"input": 1}, {"preview": []})
        with self.assertRaises(ActiveRunExists):
            self.store.create("PLAN_A", "SCENARIO_A", "planner-b", {"input": 2}, {"preview": []})

    def test_submitted_import_holds_lock_until_reconciled(self):
        run = self.store.create("PLAN_A", "SCENARIO_A", "planner-a", {}, {})
        self.store.transition(run["run_id"], "planner-a", "prepared", "importing")
        self.store.transition(run["run_id"], "planner-a", "importing", "submitted")
        with self.assertRaises(ActiveRunExists):
            self.store.create("PLAN_A", "SCENARIO_A", "planner-b", {}, {})
        self.store.resolve_submitted(run["run_id"], "planner-a", completed=True)
        self.store.create("PLAN_A", "SCENARIO_A", "planner-b", {}, {})

    def test_different_scenarios_can_run_independently(self):
        first = self.store.create("PLAN_A", "SCENARIO_A", "planner-a", {"input": 1}, {"preview": []})
        second = self.store.create("PLAN_B", "SCENARIO_B", "planner-b", {"input": 2}, {"preview": []})
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_same_scenario_id_in_different_parent_versions_is_independent(self):
        first = self.store.create("PLAN_A", "SCENARIO", "planner-a", {}, {})
        second = self.store.create("PLAN_B", "SCENARIO", "planner-b", {}, {})
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_run_is_private_to_requester(self):
        run = self.store.create("PLAN_A", "SCENARIO_A", "planner-a", {}, {})
        with self.assertRaises(RunNotFound):
            self.store.get(run["run_id"], "planner-b")


if __name__ == "__main__":
    unittest.main()