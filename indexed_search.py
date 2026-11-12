"""Find public TikTok video URLs in web indexes."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
import re
import subprocess
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from mcp_stdio import Glance


VIDEO_URL = re.compile(r"https://(?:www\.)?tiktok\.com/@[\w.-]+/video/(\d+)(?:[/?#].*)?$")
INDEXES = (
    ("yahoo-jp", "https://search.yahoo.co.jp/search", "p"),
    ("naver", "https://search.naver.com/search.naver", "query"),
)
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")


class VideoLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = {}

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        url = dict(attrs).get("href")
        match = VIDEO_URL.fullmatch(url or "")
        if match:
            self.urls.setdefault(match[1], f"https://www.tiktok.com{urlsplit(url).path.rstrip('/')}")


def index_links(index, query):
    name, endpoint, param = index
    request = Request(f"{endpoint}?{urlencode({param: query})}", headers={
        "User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8",
    })
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read(2_000_000).decode("utf-8", errors="replace")
        parser = VideoLinks()
        parser.feed(body)
        return name, list(parser.urls.values()), None
    except (OSError, ValueError) as exc:
        return name, [], f"{name}: {exc}"


def search(query, limit):
    query = f"site:tiktok.com {query}"
    with ThreadPoolExecutor(max_workers=len(INDEXES)) as pool:
        results = list(pool.map(lambda index: index_links(index, query), INDEXES))
    matches = {}
    for position in range(max((len(urls) for _, urls, _ in results), default=0)):
        for name, urls, _ in results:
            if position < len(urls):
                matches.setdefault(urls[position], []).append(name)
    errors = [error for _, _, error in results if error]
    if not matches:
        try:
            matches = {url: ["glance"] for url in glance_search(query, limit)}
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(f"glance: {exc}")
    return dict(list(matches.items())[:limit]), errors


def glance_search(query, limit):
    with Glance() as client:
        output = client.tool("web_search", {"query": query, "top_k": limit}, timeout=40)
    payload = json.loads(output)
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, list):
        return []
    urls = []
    for row in payload:
        link = row.get("link", "") if isinstance(row, dict) else ""
        match = VIDEO_URL.fullmatch(link)
        if match:
            direct = f"https://www.tiktok.com{urlsplit(link[:match.end(1)]).path}"
            if direct not in urls:
                urls.append(direct)
    return urls[:limit]
