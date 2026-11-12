# Third-party components

`setup.sh` fetches the upstream source at pinned revisions. The `vendor/` directory is local and excluded from the repository. Each dependency retains its own license.

- [REA-Video](https://github.com/JuneYaooo/rea-video) performs local video decomposition. Apache-2.0.
- [TikTok Viral AI Automation](https://github.com/abuzar561/tiktok-viral-ai-automation) provides the optional hashtag scraper and inspired the weighted engagement formula. MIT.
- [Playwright](https://github.com/microsoft/playwright-python), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [FFmpeg](https://ffmpeg.org/legal.html) and [Pillow](https://github.com/python-pillow/Pillow) provide browser and media tooling.
- Glance and Ollama are optional local integrations. Google Flow is a hosted web service accessed through the user's own browser account.

Flow2API is an optional, independently deployed gateway. This project calls its OpenAI-compatible interface and does not distribute the gateway.
