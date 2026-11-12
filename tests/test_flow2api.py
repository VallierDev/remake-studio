from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from flow2api import DEFAULT_MODEL, generate, stream_video


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            body = json.dumps({"data": [{"id": DEFAULT_MODEL}]}).encode()
        elif self.path == "/tmp/result.mp4":
            self.server.media_auth = self.headers.get("Authorization")
            if self.server.fail_download:
                self.send_error(503)
                return
            body = b"mp4-fixture"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.server.submissions.append((self.path, self.headers.get("Authorization"),
                                       json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for fragment in ("<video ", 'src="/tmp/result.mp4" controls></video>'):
            body = json.dumps({"choices": [{"delta": {"content": fragment}}]})
            self.wfile.write(f"data: {body}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


class StreamTests(unittest.TestCase):
    def test_stream_is_drained_after_receiving_video(self):
        event = json.dumps({"choices": [{"delta": {"content": '<video src="/tmp/result.mp4"></video>'}}]})
        body = f"data: {event}\n\ndata: [DONE]\n\n".encode()
        response = io.BytesIO(body)
        self.assertEqual(stream_video(response), "/tmp/result.mp4")
        self.assertGreaterEqual(response.tell(), len(body) - 1)

    def test_trailing_error_is_not_ignored(self):
        event = json.dumps({"choices": [{"delta": {"content": '<video src="/tmp/result.mp4"></video>'}}]})
        response = io.BytesIO(f'data: {event}\n\ndata: {{"error":"completion failed"}}\n\n'.encode())
        with self.assertRaises(ValueError):
            stream_video(response)


class GatewayTests(unittest.TestCase):
    def test_streaming_download_can_resume_without_posting_again(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), Gateway) as server, TemporaryDirectory() as path:
            server.submissions = []
            server.fail_download = True
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                run = Path(path)
                (run / "flow-prompt.txt").write_text("Original video")
                with patch.dict(os.environ, FLOW2API_API_KEY="fixture-key"):
                    url = f"http://127.0.0.1:{server.server_port}"
                    with self.assertRaisesRegex(ValueError, "下载中断"):
                        generate(run, url, DEFAULT_MODEL)
                    server.fail_download = False
                    output, state = generate(run, url, DEFAULT_MODEL)
                self.assertEqual(output.read_bytes(), b"mp4-fixture")
                self.assertEqual(state["status"], "downloaded")
                self.assertNotIn("video_url", state)
                self.assertIsNone(server.media_auth)
                self.assertEqual(len(server.submissions), 1)
                endpoint, auth, payload = server.submissions[0]
                self.assertEqual(endpoint, "/v1/chat/completions")
                self.assertEqual(auth, "Bearer fixture-key")
                self.assertEqual(payload["model"], DEFAULT_MODEL)
                self.assertTrue(payload["stream"])
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
