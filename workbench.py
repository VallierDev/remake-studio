"""Local task workbench for the TikTok Remake pipeline."""
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.request import urlopen
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
STATE = ROOT / ".workbench"
SLUG = re.compile(r"[A-Za-z0-9_-]+")
MODELS = {"omni-1.2-flash-8s-portrait", "veo-3.1-fast-8s-portrait"}
FINDER_MODE = os.environ.get("REMAKE_EDITION") == "finder"
stop_worker = threading.Event()
TOPICS = {
    "christmas": {"label": "圣诞女装", "queries": ['"christmas party outfit" red dress', '"holiday outfit" sequin dress']},
    "party": {"label": "派对穿搭", "queries": ['"party outfit" dress', '"evening dress" outfit TikTok']},
    "fashion": {"label": "女装热门", "queries": ['"women outfit" dress TikTok', '"fashion haul" dress TikTok']},
}


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {} if default is None else default


@contextmanager
def database():
    connection = sqlite3.connect(STATE / "jobs.sqlite", timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize():
    STATE.mkdir(mode=0o700, exist_ok=True)
    RUNS.mkdir(exist_ok=True)
    with database() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
          run TEXT PRIMARY KEY, created TEXT NOT NULL, updated TEXT NOT NULL,
          status TEXT NOT NULL, options TEXT NOT NULL, resume INTEGER DEFAULT 0,
          pid INTEGER, identity TEXT, exit_code INTEGER
        )""")
        db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO settings(id,data) VALUES(1,?)", (json.dumps({
            "enabled": True, "topic": "christmas", "interval_hours": 6,
            "next_run": time.time(), "last_job": None, "last_success": None,
        }),))


class TaskInput(BaseModel):
    keywords: str = Field(default="", max_length=500)
    product: str = Field(default="圣诞派对穿搭", min_length=1, max_length=200)
    caption: str = Field(default="", max_length=60)
    mode: str = Field(pattern="^(scout|generate)$", default="scout")
    min_views: int = Field(default=50000, ge=0, le=10**12)
    min_engagement: float = Field(default=2, ge=0, le=1000, allow_inf_nan=False)
    limit: int = Field(default=12, ge=1, le=20)
    sort: str = Field(pattern="^(views|engagement)$", default="views")
    model: str = "omni-1.2-flash-8s-portrait"
    profile: str = Field(default="default", min_length=1, max_length=80)


def command(run, options, resume=False):
    cmd = [str(ROOT / ".venv/bin/python"), "-u", str(ROOT / "remake.py"), "--run", run]
    if resume:
        cmd.extend(["--resume", "--flow"])
    else:
        if options.get("source_url"):
            cmd.extend(["--url", options["source_url"]])
        else:
            for query in options.get("queries") or [options["keywords"]]:
                cmd.extend(["--query", query])
        if options.get("seed_file"):
            cmd.extend(["--seed-file", options["seed_file"]])
        cmd.extend(["--product", options["product"],
                    "--min-views", str(options["min_views"]), "--min-engagement",
                    str(options["min_engagement"]), "--limit", str(options["limit"]),
                    "--sort", options["sort"]])
        cmd.append("--scout-only" if options["mode"] == "scout" else "--flow")
    if options["mode"] == "generate" or resume:
        cmd.extend(["--flow-backend", "flow2api", "--flow2api-model", options["model"],
                    "--chrome-profile", options["profile"]])
        if options["caption"].strip():
            cmd.extend(["--caption", options["caption"].strip()])
    return cmd


def process_identity(pid):
    if not isinstance(pid, int) or pid < 2:
        return ""
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "args="],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def log_path(run):
    return STATE / f"{run}.log"


def worker(mode):
    while not stop_worker.is_set():
        if mode == "scout":
            schedule_update()
        with database() as db:
            row = db.execute("SELECT * FROM jobs WHERE status='running' AND json_extract(options,'$.mode')=? ORDER BY created LIMIT 1", (mode,)).fetchone()
            if row is None:
                row = db.execute("SELECT * FROM jobs WHERE status='queued' AND json_extract(options,'$.mode')=? ORDER BY CASE WHEN json_extract(options,'$.search_term') IS NOT NULL THEN 0 ELSE 1 END,created LIMIT 1", (mode,)).fetchone()
        if row is None:
            stop_worker.wait(1)
            continue
        options = json.loads(row["options"])
        active = row["status"] == "running" and row["identity"] and process_identity(row["pid"]) == row["identity"]
        proc = None
        if row["status"] == "queued":
            with log_path(row["run"]).open("ab") as log:
                proc = subprocess.Popen(command(row["run"], options, bool(row["resume"])),
                                        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            with database() as db:
                db.execute("UPDATE jobs SET status='running',pid=?,identity=?,updated=? WHERE run=?",
                           (proc.pid, process_identity(proc.pid), now(), row["run"]))
            active = True
        while active and not stop_worker.is_set():
            stop_worker.wait(1)
            active = proc.poll() is None if proc else process_identity(row["pid"]) == row["identity"]
        if stop_worker.is_set():
            return
        code = proc.returncode if proc else None
        run = RUNS / row["run"]
        result = read_json(run / "result.json")
        candidates = read_json(run / "candidates.json")
        done = result.get("generation", {}).get("status") == "verified" and (run / "final.mp4").is_file()
        if options["mode"] == "scout":
            done = bool(candidates.get("eligible_count"))
        status = "completed" if done and code in (None, 0) else "failed" if code is not None else "interrupted"
        with database() as db:
            db.execute("UPDATE jobs SET status=?,exit_code=?,updated=? WHERE run=?",
                       (status, code, now(), row["run"]))
            if options.get("scheduled") and status == "completed":
                config = json.loads(db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])
                config["last_success"] = now()
                db.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps(config),))


def now():
    return datetime.now(timezone.utc).isoformat()


def run_dir(name):
    if not SLUG.fullmatch(name):
        raise HTTPException(404)
    path = RUNS / name
    if path.resolve().parent != RUNS.resolve() or not path.is_dir():
        raise HTTPException(404)
    return path


def stage(path, status):
    if status == "queued":
        return "queued"
    if (path / "final.mp4").is_file():
        return "completed"
    if (path / "flow-output.mp4").is_file():
        return "finishing"
    if (path / "flow2api-job.json").is_file() or (path / "flow-job.json").is_file():
        return "generating"
    if (path / "storyboard.json").is_file() or (path / "rea/breakdown.json").is_file():
        return "planning"
    if list(path.glob("reference-*.mp4")):
        return "decomposing"
    if (path / "candidates.json").is_file():
        return "discovered"
    return "searching"


def summary(path, managed=None):
    result = read_json(path / "result.json")
    candidate = read_json(path / "candidates.json")
    options = json.loads(managed["options"]) if managed else {}
    generation = result.get("generation", {})
    done = (path / "final.mp4").is_file() or generation.get("status") == "verified"
    status = managed["status"] if managed else "completed" if done else (
        "incomplete" if result else "scouted" if candidate.get("eligible_count") else "incomplete")
    if not managed and not (result or candidate):
        return None
    return {
        "run": path.name, "title": options.get("product") or result.get("product") or
            (candidate.get("queries") or [path.name])[0],
        "keywords": options.get("keywords") or " · ".join(candidate.get("queries", [])),
        "status": status, "stage": stage(path, status), "managed": bool(managed),
        "created": managed["created"] if managed else candidate.get("checked_at") or
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "candidate_count": len(candidate.get("candidates", [])), "eligible_count": candidate.get("eligible_count", 0),
        "shots": result.get("shots"), "final": generation.get("final"), "caption": generation.get("caption"),
        "has_video": (path / "final.mp4").is_file(), "has_preview": (path / "preview.jpg").is_file(),
        "has_poster": (path / "poster.jpg").is_file(),
        "has_report": (path / "rea/report.html").is_file(),
        "has_storyboard": (path / "rea/storyboard.jpg").is_file(),
        "can_resume": status in ("failed", "interrupted") and bool(result) and options.get("mode") == "generate",
        "options": options,
    }


@asynccontextmanager
async def lifespan(_app):
    initialize()
    # One supervisor per project, even if two launchers are clicked.
    with (STATE / "app.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("工作台已在运行") from error
        stop_worker.clear()
        threads = [threading.Thread(target=worker, args=(mode,), daemon=True, name=f"remake-{mode}")
                   for mode in (("scout",) if FINDER_MODE else ("scout", "generate"))]
        for thread in threads:
            thread.start()
        yield
        stop_worker.set()
        for thread in threads:
            thread.join(timeout=2)


app = FastAPI(title="Remake Studio", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])


@app.middleware("http")
async def local_origin(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://{request.headers.get('host')}", f"https://{request.headers.get('host')}"}:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "工作台只接受本机页面的操作"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/")
@app.get("/videos")
@app.get("/settings")
def index():
    page = (ROOT / "web/index.html").read_text()
    return HTMLResponse(page.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-edition="' +
                                      ("finder" if FINDER_MODE else "full") + '">'))


@app.get("/api/system")
def system():
    healthy = False
    try:
        if FINDER_MODE:
            raise OSError("Finder edition")
        with urlopen("http://127.0.0.1:18781/health", timeout=1) as response:
            healthy = response.status == 200
    except OSError:
        pass
    return {"edition": "finder" if FINDER_MODE else "full", "flow2api": healthy, "chrome_configured": (ROOT / ".flow2api/chrome.json").is_file(),
            "glance": bool(shutil.which("glance-mcp")), "ffmpeg": bool(shutil.which("ffmpeg")),
            "ytdlp": bool(shutil.which("yt-dlp")), "rea": (ROOT / "vendor/rea-video-sparse/rea_video").is_dir()}


@app.get("/api/runs")
def runs():
    with database() as db:
        jobs = {row["run"]: row for row in db.execute("SELECT * FROM jobs")}
    paths = {path.name: path for path in RUNS.iterdir() if path.is_dir() and not path.is_symlink() and SLUG.fullmatch(path.name)}
    for name in jobs:
        paths.setdefault(name, RUNS / name)
    entries = [summary(path, jobs.get(name)) for name, path in paths.items()]
    return sorted((entry for entry in entries if entry), key=lambda item: item["created"], reverse=True)


def collected_videos():
    collected = {}
    unplayable = {}
    for folder in sorted(RUNS.iterdir()):
        if not folder.is_dir() or folder.is_symlink() or not SLUG.fullmatch(folder.name):
            continue
        data = read_json(folder / "candidates.json")
        measured = data.get("checked_at", "")
        for error in data.get("errors", []):
            if "未核实到可播放的视频轨" in error:
                match = re.search(r"/video/(\d+)", error)
                if match:
                    unplayable[match[1]] = max(measured, unplayable.get(match[1], ""))
        for row in data.get("candidates", []):
            identity = str(row.get("id", ""))
            url = str(row.get("url", ""))
            if not identity.isdigit() or not re.fullmatch(
                    r"https://(?:www\.)?tiktok\.com/@[\w.-]+/video/" + identity, url):
                continue
            previous = collected.get(identity)
            queries = set(previous.get("queries", [])) if previous else set()
            queries.update(data.get("queries", []))
            if previous is None or measured >= previous["checked_at"]:
                collected[identity] = {key: row.get(key) for key in (
                    "id", "url", "author", "description", "views", "likes", "comments", "shares",
                    "saves", "duration", "upload_date", "weighted_engagement_rate")}
                collected[identity].update(source_run=folder.name, checked_at=measured)
            collected[identity]["queries"] = sorted(queries)
    return {identity: row for identity, row in collected.items()
            if unplayable.get(identity, "") < row["checked_at"] or identity not in unplayable}


class UpdateSettings(BaseModel):
    enabled: bool
    topic: str = Field(pattern="^(christmas|party|fashion)$")
    interval_hours: int = Field(ge=1, le=168)


def settings_snapshot():
    with database() as db:
        config = json.loads(db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])
        row = db.execute("SELECT run,status,updated FROM jobs WHERE run=?", (config.get("last_job"),)).fetchone()
    return {**config, "topics": [{"id": key, "label": value["label"]} for key, value in TOPICS.items()],
            "current_job": dict(row) if row else None}


def schedule_update(force=False):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        config = json.loads(db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])
        active = next((row for row in db.execute("SELECT run,options FROM jobs WHERE status IN ('queued','running')")
                       if json.loads(row["options"]).get("scheduled")), None)
        if active:
            return active["run"] if force else None
        if not force and (not config["enabled"] or config["next_run"] > time.time()):
            return None
        name = "auto-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        topic = TOPICS[config["topic"]]
        options = TaskInput(keywords=topic["queries"][0], product="自动更新 · " + topic["label"],
                            mode="scout", min_views=0, min_engagement=0, limit=12).model_dump()
        options.update(queries=topic["queries"], scheduled=True)
        existing = sorted(collected_videos().values(), key=lambda row: row["checked_at"])
        if existing:
            seed = STATE / (name + "-existing.txt")
            seed.write_text("\n".join(row["url"] for row in existing[:100]) + "\n")
            options["seed_file"] = str(seed)
        db.execute("INSERT INTO jobs(run,created,updated,status,options) VALUES(?,?,?,?,?)",
                   (name, now(), now(), "queued", json.dumps(options)))
        config.update(last_job=name, next_run=time.time() + config["interval_hours"] * 3600)
        db.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps(config),))
        return name


@app.get("/api/settings")
def get_settings():
    return settings_snapshot()


@app.put("/api/settings")
def set_settings(value: UpdateSettings):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        config = json.loads(db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])
        changed = value.topic != config["topic"] or value.enabled and not config["enabled"]
        config.update(value.model_dump())
        config["next_run"] = time.time() if changed else time.time() + value.interval_hours * 3600
        db.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps(config),))
        if not value.enabled:
            for row in db.execute("SELECT run,options FROM jobs WHERE status='queued'").fetchall():
                if json.loads(row["options"]).get("scheduled"):
                    db.execute("UPDATE jobs SET status='cancelled',updated=? WHERE run=?", (now(), row["run"]))
    return settings_snapshot()


@app.post("/api/chrome/connect")
def connect_current_chrome():
    if FINDER_MODE:
        raise HTTPException(403, "找片版在 YouArt Desktop 中完成视频制作")
    from local_flow2api import use_current_chrome
    try:
        use_current_chrome()
    except (OSError, ValueError) as error:
        raise HTTPException(400, str(error)) from None
    return {"connected": True}


@app.post("/api/videos/update", status_code=202)
def update_videos():
    return {"run": schedule_update(force=True)}


PRODUCT_TERMS = {
    "圣诞晚礼服": "christmas evening dress", "聖誕晚禮服": "christmas evening dress",
    "圣诞挂件": "christmas ornaments", "聖誕掛件": "christmas ornaments",
    "圣诞树": "christmas tree", "聖誕樹": "christmas tree",
    "圣诞": "christmas", "聖誕": "christmas", "晚礼服": "evening dress",
    "女装": "women fashion", "挂件": "ornaments", "连衣裙": "dress",
}


def product_query(query):
    translated = query.strip()
    for original, english in PRODUCT_TERMS.items():
        translated = translated.replace(original, " " + english + " ")
    return " ".join(translated.split())[:500]


class SearchVideos(BaseModel):
    query: str = Field(min_length=1, max_length=200)


class RemakeVideo(BaseModel):
    product: str = Field(default="", max_length=200)
    caption: str = Field(default="", max_length=60)
    request_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[A-Za-z0-9_-]{8,80}$")


@app.post("/api/videos/search", status_code=202)
def search_videos(value: SearchVideos):
    query = value.query.strip()
    if not query:
        raise HTTPException(422, "输入要找的商品")
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute("SELECT * FROM jobs ORDER BY created DESC").fetchall():
            options = json.loads(row["options"])
            if str(options.get("search_term", "")).casefold() == query.casefold():
                recent = (datetime.now(timezone.utc) - datetime.fromisoformat(row["created"])).total_seconds() < 900
                if row["status"] in ("queued", "running") or row["status"] == "completed" and recent:
                    return {"run": row["run"], "status": row["status"]}
        name = "search-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        options = TaskInput(keywords=product_query(query), product="搜索 · " + query, mode="scout",
                            min_views=0, min_engagement=0, limit=20).model_dump()
        options["search_term"] = query
        db.execute("INSERT INTO jobs(run,created,updated,status,options) VALUES(?,?,?,?,?)",
                   (name, now(), now(), "queued", json.dumps(options)))
    return {"run": name, "status": "queued"}


@app.post("/api/videos/{identity}/remake", status_code=202)
def remake_video(identity: str, value: RemakeVideo):
    if FINDER_MODE:
        raise HTTPException(403, "复制视频链接，到 YouArt Desktop 爆款复刻入口使用")
    video = collected_videos().get(identity)
    if not video:
        raise HTTPException(404, "视频尚未收录，请先搜索")
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute("SELECT run,options FROM jobs").fetchall():
            if json.loads(row["options"]).get("request_id") == value.request_id:
                return {"run": row["run"]}
        name = "remake-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        product = value.product.strip() or (video.get("description") or "参考视频复刻")[:200]
        options = TaskInput(keywords="", product=product, caption=value.caption, mode="generate",
                            min_views=0, min_engagement=0).model_dump()
        options.update(source_url=video["url"], source_video_id=identity, request_id=value.request_id)
        db.execute("INSERT INTO jobs(run,created,updated,status,options) VALUES(?,?,?,?,?)",
                   (name, now(), now(), "queued", json.dumps(options)))
    return {"run": name}


@app.get("/api/videos")
def videos(q: str = Query(default="", max_length=500), min_views: int = Query(default=0, ge=0),
           min_engagement: float = Query(default=0, ge=0, allow_inf_nan=False),
           sort: str = Query(default="views", pattern="^(views|engagement)$")):
    collected = collected_videos()
    needle = product_query(q).casefold()
    selected = [row for row in collected.values()
                if (row.get("views") or 0) >= min_views
                and (row.get("weighted_engagement_rate") or 0) * 100 >= min_engagement
                and (not needle or needle in " ".join(str(row.get(key) or "")
                     for key in ("author", "description", "queries")).casefold())]
    key = "views" if sort == "views" else "weighted_engagement_rate"
    selected.sort(key=lambda row: (row.get(key) or 0, row.get("views") or 0), reverse=True)
    return {"verified_total": len(collected), "total": len(selected), "videos": selected}


@app.get("/api/runs/{name}")
def detail(name: str):
    with database() as db:
        job = db.execute("SELECT * FROM jobs WHERE run=?", (name,)).fetchone()
    if job and not (RUNS / name).exists():
        path = RUNS / name
    else:
        path = run_dir(name)
    entry = summary(path, job)
    if not entry:
        raise HTTPException(404)
    candidate = read_json(path / "candidates.json")
    entry.update(candidates=candidate.get("candidates", []), errors=candidate.get("errors", []),
                 storyboard=read_json(path / "storyboard.json"), layout=read_json(path / "layout-check.json"),
                 acceptance=read_json(path / "acceptance.json"))
    return entry


@app.get("/api/runs/{name}/log")
def log(name: str):
    if not SLUG.fullmatch(name):
        raise HTTPException(404)
    path = log_path(name)
    if not path.is_file():
        return {"text": "", "error": ""}
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - 16000))
        text = handle.read().decode(errors="replace")
    text = re.sub(r"https?://[^\s]+\?[^\s]+", "[媒体地址已隐藏]", text)
    lines = text.replace("\r", "\n").splitlines()
    error = next((line for line in reversed(lines) if "运行失败" in line), "")
    return {"text": "\n".join(lines[-130:]), "error": error}


@app.post("/api/tasks", status_code=202)
def create_task(options: TaskInput):
    if FINDER_MODE and options.mode != "scout":
        raise HTTPException(403, "找片版只创建视频检索任务")
    if options.model not in MODELS or not options.product.strip():
        raise HTTPException(422, "检查内容方向、商品描述和模型")
    data = options.model_dump()
    if not options.keywords.strip():
        config = settings_snapshot()
        topic = TOPICS[config["topic"]]
        data["keywords"] = topic["queries"][0]
        data["queries"] = topic["queries"]
    name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    with database() as db:
        db.execute("INSERT INTO jobs(run,created,updated,status,options) VALUES(?,?,?,?,?)",
                   (name, now(), now(), "queued", json.dumps(data)))
    return {"run": name}


@app.post("/api/runs/{name}/resume", status_code=202)
def resume_task(name: str):
    if not SLUG.fullmatch(name):
        raise HTTPException(404)
    with database() as db:
        job = db.execute("SELECT * FROM jobs WHERE run=?", (name,)).fetchone()
        if not job or job["status"] not in ("failed", "interrupted"):
            raise HTTPException(409, "当前任务无法恢复")
        options = json.loads(job["options"])
        if FINDER_MODE or options["mode"] != "generate" or not (RUNS / name / "result.json").is_file():
            raise HTTPException(409, "请新建任务重新找片")
        # CLI checkpoints own submission recovery. The workbench never removes them.
        db.execute("UPDATE jobs SET status='queued',resume=1,updated=? WHERE run=?", (now(), name))
    return {"run": name}


@app.get("/api/runs/{name}/artifact/{file:path}")
def artifact(name: str, file: str):
    root = run_dir(name)
    allowed = {"final.mp4", "flow-output.mp4", "preview.jpg", "poster.jpg", "candidates.json", "storyboard.json",
               "flow-prompt.txt", "layout-check.json", "acceptance.json"}
    if file not in allowed and not (file.startswith("rea/") and Path(file).suffix in
                                   {".html", ".jpg", ".jpeg", ".png", ".json", ".md", ".srt", ".ass"}):
        raise HTTPException(404)
    path = (root / file).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(404)
    headers = {"Cross-Origin-Resource-Policy": "same-origin"}
    if path.suffix == ".html":
        headers["Content-Security-Policy"] = "sandbox; default-src 'self' data:; style-src 'unsafe-inline'"
    return FileResponse(path, headers=headers)


app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


def main():
    os.environ["PATH"] = str(ROOT / ".venv/bin") + os.pathsep + os.environ.get("PATH", "")
    import argparse
    import webbrowser
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18782)
    parser.add_argument("--open", action="store_true", help="在默认浏览器打开工作台")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口应在 1024–65535")
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
