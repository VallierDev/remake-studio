"""Project-owned Flow2API process, configuration and account import."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
import tomllib
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "vendor" / "flow2api"
RUNTIME = ROOT / ".flow2api"
BASE = "http://127.0.0.1:18781"


def private_json(path, value):
    temp = path.with_suffix(".pending")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        os.fchmod(out.fileno(), 0o600)
        json.dump(value, out)
        out.flush()
        os.fsync(out.fileno())
    temp.replace(path)


@contextmanager
def service_lock():
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with (RUNTIME / "service.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def configure():
    if not (SOURCE / "main.py").is_file():
        raise ValueError("请先运行 ./setup.sh --flow2api 安装项目内 Flow2API")
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    config = SOURCE / "config" / "setting.toml"
    credentials = RUNTIME / "credentials.json"
    if not credentials.exists():
        if config.exists():
            raise ValueError("已有 Flow2API 配置，需先核对其凭据归属")
        private_json(credentials, {"api_key": secrets.token_urlsafe(32),
                     "admin_username": "admin", "admin_password": secrets.token_urlsafe(24)})
    creds = json.loads(credentials.read_text())
    if not config.exists():
        text = (SOURCE / "config" / "setting_example.toml").read_text()
        text = text.replace('api_key = "han1234"', f'api_key = "{creds["api_key"]}"', 1)
        text = text.replace('admin_password = "admin"', f'admin_password = "{creds["admin_password"]}"', 1)
        text = text.replace('host = "0.0.0.0"', 'host = "127.0.0.1"', 1)
        text = text.replace('port = 8000', 'port = 18781', 1)
        text = text.replace('captcha_method = "yescaptcha"', 'captcha_method = "extension"', 1)
        text = text.replace('log_requests = true', 'log_requests = false')
        text = text.replace('log_responses = true', 'log_responses = false')
        # Store settings with the runtime data, while preserving upstream's lookup.
        settings = RUNTIME / "setting.toml"
        fd = os.open(settings, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(text)
        config.symlink_to(settings)
    current = tomllib.loads(config.read_text())
    if current["global"]["api_key"] != creds["api_key"]:
        raise ValueError("本地 Flow2API 配置与项目密钥不匹配")
    for name in ("data", "tmp", "logs"):
        target = RUNTIME / name
        target.mkdir(mode=0o700, exist_ok=True)
        link = SOURCE / name
        if not link.exists():
            link.symlink_to(target, target_is_directory=True)
    return creds


def api(path, payload=None, admin=False, method=None):
    creds = configure()
    if admin:
        token = api("/api/admin/login", {"username": creds["admin_username"],
                                        "password": creds["admin_password"]})["token"]
    else:
        token = creds["api_key"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urlopen(Request(BASE + path, data, headers, method=method), timeout=60 if payload else 10) as response:
            return json.load(response)
    except HTTPError as error:
        raise ValueError(f"项目内 Flow2API 返回 HTTP {error.code}") from None


def credentials():
    ensure()
    return BASE, {"Authorization": f"Bearer {configure()['api_key']}"}


def running_pid():
    path = RUNTIME / "process.json"
    if not path.is_file():
        return None
    record = json.loads(path.read_text())
    pid = record.get("pid", 0)
    if not isinstance(pid, int) or pid < 2:
        return None
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "args="],
                            capture_output=True, text=True, check=False)
    # Compare the process birth stamp as well as argv to guard against PID reuse.
    return pid if result.returncode == 0 and result.stdout.strip() == record.get("identity") else None


def ensure():
    with service_lock():
        configure()
        try:
            api("/v1/models")
            return
        except (OSError, ValueError):
            pass
        if running_pid():
            raise ValueError("项目内 Flow2API 进程已启动但尚未就绪，检查 .flow2api/service.log")
        executable = ROOT / ".flow2api-venv" / "bin" / "python"
        if not executable.is_file():
            raise ValueError("请先运行 ./setup.sh --flow2api")
        env = {**os.environ, "PYTHONUNBUFFERED": "1",
               "PERSONAL_BROWSER_USER_DATA_DIR": str(RUNTIME / "login-browser")}
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if chrome.is_file():
            env["BROWSER_EXECUTABLE_PATH"] = str(chrome)
        log = RUNTIME / "service.log"
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as out:
            proc = subprocess.Popen([str(executable), str(SOURCE / "main.py")], cwd=SOURCE, env=env,
                                    stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        identity = subprocess.run(["ps", "-p", str(proc.pid), "-o", "lstart=", "-o", "args="],
                                  capture_output=True, text=True, check=True).stdout.strip()
        private_json(RUNTIME / "process.json", {"pid": proc.pid, "identity": identity})
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise ValueError("项目内 Flow2API 启动失败，检查 .flow2api/service.log")
            try:
                api("/v1/models")
                return
            except (OSError, ValueError):
                time.sleep(1)
        raise TimeoutError("项目内 Flow2API 启动超时，检查 .flow2api/service.log")


def stop():
    with service_lock():
        pid = running_pid()
        if pid:
            os.killpg(pid, signal.SIGTERM)
            for _ in range(30):
                if not running_pid():
                    break
                time.sleep(0.2)
            else:
                raise TimeoutError("Flow2API 仍在退出，请检查进程")
        print("项目内 Flow2API 已停止")


def status():
    ensure()
    models = api("/v1/models")["data"]
    tokens = api("/api/tokens", admin=True)
    items = tokens if isinstance(tokens, list) else tokens.get("tokens", tokens.get("data", []))
    print(f"项目内 Flow2API 已就绪 · {BASE} · {len(models)} 个模型 · {len(items)} 个账号")


def login():
    ensure()
    stop()
    from playwright.sync_api import sync_playwright
    profile = RUNTIME / "login-browser"
    print("请在 Chrome 中登录 Google Flow。账号会话将保存到本项目的本地 Flow2API。", flush=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(str(profile), channel="chrome", headless=False)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://flow.google.com/", wait_until="domcontentloaded")
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                if page.locator("button.new-project-button").count():
                    break
                page.wait_for_timeout(1000)
            else:
                raise TimeoutError("等待 Google Flow 登录超时，可重新运行 --flow2api-login")
            cookies = context.cookies(["https://flow.google.com", "https://accounts.google.com", "https://google.com"])
            cookie_data = json.dumps(cookies)
        finally:
            context.close()
    ensure()
    result = api("/api/tokens", {"google_cookies": cookie_data, "remark": "Project local Flow",
                               "video_concurrency": 1, "image_enabled": False}, admin=True)
    if not result.get("success"):
        raise ValueError("Flow2API 账号导入未成功")
    print("Google Flow 账号已导入本项目")


def import_chrome(tab_id, profile):
    """Import only the Google cookies belonging to a selected live Flow tab."""
    ensure()
    from mcp_stdio import Glance
    with Glance() as client:
        state = client.tool("chrome", {"action": "evaluate", "bname": profile, "tab_id": tab_id,
            "expression": "({host:location.hostname,signedIn:!!document.querySelector('button.new-project-button,button[aria-label=Home]')})"})
        state = json.loads(state)
        if state.get("host") != "flow.google.com" or not state.get("signedIn"):
            raise ValueError("请选择已登录 Google Flow 的标签页")
        result = client.tool("chrome", {"action": "cdp", "bname": profile, "tab_id": tab_id,
             "method": "Network.getCookies", "params": {"urls": [
                 "https://flow.google.com/", "https://accounts.google.com/", "https://www.google.com/"]}})
        cookies = json.loads(result).get("cookies", [])
    if not cookies:
        raise ValueError("这个 Flow 浏览器会话没有可导入的 Cookie")
    response = api("/api/tokens", {"google_cookies": json.dumps(cookies),
                   "remark": "Project local Chrome Flow", "video_concurrency": 1,
                   "image_enabled": False}, admin=True)
    if not response.get("success"):
        raise ValueError("Flow2API 导入账号失败")
    print("已将选定 Chrome 的 Flow 会话导入本项目")


def live_flow_tab(profile="default"):
    import re
    from mcp_stdio import Glance
    with Glance() as client:
        listing = client.tool("chrome", {"action": "list_tabs", "bname": profile})
        candidates = []
        lines = listing.splitlines()
        for index, line in enumerate(lines):
            if f"[{profile} (" not in line or index + 1 >= len(lines):
                continue
            if not lines[index + 1].strip().startswith("https://flow.google.com/"):
                continue
            match = re.search(r"`(\d+)`", line)
            if match:
                candidates.append(int(match[1]))
        for tab in candidates:
            state = json.loads(client.tool("chrome", {"action": "evaluate", "bname": profile,
                "tab_id": tab, "expression": """({
                    host: location.hostname,
                    signedIn: !!document.querySelector('button.new-project-button,button[aria-label="Home"],button[aria-label="Download media"]')
                })"""}))
            if state.get("host") == "flow.google.com" and state.get("signedIn"):
                return tab
    raise ValueError("请在当前 Chrome 打开已登录的 flow.google.com，再重试")


def use_current_chrome(profile="default"):
    ensure()
    tab = live_flow_tab(profile)
    # The gateway already owns this account. Refresh the first locally imported
    # account from the selected live session instead of creating a duplicate.
    from mcp_stdio import Glance
    with Glance() as client:
        data = json.loads(client.tool("chrome", {"action": "cdp", "bname": profile, "tab_id": tab,
             "method": "Network.getCookies", "params": {"urls": [
                 "https://flow.google.com/", "https://accounts.google.com/", "https://www.google.com/"]}}))
    cookies = data.get("cookies", [])
    if not cookies:
        raise ValueError("当前 Flow 会话没有可导入的 Cookie")
    # A session cookie must omit expiry when injected into another browser.
    for cookie in cookies:
        if cookie.get("expires", 1) <= 0:
            cookie.pop("expires", None)
    accounts = api("/api/tokens", admin=True)
    if not isinstance(accounts, list):
        accounts = accounts.get("tokens", accounts.get("data", []))
    owned = next((item for item in accounts if item.get("remark", "").startswith("Project local")), None)
    payload = {"google_cookies": json.dumps(cookies), "video_concurrency": 1,
               "image_enabled": False, "video_enabled": True, "remark": "Project local Chrome Flow"}
    if owned:
        response = api(f"/api/tokens/{owned['id']}", payload, admin=True, method="PUT")
    else:
        response = api("/api/tokens", payload, admin=True)
    if not response.get("success"):
        raise ValueError("当前 Chrome 账号绑定失败")
    response = api("/api/captcha/config", {"captcha_method": "extension"}, admin=True)
    if not response.get("success"):
        raise ValueError("Flow2API 无法切换到当前浏览器桥接")
    api("/api/admin/debug", {"enabled": False}, admin=True)
    private_json(RUNTIME / "chrome.json", {"profile": profile, "tab": tab})
    return tab
