#!/usr/bin/env python3
"""Scout TikTok references, rank candidates, and prepare a video production brief."""
import argparse
import fcntl
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from indexed_search import VIDEO_URL, search

ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / "vendor/tiktok-viral-ai-automation"
YTDLP_ARGS = ["--ignore-config", "--no-update", "--no-playlist", "--socket-timeout", "15", "--retries", "1"]


def save_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def fetch(url):
    match = VIDEO_URL.fullmatch(url)
    if not match:
        raise ValueError("需要 TikTok 完整视频链接（含 /@用户名/video/编号）")
    result = subprocess.run(["yt-dlp", *YTDLP_ARGS, "--skip-download", "--dump-json", url],
                            capture_output=True, text=True, check=True, timeout=65)
    source = json.loads(result.stdout)
    if str(source.get("id")) != match[1] or not source.get("view_count"):
        raise ValueError("视频 ID 或播放量无法核实")
    if not source.get("duration") or not any(
            fmt.get("vcodec") not in (None, "none") for fmt in source.get("formats", [])):
        raise ValueError("未核实到可播放的视频轨")
    return {
        "id": match[1], "url": url.split("?")[0].split("#")[0], "author": source.get("uploader"),
        "description": source.get("description"), "duration": source.get("duration"),
        "upload_date": source.get("upload_date"),
        "views": source.get("view_count"), "likes": source.get("like_count"),
        "comments": source.get("comment_count"), "shares": source.get("repost_count"),
        "saves": source.get("save_count"),
    }


def scout(tag, limit):
    script = """
import json
import sys
from unittest.mock import patch
from src import tiktok_deep_scrape as upstream

if sys.argv[3]:
    with patch.object(upstream.ChromeDriverManager, "install", return_value=sys.argv[3]):
        rows = upstream.scrape_tiktok_data(sys.argv[1], int(sys.argv[2]))
else:
    rows = upstream.scrape_tiktok_data(sys.argv[1], int(sys.argv[2]))
print(json.dumps(rows))
"""
    bundled = ROOT / "vendor/chrome-driver/chromedriver-mac-arm64/chromedriver"
    driver = os.environ.get("TIKTOK_CHROMEDRIVER") or (str(bundled) if bundled.is_file() else "")
    out = subprocess.run([str(ROOT / ".scout-venv/bin/python"), "-c", script, tag, str(limit), driver],
                         cwd=UPSTREAM, capture_output=True, text=True, check=True, timeout=140)
    return [row["video_link"] for row in json.loads(out.stdout)
            if VIDEO_URL.fullmatch(row.get("video_link", ""))]


def failure_reason(error):
    if isinstance(error, subprocess.TimeoutExpired):
        return f"超过 {error.timeout} 秒"
    details = getattr(error, "stderr", "") or str(error)
    if isinstance(details, bytes):
        details = details.decode(errors="replace")
    lines = [line for line in details.splitlines() if line.strip()]
    return lines[-1][:300] if lines else type(error).__name__


def discover(args, run):
    urls = list(args.url)
    for seed_file in args.seed_file:
        urls.extend(line.strip() for line in seed_file.read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#"))
    errors = []
    origins = {}
    for tag in args.tag:
        try:
            hits = scout(tag, args.limit)
            if not hits:
                errors.append(f"#{tag}: 标签页没有返回视频链接")
            urls.extend(hits)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(f"#{tag}: {failure_reason(exc)}")
    for query in args.query:
        try:
            hits, lookup_errors = search(query, args.limit)
            errors.extend(f"{query}: {error}" for error in lookup_errors)
            if not hits:
                errors.append(f"{query}: 公开索引没有返回直接视频链接")
            for url, sources in hits.items():
                match = VIDEO_URL.fullmatch(url)
                if match:
                    origins.setdefault(match[1], set()).update(sources)
                    urls.append(url)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(f"{query}: {failure_reason(exc)}")
    unique = {}
    for url in urls:
        match = VIDEO_URL.fullmatch(url)
        unique[match[1] if match else url] = url
    def collect(url):
        try:
            return fetch(url), None
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return None, f"{url}: {failure_reason(exc)}"
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(collect, unique.values()))
    rows = []
    required = [term.casefold().strip() for term in args.require]
    for row, error in results:
        if error:
            errors.append(error)
            continue
        row["discovery_sources"] = sorted(origins.get(row["id"], ()))
        row["weighted_engagement_rate"] = sum((row[key] or 0) * weight for key, weight in
            [("likes", 1), ("comments", 2), ("shares", 3), ("saves", 2)]) / row["views"]
        caption = (row["description"] or "").casefold()
        row["eligible"] = (row["views"] >= args.min_views
                           and row["weighted_engagement_rate"] * 100 >= args.min_engagement
                           and all(term in caption for term in required))
        rows.append(row)

    primary, secondary = ("views", "weighted_engagement_rate") if args.sort == "views" else (
        "weighted_engagement_rate", "views")
    rows.sort(key=lambda row: (row[primary], row[secondary]), reverse=True)
    eligible = [row for row in rows if row["eligible"]]
    save_json(run / "candidates.json", {"checked_at": datetime.now(timezone.utc).isoformat(),
              "tags": args.tag, "queries": args.query, "min_views": args.min_views,
              "min_engagement_percent": args.min_engagement, "required_caption_terms": args.require,
              "sort": args.sort, "eligible_count": len(eligible), "candidates": rows, "errors": errors})
    for row in eligible:
        print(f"{row['views']:>9,} 播放  {row['weighted_engagement_rate']:.2%} 加权互动率  {row['url']}")
    print(f"回源核实 {len(rows)} 条，符合筛选 {len(eligible)} 条。{run / 'candidates.json'}")
    for error in errors:
        print(error, file=sys.stderr)
    if not eligible:
        raise ValueError("没有符合筛选条件的候选，详情见 candidates.json")
    return next((row for row in eligible if row["id"] == args.pick), None) if args.pick else eligible[0]


def prepare(args, run, selected):
    reference = args.file.resolve() if args.file else run / f"reference-{selected['id']}.mp4"
    if not args.file and not reference.exists():
        subprocess.run(["yt-dlp", *YTDLP_ARGS, "-f", "b[ext=mp4]/b", "-o", str(reference),
                        selected["url"]], check=True, timeout=180)
    if not reference.is_file():
        raise ValueError(f"视频文件不存在：{reference}")
    ffprobe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", str(reference)],
                            check=True, capture_output=True, text=True, timeout=20)
    duration = float(ffprobe.stdout)
    breakdown = run / "rea"
    start = datetime.now(timezone.utc)
    env = dict(os.environ, REA_VIDEO_HOME=str(ROOT / ".rea-state"))
    command = [str(ROOT / ".venv/bin/python"), "-m", "rea_video.cli", "decompose",
               str(reference), "-o", str(breakdown)]
    if not args.audio_detail:
        command.extend(["--no-stems", "--no-asr"])
    subprocess.run(command, check=True, timeout=600, env=env)
    data = json.loads((breakdown / "breakdown.json").read_text())
    shots = data.get("shots") or []
    if not shots or not (breakdown / "report.html").is_file():
        raise ValueError("REA 没有产出完整报告和镜头数据")
    timings = []
    for shot in shots:
        start_sec = shot.get("start_sec", shot.get("start_s", shot.get("start")))
        end_sec = shot.get("end_sec", shot.get("end_s", shot.get("end")))
        if isinstance(start_sec, (int, float)) and isinstance(end_sec, (int, float)):
            timings.append(f"- {start_sec:.2f}–{end_sec:.2f} 秒")
    timing_text = "\n".join(timings) or "打开 rea/report.html 查看时间轴。"
    source_text = f"{selected['url']}（查询时 {selected['views']:,} 播放）" if selected else str(reference)
    notes = args.notes.read_text().strip() if args.notes else ""
    (run / "youart-brief.md").write_text(f"""# YouArt Desktop 制作任务

商品或主题：{args.product}
参考：{source_text}
原片 {duration:.1f} 秒，REA 检出 {len(shots)} 镜。

{timing_text}

{notes}

请用提供的自有素材，制作一条适合 TikTok 的原创 9:16 视频。参考时间轴里的镜头长短和节奏。第一秒给出具体看点，后续镜头展示商品实际细节和使用效果。按商品调整文案和运镜，输出一条 MP4。请用新写的字幕和有授权的配乐。原片只用于拆解参考。

实际画面和字幕以 rea/report.html 为准。机器切点是初稿，遇到同一镜内的换装要看片确认。
""")
    (run / "gemini-prompt.txt").write_text(f"""生成一条 9:16 的原创 TikTok 商品展示样片，约 8 秒。
主题：{args.product}

可参考的拍摄和换装节奏：
{notes or timing_text}

第一个镜头直接展示商品或上身反差，中段展示商品卖点，收尾给一个清晰结果。请使用原创角色、场景和声音。画面留出字幕空间，不生成文字、水印或品牌标志。输出视频。
""")
    save_json(run / "result.json", {"source": source_text, "duration": duration, "shots": len(shots),
              "product": args.product, "notes": notes,
              "rea_seconds": round((datetime.now(timezone.utc) - start).total_seconds(), 1),
              "generation": {"status": "ready", "outputs": {}}})
    print(f"报告：{breakdown / 'report.html'}\nGemini 提示词：{run / 'gemini-prompt.txt'}"
          f"\nYouArt 制作任务：{run / 'youart-brief.md'}")


def record_video(run, path, provider):
    result_path = run / "result.json"
    result = json.loads(result_path.read_text())
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height:format=duration", "-of", "json", str(path)],
                          check=True, capture_output=True, text=True, timeout=20)
    media = json.loads(info.stdout)
    stream = next(iter(media.get("streams", [])), {})
    width, height = stream.get("width", 0), stream.get("height", 0)
    duration = float(media.get("format", {}).get("duration", 0))
    if duration <= 0 or not 0 < width < height:
        raise ValueError("需要有可读画面和时长的竖屏成片")
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
                   check=True, timeout=90)
    target = run / f"{provider}-output.mp4"
    if path.resolve() != target.resolve():
        temp = target.with_suffix(".tmp")
        shutil.copy2(path, temp)
        temp.replace(target)
    result.pop("youart", None)
    generation = result.setdefault("generation", {"outputs": {}})
    generation["status"] = "verified"
    generation["outputs"][provider] = {"file": target.name, "width": width, "height": height, "duration": duration}
    save_json(result_path, result)
    print(f"已验收：{target} · {width}×{height} · {duration:.2f}s")


def automate(args, run):
    try:
        from finishing import finish
        from flow import generate
        from storyboard import build
    except ImportError as error:
        raise ValueError("请先运行 ./setup.sh，并用 .venv/bin/python 启动全流程") from error

    result_path = run / "result.json"
    with (run / ".flow.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("这个运行已经有一个 Flow 进程") from error
        result = json.loads(result_path.read_text())
        result.setdefault("generation", {"status": "ready", "outputs": {}})
        generation = result["generation"]
        if generation.get("final") and (run / generation["final"]["file"]).is_file():
            print(f"已验收：{run / generation['final']['file']}")
            return
        storyboard_path = run / "storyboard.json"
        if storyboard_path.is_file() and (run / "flow-prompt.txt").is_file():
            plan = json.loads(storyboard_path.read_text())
        else:
            print("正在从 REA 报告提炼 8 秒三镜分镜", flush=True)
            plan = build(run, result.get("product", args.product), result.get("notes", ""),
                         args.vision, args.vision_model)
        backend = args.flow_backend
        if backend == "flow2api":
            if (run / "flow-job.json").exists() and not (run / "flow-output.mp4").exists():
                raise ValueError("这个任务已有网页提交记录。使用新运行名切换到 Flow2API。")
            from flow2api import generate as generate_api
            raw, job = generate_api(run, args.flow2api_url, args.flow2api_model)
        else:
            if (run / "flow2api-job.json").exists() and not (run / "flow-output.mp4").exists():
                raise ValueError("这个任务使用了 Flow2API，恢复时请保留 --flow-backend flow2api。")
            raw, job = generate(run, args.flow_browser, args.chrome_profile if args.flow_browser == "glance"
                                else args.flow_profile, args.max_flow_credits, save_json)
        headline = args.caption or plan["headline"]
        final = finish(run, raw, headline, args.font)
        generation["status"] = "verified"
        generation["final"] = final
        generation.setdefault("outputs", {})["flow"] = {"file": raw.name, "credits": job.get("credits"),
                                                         "model": job.get("model"), "backend": backend}
        generation["caption"] = headline
        generation["storyboard"] = storyboard_path.name
        save_json(result_path, result)
        print(f"已验收：{run / final['file']} · {final['width']}×{final['height']} · {final['duration']:.2f}s")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--query", action="append", default=[], help="关键词，通过公开索引自动发现 TikTok 视频")
    p.add_argument("--tag", action="append", default=[], help="TikTok 话题，可重复")
    p.add_argument("--seed-file", type=Path, action="append", default=[], help="公开候选链接文本，每行一条")
    p.add_argument("--url", action="append", default=[], help="公开 TikTok 视频链接，可重复")
    p.add_argument("--file", type=Path, help="已有本地视频，可单独使用，也可配一个对应的 --url")
    p.add_argument("--notes", type=Path, help="人工复核与复拍要求，追加到制作任务")
    p.add_argument("--audio-detail", action="store_true", help="启用人声分离与本地听写，首次需下载模型")
    p.add_argument("--limit", type=int, default=8, help="每个关键词或话题最多回源核实的候选数，1–20")
    p.add_argument("--min-views", type=int, default=50000)
    p.add_argument("--min-engagement", type=float, default=0, help="最低加权互动率，2 表示 2%%")
    p.add_argument("--sort", choices=("views", "engagement"), default="engagement")
    p.add_argument("--require", action="append", default=[], help="要求原始视频文案含这个词，可重复")
    p.add_argument("--pick", help="选择达到门槛的指定视频 ID")
    p.add_argument("--product", default="圣诞派对女装")
    p.add_argument("--scout-only", action="store_true")
    p.add_argument("--output", type=Path, help="验收下载好的竖屏成片并关联到已有运行")
    p.add_argument("--provider", choices=("flow", "gemini", "youart"), default="gemini")
    p.add_argument("--flow", action="store_true", help="提交 Flow 并自动下载、加标题和验收")
    p.add_argument("--resume", action="store_true", help="继续同名任务的 Flow 阶段")
    p.add_argument("--flow-login", action="store_true", help="登录项目的独立 Chrome 配置")
    p.add_argument("--flow-backend", choices=("web", "flow2api"), default="web")
    p.add_argument("--flow2api-url", help="Flow2API 服务地址，也可设 FLOW2API_BASE_URL")
    p.add_argument("--flow2api-model", default="veo_3_1_t2v_fast_portrait")
    p.add_argument("--flow2api-check", action="store_true", help="核对网关连接和模型列表")
    p.add_argument("--flow-browser", choices=("playwright", "glance"), default="playwright")
    p.add_argument("--flow-profile", type=Path, default=ROOT / ".flow-profile")
    p.add_argument("--chrome-profile", default="default", help="Glance Chrome 配置名")
    p.add_argument("--max-flow-credits", type=int, default=24)
    p.add_argument("--vision", choices=("local", "glance", "ollama"), default="local")
    p.add_argument("--vision-model", help="Ollama 本机视觉模型名")
    p.add_argument("--caption", help="覆盖自动生成的片头标题")
    p.add_argument("--font", type=Path, help="覆盖成片标题字体")
    p.add_argument("--run", help="运行目录名，字母、数字、横线或下划线")
    args = p.parse_args()
    if args.flow2api_check:
        from flow2api import check_model
        check_model(args.flow2api_url, args.flow2api_model)
        print(f"Flow2API 模型已核对：{args.flow2api_model}")
        return
    if args.flow_login:
        from flow import login
        login(args.flow_profile)
        return
    if not args.run or not re.fullmatch(r"[A-Za-z0-9_-]+", args.run) or not 1 <= args.limit <= 20 or args.min_views < 0:
        p.error("检查 --run、--limit 和 --min-views")
    if not math.isfinite(args.min_engagement) or args.min_engagement < 0:
        p.error("--min-engagement 必须是非负的百分比数值")
    run = ROOT / "runs" / args.run
    has_source = bool(args.tag or args.query or args.url or args.seed_file)
    if args.flow and (args.output or args.scout_only):
        p.error("--flow 不能与 --scout-only 或 --output 合用")
    if args.max_flow_credits < 0 or (args.vision == "ollama" and not args.vision_model):
        p.error("检查积分上限，Ollama 模式需要 --vision-model")
    if args.resume:
        if not args.flow or has_source or args.file or args.output:
            p.error("--resume 需要 --flow 和原 --run 名称")
        automate(args, run)
        return
    if args.output:
        if has_source or args.file or args.scout_only:
            p.error("--output 用于验收已有运行，单独指定 --run 和 --provider")
        record_video(run, args.output, args.provider)
        return
    if not (has_source or args.file):
        p.error("指定 --query、--tag、--url、--seed-file 或 --file")
    if args.file and (args.tag or args.query or args.seed_file or len(args.url) > 1 or args.scout_only or args.pick):
        p.error("--file 只能与单个对应的 --url 合用")
    if args.file and not args.file.is_file():
        p.error(f"视频文件不存在：{args.file}")
    if not args.scout_only and not shutil.which("ffprobe"):
        p.error("需要 FFmpeg（含 ffprobe）")
    run.mkdir(parents=True, exist_ok=False)
    selected = discover(args, run) if has_source else None
    if has_source and selected is None:
        raise ValueError("--pick 指定的视频不符合筛选条件，详情见 candidates.json")
    if not args.scout_only:
        prepare(args, run, selected)
        if args.flow:
            automate(args, run)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        sys.exit(1)
