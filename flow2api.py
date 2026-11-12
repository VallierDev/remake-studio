"""Text-to-video through an independently configured Flow2API gateway."""
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

DEFAULT_MODEL = "veo_3_1_t2v_fast_portrait"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class VideoTag(HTMLParser):
    def __init__(self):
        super().__init__()
        self.url = None

    def handle_starttag(self, tag, attrs):
        if tag == "video":
            self.url = dict(attrs).get("src") or self.url


def gateway_config(base):
    base = (base or os.getenv("FLOW2API_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = urlsplit(base)
    if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
            or url.query or url.fragment):
        raise ValueError("Flow2API 地址必须是服务根 URL，密钥单独设置")
    if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("远程 Flow2API 请使用 HTTPS 或本地 SSH 隧道")
    key = os.getenv("FLOW2API_API_KEY", "").strip()
    if not key:
        raise ValueError("请先在本机环境设置 FLOW2API_API_KEY")
    return base, {"Authorization": f"Bearer {key}"}


def gateway_request(url, headers, data=None, timeout=20):
    request = Request(url, data, headers=headers)
    try:
        return build_opener(NoRedirect()).open(request, timeout=timeout)
    except HTTPError as error:
        raise ValueError(f"Flow2API 返回 HTTP {error.code}") from None
    except OSError as error:
        raise ValueError("无法连接 Flow2API 网关，检查地址和服务状态") from error


def check_model(base, model):
    base, headers = gateway_config(base)
    with gateway_request(f"{base}/v1/models", headers) as response:
        models = json.load(response)["data"]
    if not any(item.get("id") == model for item in models):
        raise ValueError(f"Flow2API 未提供模型 {model}")
    return base, headers


def stream_video(response):
    content = VideoTag()
    reasoning = VideoTag()
    for line in response:
        if not line.startswith(b"data:"):
            continue
        body = line[5:].strip()
        if body == b"[DONE]":
            break
        event = json.loads(body)
        if event.get("error"):
            raise ValueError("Flow2API 流式生成返回错误，请检查网关任务日志")
        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            for parser, key in ((content, "content"), (reasoning, "reasoning_content")):
                text = delta.get(key)
                if isinstance(text, str):
                    parser.feed(text)
        if content.url or reasoning.url:
            return content.url or reasoning.url
    raise ValueError("Flow2API 未返回视频地址，请检查网关任务状态")


def save_private(path, value):
    temp = path.with_suffix(".pending")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


def generate(run, base, model):
    destination = run / "flow-output.mp4"
    checkpoint = run / "flow2api-job.json"
    job = json.loads(checkpoint.read_text()) if checkpoint.is_file() else {}
    if destination.is_file():
        return destination, job
    if job.get("video_url"):
        base = job["gateway"]
    elif job.get("status") == "submitting":
        raise ValueError("上次请求结果未知。先在 Flow2API 网关查看任务，避免重复生成。")
    else:
        base, headers = check_model(base, model)
        prompt = (run / "flow-prompt.txt").read_text().strip()
        payload = json.dumps({"model": model, "stream": True,
                              "messages": [{"role": "user", "content": prompt}]}).encode()
        job = {"status": "submitting", "gateway": base, "model": model, "credits": None}
        save_private(checkpoint, job)
        print(f"Flow2API 提交一条视频（{model}；网关未提供生成前积分报价）", flush=True)
        headers.update({"Content-Type": "application/json", "Accept": "text/event-stream"})
        try:
            with gateway_request(f"{base}/v1/chat/completions", headers, payload, timeout=480) as response:
                job["video_url"] = stream_video(response)
        except (OSError, ValueError) as error:
            raise ValueError("Flow2API 未返回成片，已保留提交记录。检查网关任务日志后再重试。") from error
        job["status"] = "generated"
        save_private(checkpoint, job)
    url = urljoin(base + "/", job["video_url"])
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Flow2API 返回了无效视频地址")
    pending = run / "flow-output.pending.mp4"
    try:
        with urlopen(url, timeout=120) as response, pending.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except OSError:
        raise ValueError("成片下载中断，保留下载地址，可用 --resume 重试") from None
    pending.replace(destination)
    job.pop("video_url", None)
    job["status"] = "downloaded"
    save_private(checkpoint, job)
    return destination, job
