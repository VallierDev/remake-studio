import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import workbench


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        base = Path(self.temp.name)
        self.patches = [
            patch.object(workbench, "STATE", base / "state"),
            patch.object(workbench, "RUNS", base / "runs"),
            patch.object(workbench, "worker", lambda: None),
        ]
        for patcher in self.patches:
            patcher.start()
        self.client = TestClient(workbench.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def test_scout_task_is_queued_with_literal_keyword_argument(self):
        keywords = 'christmas dress $(touch /tmp/never-execute)'
        response = self.client.post("/api/tasks", json={"keywords": keywords})
        self.assertEqual(response.status_code, 202)
        run = response.json()["run"]
        listing = self.client.get("/api/runs").json()
        self.assertEqual(listing[0]["status"], "queued")
        self.assertEqual(listing[0]["run"], run)
        with workbench.database() as db:
            row = db.execute("SELECT options FROM jobs WHERE run=?", (run,)).fetchone()
        cmd = workbench.command(run, json.loads(row["options"]))
        self.assertIn(keywords, cmd)
        self.assertIn("--scout-only", cmd)
        self.assertNotIn("--flow", cmd)

    def test_secret_checkpoints_and_symlink_targets_are_not_served(self):
        root = workbench.RUNS / "sample"
        root.mkdir()
        (root / "flow2api-job.json").write_text('{"secret":"private"}')
        outside = Path(self.temp.name) / "private.json"
        outside.write_text("private")
        (root / "acceptance.json").symlink_to(outside)
        self.assertEqual(self.client.get("/api/runs/sample/artifact/flow2api-job.json").status_code, 404)
        self.assertEqual(self.client.get("/api/runs/sample/artifact/acceptance.json").status_code, 404)

    def test_video_ranges_and_cross_origin_submit(self):
        root = workbench.RUNS / "sample"
        root.mkdir()
        (root / "final.mp4").write_bytes(b"test-video")
        response = self.client.get("/api/runs/sample/artifact/final.mp4", headers={"Range": "bytes=0-3"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"test")
        response = self.client.post("/api/tasks", json={"keywords":"dress"},
                                    headers={"Origin":"https://outside.example"})
        self.assertEqual(response.status_code, 403)

    def test_resume_keeps_generation_checkpoint(self):
        response = self.client.post("/api/tasks", json={"keywords":"dress", "mode":"generate"})
        run = response.json()["run"]
        root = workbench.RUNS / run
        root.mkdir()
        (root / "result.json").write_text("{}")
        checkpoint = root / "flow2api-job.json"
        checkpoint.write_text('{"status":"submitting"}')
        with workbench.database() as db:
            db.execute("UPDATE jobs SET status='interrupted' WHERE run=?", (run,))
        self.assertEqual(self.client.post(f"/api/runs/{run}/resume").status_code, 202)
        self.assertEqual(json.loads(checkpoint.read_text())["status"], "submitting")


if __name__ == "__main__":
    unittest.main()
