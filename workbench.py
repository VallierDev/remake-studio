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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
STATE = ROOT / ".workbench"
SLUG = re.compile(r"[A-Za-z0-9_-]+")
MODELS = {"omni-1.2-flash-8s-portrait", "veo-3.1-fast-8s-portrait"}
stop_worker = threading.Event()


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


class TaskInput(BaseModel):
    keywords: str = Field(min_length=1, max_length=500)
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
        cmd.extend(["--query", options["keywords"], "--product", options["product"],
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


def worker():
    while not stop_worker.is_set():
        with database() as db:
            row = db.execute("SELECT * FROM jobs WHERE status='running' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
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
        thread = threading.Thread(target=worker, daemon=True, name="remake-queue")
        thread.start()
        yield
        stop_worker.set()
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
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/")
def index():
    return FileResponse(ROOT / "web/index.html")


@app.get("/api/system")
def system():
    healthy = False
    try:
        with urlopen("http://127.0.0.1:18781/health", timeout=1) as response:
            healthy = response.status == 200
    except OSError:
        pass
    return {"flow2api": healthy, "chrome_configured": (ROOT / ".flow2api/chrome.json").is_file(),
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


@app.get("/api/videos")
def videos(q: str = Query(default="", max_length=500), min_views: int = Query(default=50000, ge=0),
           min_engagement: float = Query(default=2, ge=0, allow_inf_nan=False),
           sort: str = Query(default="views", pattern="^(views|engagement)$")):
    collected = {}
    for folder in sorted(RUNS.iterdir()):
        if not folder.is_dir() or folder.is_symlink() or not SLUG.fullmatch(folder.name):
            continue
        data = read_json(folder / "candidates.json")
        measured = data.get("checked_at", "")
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
    needle = q.strip().casefold()
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
    if options.model not in MODELS or not options.keywords.strip() or not options.product.strip():
        raise HTTPException(422, "检查关键词、商品描述和模型")
    name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    with database() as db:
        db.execute("INSERT INTO jobs(run,created,updated,status,options) VALUES(?,?,?,?,?)",
                   (name, now(), now(), "queued", options.model_dump_json()))
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
        if options["mode"] != "generate" or not (RUNS / name / "result.json").is_file():
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
