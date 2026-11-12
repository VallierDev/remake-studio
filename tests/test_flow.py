"""Regression coverage for stopping on price and recovering uncertain submits."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
import unittest
from unittest.mock import patch

import flow


class Browser:
    def __init__(self, cost=24, fail_at_submit=False):
        self.cost, self.fail_at_submit = cost, fail_at_submit
        self.path = "/"
        self.submit_count = 0
        self.generated = False

    def open(self, url):
        self.path = urlsplit(url).path

    def evaluate(self, expression):
        if expression == flow.PAGE_STATE:
            return {"host": "flow.google.com", "path": self.path, "home": self.path == "/",
                    "composer": True, "failure": None, "title": "Holiday dress" if self.generated else None,
                    "download": True}
        if "Generating will use" in expression:
            return {"settings": "Video 720p 8s crop_9_16 x1", "cost": str(self.cost), "model": "test"}
        return True

    def fill(self, _):
        pass

    def click(self, selector):
        if selector == flow.NEW_PROJECT:
            self.path = "/project/a1"
        if selector == flow.START:
            self.submit_count += 1
            if self.fail_at_submit:
                raise TimeoutError("browser disconnected")
            self.generated = True

    def export(self, path, _):
        path.write_bytes(b"fixture")


def save(path, value):
    path.write_text(json.dumps(value))


class FlowTests(unittest.TestCase):
    def run_flow(self, directory, page, cap=24):
        @contextmanager
        def browser(*_):
            yield page
        with patch.object(flow, "browser", browser):
            return flow.generate(directory, "fixture", "profile", cap, save)

    def test_price_cap_stops_before_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            page = Browser(cost=25)
            with self.assertRaisesRegex(ValueError, "尚未提交"):
                self.run_flow(directory, page)
            self.assertEqual(page.submit_count, 0)
            self.assertEqual(json.loads((directory / "flow-job.json").read_text())["status"], "draft")

    def test_recovery_never_submits_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "flow-prompt.txt").write_text("Sample prompt")
            page = Browser(fail_at_submit=True)
            with self.assertRaisesRegex(TimeoutError, "disconnected"):
                self.run_flow(directory, page)
            self.assertEqual(json.loads((directory / "flow-job.json").read_text())["status"], "submitting")
            page.generated = True
            result, job = self.run_flow(directory, page)
            self.assertEqual(page.submit_count, 1)
            self.assertEqual(result.read_bytes(), b"fixture")
            self.assertEqual(job["status"], "downloaded")


if __name__ == "__main__":
    unittest.main()
