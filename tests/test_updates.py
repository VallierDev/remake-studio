import json
from pathlib import Path

import unittest
from unittest.mock import patch
import test_workbench
import workbench


class AutoUpdateTests(unittest.TestCase):
    setUp = test_workbench.WorkbenchTests.setUp
    tearDown = test_workbench.WorkbenchTests.tearDown

    def test_schedule_is_persistent_and_single_flight(self):
        name = workbench.schedule_update()
        self.assertIsNotNone(name)
        self.assertIsNone(workbench.schedule_update())
        self.assertEqual(workbench.schedule_update(force=True), name)
        with workbench.database() as db:
            row = db.execute("SELECT options FROM jobs WHERE run=?", (name,)).fetchone()
            count = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
        options = json.loads(row["options"])
        self.assertEqual(options["mode"], "scout")
        self.assertTrue(options["scheduled"])
        self.assertEqual(count, 1)
        self.assertNotIn("--flow", workbench.command(name, options))
        config = workbench.settings_snapshot()
        with workbench.database() as db:
            db.execute("UPDATE jobs SET status='completed' WHERE run=?", (name,))
        self.assertIsNone(workbench.schedule_update())
        workbench.initialize()
        self.assertEqual(workbench.settings_snapshot()["next_run"], config["next_run"])
        with patch.object(workbench.time, "time", return_value=config["next_run"] + 1):
            self.assertNotEqual(workbench.schedule_update(), name)

    def test_pause_removes_queued_updates_and_preserves_selection(self):
        name = workbench.schedule_update()
        response = self.client.put("/api/settings", json={"enabled":False,"topic":"fashion","interval_hours":24})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["enabled"])
        with workbench.database() as db:
            self.assertEqual(db.execute("SELECT status FROM jobs WHERE run=?",(name,)).fetchone()[0], "cancelled")
        self.assertIsNone(workbench.schedule_update())
        response = self.client.post("/api/tasks",json={})
        self.assertEqual(response.status_code,202)
        with workbench.database() as db:
            data=json.loads(db.execute("SELECT options FROM jobs WHERE run=?",(response.json()["run"],)).fetchone()[0])
        self.assertEqual(data["queries"],workbench.TOPICS["fashion"]["queries"])

    def test_scheduled_job_refreshes_previously_collected_urls(self):
        root=workbench.RUNS/"old"
        root.mkdir()
        (root/"candidates.json").write_text(json.dumps({"checked_at":"2026-11-12T00:00:00Z",
             "candidates":[{"id":"11","url":"https://www.tiktok.com/@dress/video/11","views":1234,"weighted_engagement_rate":.01}]}))
        name=workbench.schedule_update()
        with workbench.database() as db:
            options=json.loads(db.execute("SELECT options FROM jobs WHERE run=?",(name,)).fetchone()[0])
        self.assertIn("https://www.tiktok.com/@dress/video/11",Path(options["seed_file"]).read_text())
        self.assertIn("--seed-file",workbench.command(name,options))
