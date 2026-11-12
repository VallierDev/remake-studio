"""Render a title and validate the delivered vertical MP4."""
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont


def verify(path):
    data = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height:format=duration", "-of", "json", str(path)],
                          capture_output=True, text=True, check=True, timeout=20)
    media = json.loads(data.stdout)
    stream = next(iter(media.get("streams", [])), {})
    w, h = stream.get("width", 0), stream.get("height", 0)
    seconds = float(media.get("format", {}).get("duration", 0))
    if not 0 < w < h or not 0 < seconds < 60:
        raise ValueError("成片缺少有效竖屏视频轨或时长异常")
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0",
                    "-map", "0:a?", "-f", "null", "-"], check=True, timeout=90)
    return {"width": w, "height": h, "duration": seconds}


def font_path(text, override):
    if override:
        return str(override)
    cjk = any(ord(char) > 0x2fff for char in text)
    fonts = ("/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc") if cjk else (
             "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    for font in fonts:
        if Path(font).is_file():
            return font
    raise ValueError("请通过 --font 指定字体。中文标题可用 Noto Sans CJK。")


def finish(run, raw, headline, font):
    spec = verify(raw)
    width, height = spec["width"], spec["height"]
    if abs(width / height - 9 / 16) > 0.025 or abs(spec["duration"] - 8) > 1:
        raise ValueError("Flow 输出没有达到 9:16、约 8 秒的交付规格")
    if not headline.strip():
        raise ValueError("标题为空")
    overlay = run / "caption.png"
    canvas = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(canvas)
    size = round(width / 18)
    path = font_path(headline, font)
    while True:
        face = ImageFont.truetype(path, size)
        if draw.textlength(headline, font=face) < width * 0.86 or size <= 16:
            break
        size -= 2
    draw.text((width / 2, height * 0.145), headline, font=face, anchor="mm",
              fill="white", stroke_width=max(2, round(width / 240)), stroke_fill=(15, 14, 14, 230))
    canvas.save(overlay)
    final, temp = run / "final.mp4", run / "final.pending.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-loop", "1", "-i", str(overlay),
                    "-filter_complex", "[0:v][1:v]overlay=shortest=1:format=auto,format=yuv420p[v]",
                    "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-crf", "17", "-preset", "fast",
                    "-c:a", "copy", "-movflags", "+faststart", str(temp)], check=True, timeout=120)
    result = verify(temp)
    temp.replace(final)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(final),
                    "-vf", "fps=1,scale=180:-2,tile=4x2", "-frames:v", "1", str(run / "preview.jpg")],
                   check=True, timeout=30)
    return {"file": final.name, **result}
