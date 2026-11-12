"""Use the user's existing Glance Chrome session for local Flow2API requests."""
import json
import re
import time

from mcp_stdio import Glance

# Uses the site's own loaded Enterprise client. No hooks or browser profile copy.
TOKEN_SCRIPT = r"""(async () => {
  if (location.hostname !== 'flow.google.com') throw Error('Open Google Flow first');
  const enterprise = window.grecaptcha?.enterprise;
  const script = [...document.scripts].find(s => s.src.includes('/recaptcha/enterprise.js'));
  const siteKey = script ? new URL(script.src).searchParams.get('render') : null;
  if (!enterprise || !siteKey || siteKey === 'explicit') throw Error('Flow verification client unavailable');
  const action = __ACTION__;
  return await Promise.race([
    new Promise((resolve,reject) => enterprise.ready(() => {
      enterprise.execute(siteKey, {action}).then(resolve,reject);
    })),
    new Promise((_,reject) => setTimeout(() => reject(Error('Flow verification timed out')), 25000))
  ]);
})()"""


def token_result(result):
    try:
        token = json.loads(result)
    except ValueError:
        token = result.strip()
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{100,}", token):
        raise ValueError("Flow verification did not return a token")
    return token


def run(ready_file):
    from websockets.sync.client import connect
    from local_flow2api import BASE, RUNTIME, configure, live_flow_tab

    settings = json.loads((RUNTIME / "chrome.json").read_text())
    profile = settings["profile"]
    key = configure()["api_key"]
    with connect(BASE.replace("http://", "ws://") + "/captcha_ws",
                 additional_headers={"Authorization": f"Bearer {key}"},
                 open_timeout=10, ping_interval=15) as socket, Glance() as client:
        socket.send(json.dumps({"type": "register", "client_label": "Project Glance Chrome", "route_key": ""}))
        ack = json.loads(socket.recv(timeout=10))
        if ack.get("type") != "register_ack":
            raise ValueError("Flow2API 本机 Chrome 桥接注册失败")
        ready_file.write_text("ready")
        while True:
            try:
                raw = socket.recv(timeout=20)
            except TimeoutError:
                continue
            message = json.loads(raw)
            if message.get("type") != "get_token":
                continue
            response = {"req_id": message["req_id"], "status": "error"}
            action = message.get("action")
            if action not in {"VIDEO_GENERATION", "IMAGE_GENERATION", "uploadUserImage"}:
                response["error"] = "Unsupported Flow action"
                socket.send(json.dumps(response))
                continue
            try:
                tab = live_flow_tab(profile)
                expression = TOKEN_SCRIPT.replace("__ACTION__", json.dumps(action))
                result = client.tool("chrome", {"action": "evaluate", "bname": profile, "tab_id": tab,
                    "expression": expression, "await_promise": True, "timeout_ms": 30000}, timeout=35)
                response.update(status="success", token=token_result(result))
            except (OSError, ValueError, TimeoutError):
                response["error"] = "Current Chrome Flow verification unavailable"
            socket.send(json.dumps(response))


if __name__ == "__main__":
    import sys
    from pathlib import Path
    run(Path(sys.argv[1]))
