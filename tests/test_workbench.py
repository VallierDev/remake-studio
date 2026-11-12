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
            patch.object(workbench, "worker", lambda *_: None),
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

    def test_library_has_direct_entry_and_fresh_assets(self):
        response = self.client.get("/videos")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="viral-nav"', response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        asset = self.client.get("/static/app.js")
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.headers["cache-control"], "no-store")

    def test_video_library_deduplicates_and_uses_latest_metrics(self):
        for name, date, views, rate in (("old", "2026-11-10T00:00:00Z", 900000, .09),
                                       ("new", "2026-11-12T00:00:00Z", 80000, .03)):
            root = workbench.RUNS / name
            root.mkdir()
            data = {"checked_at": date, "queries": [name], "candidates": [{
                "id": "11", "url": "https://www.tiktok.com/@dress/video/11",
                "author": "dress", "description": "Christmas red dress",
                "views": views, "likes": 100, "weighted_engagement_rate": rate,
            }]}
            if name == "new":
                data["candidates"].append({
                    "id": "22", "url": "https://www.tiktok.com/@other/video/22",
                    "views": 200000, "weighted_engagement_rate": .01,
                    "description": "Holiday outfit", "author": "other",
                })
            (root / "candidates.json").write_text(json.dumps(data))
        complete = self.client.get("/api/videos").json()
        self.assertEqual(complete["total"], 2)
        result = self.client.get("/api/videos?min_views=50000&min_engagement=2").json()
        self.assertEqual(result["verified_total"], 2)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["videos"][0]["views"], 80000)
        self.assertEqual(result["videos"][0]["source_run"], "new")
        self.assertEqual(result["videos"][0]["queries"], ["new", "old"])
        self.assertEqual(self.client.get("/api/videos?q=CHRISTMAS").json()["total"], 1)
        self.assertEqual(self.client.get("/api/videos?min_views=100000&min_engagement=2").json()["total"], 0)
        sorted_result = self.client.get("/api/videos?min_engagement=0").json()
        self.assertEqual([row["id"] for row in sorted_result["videos"]], ["22", "11"])
        self.assertEqual(self.client.get("/api/videos?sort=engagement&min_engagement=0").json()["videos"][0]["id"], "11")

    def test_product_search_queues_real_discovery_and_maps_chinese(self):
        result = self.client.post("/api/videos/search", json={"query":"圣诞挂件"})
        self.assertEqual(result.status_code, 202)
        run = result.json()["run"]
        self.assertEqual(self.client.post("/api/videos/search",json={"query":"圣诞挂件"}).json()["run"],run)
        with workbench.database() as db:
            data = json.loads(db.execute("SELECT options FROM jobs WHERE run=?",(run,)).fetchone()[0])
        self.assertEqual(data["mode"],"scout")
        self.assertEqual(data["keywords"],"christmas ornaments")
        self.assertIn("--query",workbench.command(run,data))
        self.assertEqual(workbench.product_query("圣诞树"),"christmas tree")
        self.assertEqual(workbench.product_query("圣诞晚礼服"),"christmas evening dress")

    def test_remake_locks_exact_video_and_is_idempotent(self):
        root=workbench.RUNS/"source"
        root.mkdir()
        (root/"candidates.json").write_text(json.dumps({"checked_at":"2026-11-12T00:00:00Z",
            "candidates":[{"id":"11","url":"https://www.tiktok.com/@dress/video/11",
                           "description":"Reference dress","views":100000,"weighted_engagement_rate":.05}]}))
        payload={"product":"圣诞晚礼服","request_id":"fixture-request-1"}
        response=self.client.post("/api/videos/11/remake",json=payload)
        self.assertEqual(response.status_code,202)
        run=response.json()["run"]
        self.assertEqual(self.client.post("/api/videos/11/remake",json=payload).json()["run"],run)
        with workbench.database() as db:
            data=json.loads(db.execute("SELECT options FROM jobs WHERE run=?",(run,)).fetchone()[0])
        cmd=workbench.command(run,data)
        self.assertIn("--url",cmd)
        self.assertIn("https://www.tiktok.com/@dress/video/11",cmd)
        self.assertNotIn("--query",cmd)
        self.assertIn("--flow",cmd)

    def test_newer_unplayable_verification_hides_stale_candidate(self):
        old = workbench.RUNS / "old"
        old.mkdir()
        (old / "candidates.json").write_text(json.dumps({"checked_at":"2026-11-10T00:00:00Z",
            "candidates":[{"id":"11","url":"https://www.tiktok.com/@dress/video/11","views":10}]}))
        latest = workbench.RUNS / "latest"
        latest.mkdir()
        (latest / "candidates.json").write_text(json.dumps({"checked_at":"2026-11-12T00:00:00Z",
            "errors":["https://www.tiktok.com/@dress/video/11: 未核实到可播放的视频轨"]}))
        self.assertEqual(self.client.get("/api/videos").json()["verified_total"],0)

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
