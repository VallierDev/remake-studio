"""Flow web automation with a durable, single-submit job record."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import shutil
import time

from mcp_stdio import Glance

HOME = "https://flow.google.com/"
PROJECT = re.compile(r"/project/[0-9a-f-]+")
EDITOR = '[contenteditable=true][role=textbox]'
SETTINGS = '[aria-label="Settings trigger"]'
START = 'button[aria-label="Start generation"]'
NEW_PROJECT = "button.new-project-button"

PAGE_STATE = r"""(() => {
  const body = document.querySelector('main') || document.body;
  const tile = [...document.querySelectorAll('flow-grid-tile-container')]
    .find(e => e.querySelector('img[alt*="video thumbnail" i]'));
  const progress = body.innerText.match(/^\d{1,3}%$/m);
  return {path: location.pathname, host: location.hostname,
    home: Boolean(document.querySelector('button.new-project-button')),
    composer: Boolean(document.querySelector('[contenteditable=true][role=textbox]')),
    progress: progress?.[0] || null, title: tile?.getAttribute('aria-label') || null,
    failure: body.innerText.split('\n').find(t =>
      /^(Failed to generate|Generation failed|You are out of credits|Something went wrong|Flow is not available|Verify that you are)/i.test(t)) || null,
    download: Boolean(document.querySelector('button[aria-label="Download media"]'))};
})()"""
ORIGINAL = """(() => {
  const item = [...document.querySelectorAll('button[role=menuitem]')]
    .find(e => e.getClientRects().length && /Original size/i.test(e.innerText));
  if (!item) return false;
  item.click();
  return true;
})()"""


def wait_for(check, timeout, description, interval=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(f"等待{description}超时。可用 --resume 继续当前任务。")


def normalized(value):
    return "".join(char for char in value.casefold() if char.isalnum())


class BridgeBrowser:
    def __init__(self, client, profile):
        self.client, self.profile = client, profile
        self.tab = None

    def call(self, action, **kwargs):
        if self.tab is not None:
            kwargs.setdefault("tab_id", self.tab)
        message = self.client.tool("chrome", {"action": action, "bname": self.profile, **kwargs}, 20)
        if message.startswith("[chrome]") and (" failed:" in message or " needs " in message):
            raise ValueError(f"Chrome Bridge {action} 失败")
        return message

    def open(self, url):
        self.call("ping")
        result = self.call("new_tab", url=url, silent=False)
        self.tab = json.loads(result[result.index("{"):])["id"]

    def evaluate(self, expression):
        return json.loads(self.call("evaluate", expression=expression, timeout_ms=12000))

    def click(self, selector):
        if not self.evaluate(f"(() => {{const e=document.querySelector({json.dumps(selector)});"
                             "if(!e || e.disabled)return false;e.click();return true;})()"):
            raise ValueError(f"Flow 控件不可用：{selector}")

    def fill(self, value):
        if not self.evaluate(f"(() => {{const e=document.querySelector({json.dumps(EDITOR)});"
                             "if(!e)return false;e.focus();return document.activeElement===e;})()"):
            raise ValueError("Flow 提示词输入框未就绪")
        self.call("type_multiline", value=value)

    def export(self, destination, title):
        def downloads():
            result = json.loads(self.call("list_downloads", limit=15))
            return result if isinstance(result, list) else result.get("downloads", [])
        baseline = {item["id"] for item in downloads()}
        if not self.evaluate(ORIGINAL):
            raise ValueError("Flow 没有显示原尺寸下载选项")
        prefix = normalized(title)[:24]
        def ready():
            for item in downloads():
                path = Path(item.get("path") or "")
                if (item["id"] not in baseline and item.get("state") == "complete"
                        and path.suffix.lower() == ".mp4" and path.is_file()
                        and normalized(path.stem).startswith(prefix)):
                    return path
            return None
        shutil.copy2(wait_for(ready, 150, "下载 Flow 成片", interval=3), destination)

    def close(self):
        if self.tab is not None:
            self.call("finalize_tabs", keep=[self.tab])


class PlaywrightBrowser:
    def __init__(self, page):
        self.page = page

    def open(self, url):
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

    def evaluate(self, expression):
        return self.page.evaluate(expression)

    def click(self, selector):
        self.page.locator(selector).click(timeout=12000)

    def fill(self, value):
        self.page.locator(EDITOR).fill(value, timeout=12000)

    def export(self, destination, _title):
        with self.page.expect_download(timeout=150000) as download:
            if not self.evaluate(ORIGINAL):
                raise ValueError("Flow 没有显示原尺寸下载选项")
        download.value.save_as(destination)


@contextmanager
def browser(backend, profile):
    if backend == "glance":
        with Glance() as client:
            tab = BridgeBrowser(client, profile)
            try:
                yield tab
            finally:
                try:
                    tab.close()
                except (OSError, ValueError):
                    pass
    else:
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError as error:
            raise ValueError("请先运行 ./setup.sh 安装 Playwright") from error
        with sync_playwright() as pw:
            try:
                context = pw.chromium.launch_persistent_context(str(profile), channel="chrome", headless=False,
                                                                locale="en-US", accept_downloads=True)
                try:
                    yield PlaywrightBrowser(context.pages[0] if context.pages else context.new_page())
                finally:
                    context.close()
            except Error as error:
                raise ValueError(f"Flow 浏览器运行失败：{str(error).splitlines()[0]}") from error


def login(profile):
    with browser("playwright", profile) as page:
        page.open(HOME)
        print("请在新打开的 Chrome 窗口登录有 Flow 权限的 Google 账号。", flush=True)
        wait_for(lambda: page.evaluate(PAGE_STATE)["home"], 600, "登录 Flow")
        print("Flow 登录已保存。", flush=True)


def select_video_options(page, cap):
    page.click(SETTINGS)
    ready = page.evaluate("""(() => {
      const trigger = document.querySelector('[aria-label="Settings trigger"]');
      if (trigger?.innerText.includes('Video')) return true;
      const video = [...document.querySelectorAll('button')].find(e => e.getClientRects().length &&
        e.innerText.trim().split(String.fromCharCode(10)).at(-1) === 'Video');
      if (!video) return false;
      video.click();
      return true;
    })()""")
    if not ready:
        raise ValueError("Flow 没有显示视频生成模式")

    def select(label):
        choice = json.dumps(label)
        ok = page.evaluate(fr"""(() => {{
          const button = [...document.querySelectorAll('button[role=radio]')]
            .find(e => e.getClientRects().length && e.innerText.trim().split('\n').at(-1) === {choice});
          if (!button) return false;
          if (button.getAttribute('aria-checked') !== 'true') button.click();
          return true;
        }})()""")
        if not ok:
            raise ValueError(f"Flow 没有显示 {label} 选项")
    select("9:16")
    select("x1")
    evidence = page.evaluate(r"""(() => ({
      settings: document.querySelector('[aria-label="Settings trigger"]')?.innerText || '',
      cost: document.body.innerText.match(/Generating will use\s+(\d+)\s+credits/i)?.[1] || null,
      model: document.body.innerText.match(/Omni[^\n]+Flash|Veo[^\n]+/i)?.[0] || null
    }))()""")
    settings = evidence["settings"]
    if not all(value in settings for value in ("Video", "720p", "8s", "crop_9_16", "x1")):
        raise ValueError(f"Flow 输出设置不符：{settings.replace(chr(10), ' ')}")
    cost = int(evidence["cost"]) if evidence["cost"] is not None else None
    if cost is None or cost > cap:
        raise ValueError(f"Flow 价格为 {cost if cost is not None else '未知'}，本次上限 {cap}，生成尚未提交")
    page.click(SETTINGS)
    return cost, evidence["model"]


def generate(run, backend, profile, cap, save):
    checkpoint = run / "flow-job.json"
    job = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    destination = run / "flow-output.mp4"
    if destination.exists():
        return destination, job
    with browser(backend, profile) as page:
        if not job.get("project"):
            page.open(HOME)
            wait_for(lambda: page.evaluate(PAGE_STATE)["home"], 50, "Flow 项目首页")
            page.click(NEW_PROJECT)
            def created():
                state = page.evaluate(PAGE_STATE)
                return state["path"] if state["host"] == "flow.google.com" and PROJECT.fullmatch(state["path"]) else None
            job.update(project=wait_for(created, 50, "新建 Flow 项目"), status="draft")
            save(checkpoint, job)
        else:
            if not PROJECT.fullmatch(job["project"]):
                raise ValueError("无效的 Flow 项目记录")
            page.open(HOME.rstrip("/") + job["project"])

        if job["status"] == "draft":
            wait_for(lambda: page.evaluate(PAGE_STATE)["composer"], 45, "Flow 编辑器")
            cost, model = select_video_options(page, cap)
            prompt = " ".join((run / "flow-prompt.txt").read_text().split())
            page.fill(prompt)
            expected = json.dumps(prompt)
            check = page.evaluate(fr"""(() => {{
              const field = document.querySelector({json.dumps(EDITOR)});
              const button = document.querySelector({json.dumps(START)});
              return !!button && !button.disabled && field?.innerText.trim().replace(/\s+/g, ' ') === {expected};
            }})()""")
            if not check:
                raise ValueError("Flow 提示词或提交按钮未通过验证")
            print(f"Flow 即将提交 1 条视频（{cost} 积分）", flush=True)
            job.update(status="submitting", credits=cost, model=model)
            save(checkpoint, job)
            page.click(START)

        def asset():
            state = page.evaluate(PAGE_STATE)
            if state["failure"]:
                raise ValueError(f"Flow 返回：{state['failure']}")
            if (state.get("progress") or state["title"]) and job["status"] == "submitting":
                job["status"] = "submitted"
                save(checkpoint, job)
            return state["title"]
        title = wait_for(asset, 420, "Flow 生成结果", interval=5)
        if not normalized(title):
            raise ValueError("Flow 生成结果缺少可识别的资产名")
        job.update(status="generated", asset=title)
        save(checkpoint, job)
        page.click('flow-grid-tile-container img[alt*="video thumbnail" i]')
        wait_for(lambda: page.evaluate(PAGE_STATE)["download"], 45, "成片下载入口")
        page.click('button[aria-label="Download media"]')
        wait_for(lambda: page.evaluate("""[...document.querySelectorAll('button[role=menuitem]')]
            .some(e => e.getClientRects().length && e.innerText.includes('Original size'))"""), 12, "原尺寸下载菜单")
        temp = run / "flow-output.pending.mp4"
        page.export(temp, title)
        temp.replace(destination)
        job["status"] = "downloaded"
        save(checkpoint, job)
    return destination, job
