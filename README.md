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


## Flow2API 后端

已配置 Flow2API 网关时，设置本机环境变量 `FLOW2API_API_KEY`，可用 `FLOW2API_BASE_URL` 覆盖默认的 `http://127.0.0.1:8000`。远程网关可用 HTTPS 或 SSH 隧道。先只读核对网关和模型：

```sh
.venv/bin/python remake.py --flow2api-check
```

在运行命令末尾加 `--flow-backend flow2api`，或用 `--flow2api-model` 指定支持 8 秒竖屏文生视频的模型。默认是 `veo_3_1_t2v_fast_portrait`。该后端通过流式 API 生成，和网页后端共享分镜、字幕、下载和 MP4 验收。网关接口未返回提交前的积分报价，`--max-flow-credits` 仅约束网页后端。

Flow2API 已返回视频地址而下载中断时，可带 `--flow-backend flow2api --resume` 继续下载。生成流断开且尚未返回地址时，需要先查网关任务日志。当前网关协议未返回用于客户端恢复轮询的任务 ID。

本项目采用 [MIT 许可证](LICENSE)。第三方组件见 [THIRD_PARTY.md](THIRD_PARTY.md)。本地运行产物、浏览器配置和下载媒体由 Git 忽略。
