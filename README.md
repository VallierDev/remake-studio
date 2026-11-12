# TikTok Remake

从关键词找到 TikTok 参考视频，用 REA-Video 拆解镜头，再用 Google Flow 生成、下载并验收一条原创竖屏视频。也可以单独找片、拆片，或导入已有视频。

Find TikTok references from keywords, break down shots with REA-Video, then generate and validate an original vertical clip through Google Flow. The CLI runs locally. Flow runs in the browser.

## 安装 / Setup

需要 macOS 或 Linux、Git、uv、Python 3.13、Google Chrome、yt-dlp 和 FFmpeg。中文标题需要本机中文字体，Linux 可安装 Noto Sans CJK。

```sh
./setup.sh
.venv/bin/python remake.py --flow-login
```

首次在打开的独立 Chrome 窗口登录自己的 Google Flow 账号。之后使用同一份本地浏览器配置。项目不会读取其他 Chrome 配置的密码或 Cookie。启用可选的 `--flow-browser glance` 时，使用你已配置的 Glance Chrome Bridge 会话。

## 运行 / Usage

```sh
.venv/bin/python remake.py --run holiday-demo \
  --query '"christmas party outfit" red dress' --limit 16 \
  --min-views 50000 --min-engagement 2 --sort views \
  --require christmas --require dress \
  --product '公司聖誕派對女裝，主款酒紅亮片裙' \
  --flow --max-flow-credits 24
```

- 加 `--scout-only` 时只找片和排序，并移除 `--flow`。`--url` 和 `--seed-file` 也接受明确的公开候选，`--file` 可拆本地视频。
- `--query` 从公开索引发现 TikTok 链接，再向 TikTok 核实 ID、播放量、互动和视频轨。索引覆盖及平台可用性会影响结果。公开互动数据适用于筛参考片。
- 加权互动率为（赞 + 2×评论 + 3×分享 + 2×收藏）/ 播放。缺失项在数据里保留空值，计算时按 0。`--min-engagement 2` 表示 2%。`--sort engagement` 按加权互动率排，`--sort views` 按播放量排。
- 默认分镜基于 REA 切点和 OCR。指定 `--vision ollama --vision-model <本地视觉模型>` 时，将 REA 分镜图交给本机 Ollama 识别画面。已有 Glance 的用户可指定 `--vision glance`，这会把分镜图和分析问题交给 Glance 配置的视觉服务。
- `--flow` 明确启用 Flow 生成。一轮只提交一条 9:16、720p、8 秒视频，提交前核对页面价格，超过 `--max-flow-credits` 或读不到价格会停止。Flow 网页界面、登录权限、地区和额度会影响可用性。

如果生成或下载中断，使用原任务名恢复。若原命令指定了 `--flow-browser glance`、自定义浏览器配置或字体，恢复时也带上相同参数。已提交的任务会重新检查原项目，避免重复生成。

```sh
.venv/bin/python remake.py --run holiday-demo --flow --resume
```

输出保存在 `runs/<运行名>/`。从 `candidates.json` 查看筛选结果，从 `rea/report.html` 查看切点和包装分析，从 `storyboard.json` 和 `flow-prompt.txt` 查看制作计划。`flow-output.mp4` 为 Flow 原片，`final.mp4` 为带标题且通过解码检查的成片，`preview.jpg` 为逐秒抽帧。字幕默认使用分镜标题，可用 `--caption` 和 `--font` 覆盖。YouArt 演示使用单独生成的 `youart-brief.md`。


## 项目内 Flow2API

安装项目自己管理的 Flow2API 运行时，并登录 Google Flow：

```sh
./setup.sh --flow2api
.venv/bin/python remake.py --flow2api-use-chrome
.venv/bin/python remake.py --flow2api-check
```

安装器从公开上游下载固定版本到 `vendor/flow2api`，依赖位于 `.flow2api-venv`。启动时生成随机的本地 API 和管理员凭据，配置、数据库、缓存和登录浏览器放在忽略提交的 `.flow2api/`。服务只监听 `127.0.0.1:18781`，生成前会自动检查和启动它。

默认复用 Glance Chrome Bridge 连接的当前 Chrome。在 Chrome 打开并登录 `flow.google.com`，运行 `--flow2api-use-chrome` 后，项目内服务会导入这个会话。生成时，本机 WebSocket 桥接通过同一浏览器完成 Flow 所需的站点验证。每轮请求结束后自动关闭本机桥接进程，保留浏览器标签页。Google 出现交互式验证码或登录提示时需要人在浏览器处理。

`--chrome-profile` 可指定 Glance 的浏览器配置名，默认是 `default`。`--flow2api-login` 保留为独立 Chrome 登录工具。

```sh
.venv/bin/python remake.py --flow2api-service status
.venv/bin/python remake.py --flow2api-service stop
.venv/bin/python remake.py --flow2api-service start
```

从关键词到字幕成片可直接运行：

```sh
.venv/bin/python remake.py --run christmas-demo \
  --query '"christmas party outfit" red dress' --limit 16 \
  --min-views 50000 --min-engagement 2 --sort views \
  --require christmas --require dress \
  --product '聖誕派對穿搭，酒紅亮片連衣裙' \
  --caption 'POV：公司的聖誕派對' \
  --flow --flow-backend flow2api --flow2api-model omni-1.2-flash-8s-portrait
```

生成后会逐秒检查大块纯色边带，并将检查结果写进 `layout-check.json`。检测不通过时保留原片和任务记录，停止交付。标题直接叠加在实际画面上，`--caption` 可写自然的观众用语。

在生成命令末尾加 `--flow --flow-backend flow2api`。默认模型是 `veo-3.1-fast-8s-portrait`，可用 `--flow2api-model omni-1.2-flash-8s-portrait` 等模型覆盖。它和网页后端共享分镜、下载、字幕、成片验收。网关接口未返回提交前的积分报价，`--max-flow-credits` 仅约束网页后端。

已返回视频地址而下载中断时，带 `--flow-backend flow2api --resume` 继续下载。生成流断开且尚未返回地址时，需要先查看本地任务日志，避免重复提交。流式接口目前没有返回可用于客户端恢复轮询的任务 ID。

需要连接自行部署的其他兼容网关时，显式设置 `--flow2api-url` 和本机环境变量 `FLOW2API_API_KEY`。运行产物中的 `flow2api-job.json` 保存当前任务进度。

本项目采用 [MIT 许可证](LICENSE)。第三方组件见 [THIRD_PARTY.md](THIRD_PARTY.md)。本地运行产物、浏览器配置和下载媒体由 Git 忽略。
