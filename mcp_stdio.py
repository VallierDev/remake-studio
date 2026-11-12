"""Small stdio MCP client for the optional local Glance integration."""
import json
import selectors
import subprocess
import time


class Glance:
    def __enter__(self):
        self.proc = subprocess.Popen(["glance-mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.proc.stdout, selectors.EVENT_READ)
        self.buffer = b""
        self.sequence = 0
        try:
            self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "tiktok-remake", "version": "0.2"}}, 10)
            self.send({"method": "notifications/initialized"})
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_):
        self.selector.close()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc.stdin.close()
        self.proc.stdout.close()

    def send(self, message):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}).encode() + b"\n")
        self.proc.stdin.flush()

    def request(self, method, params, timeout):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                response = json.loads(line)
                if response.get("id") == request_id:
                    if "error" in response:
                        raise ValueError(response["error"].get("message", "Glance request failed"))
                    return response["result"]
            if not self.selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = self.proc.stdout.read1(65536)
            if not chunk:
                break
            self.buffer += chunk
        raise TimeoutError(f"Glance {method} did not respond in {timeout}s")

    def tool(self, name, arguments, timeout=30):
        result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout)
        messages = [part["text"] for part in result.get("content", []) if part.get("type") == "text"]
        if result.get("isError"):
            raise ValueError(messages[0] if messages else f"Glance {name} failed")
        return "\n".join(messages)
