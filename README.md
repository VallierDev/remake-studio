# Remake Studio · TikTok 爆款视频查找器

输入商品名称，搜索相关 TikTok 视频，按播放量和互动数据筛选参考素材。选好视频后，复制链接到 YouArt Desktop 的“爆款复刻 / Viral Remake”入口，上传自己的商品图并说明修改目标。

Search TikTok reference videos by product keywords, compare views and engagement, and copy a selected video link into YouArt Desktop's Viral Remake workflow.

![TikTok 视频查找工作台](docs/workbench.png)

## 安装与启动

需要 macOS 或 Linux，以及 Git 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。安装器会准备 Python 环境和找片依赖。

下载 [源码 ZIP](https://github.com/VallierDev/remake-studio/archive/refs/heads/codex/open-source.zip) 并解压，或克隆仓库，然后在项目目录运行：

```sh
git clone https://github.com/VallierDev/remake-studio.git
cd remake-studio
./setup.sh --finder
./start-finder.sh
```

浏览器打开 `http://127.0.0.1:18782`。

## 查找参考视频

1. 输入商品名称，例如“圣诞挂件”“圣诞树”“圣诞晚礼服”，点击“搜索视频”。
2. 查看真实视频的播放量、点赞和加权互动率，点击标题打开原片。
3. 按需调整播放量、互动率和排序条件。
4. 点击“复制链接”，进入 YouArt Desktop 的“爆款复刻”入口。

可向 YouArt 提出具体要求：

> 参考这条视频的开场、运镜和剪辑节奏，改成我的圣诞挂件。先展示挂件细节，再拍挂上圣诞树后的效果。使用新的场景和文案，做一条适合 TikTok 的竖屏带货视频。

从 [YouArt 官方下载页](https://youart.ai/download) 安装桌面应用。

## 列表与自动更新

列表汇总已收录视频并去重，保留最近一次核实的播放与互动数据。搜索会从公开索引发现 TikTok 链接，再向 TikTok 回源核实视频和计数。结果受索引覆盖、网络和平台可用性影响。

在“设置”选择内容方向、更新频率或暂停更新。默认方向为圣诞女装，每 6 小时更新。工作台服务运行时，关闭浏览器页面后仍会继续执行到期任务。

加权互动率为（点赞 + 2×评论 + 3×分享 + 2×收藏）/ 播放量。缺失项以空值保存，计算时按 0。

## 本地数据

搜索结果与原始计数保存在 `runs/`，队列和设置保存在 `.workbench/`。这些目录由 Git 忽略。

## 开源与源码打包

本项目采用 [MIT 许可证](LICENSE)。第三方组件见 [THIRD_PARTY.md](THIRD_PARTY.md)。

提交源码改动后运行 `./package.sh`，会在 `dist/` 导出当前提交的源码 ZIP。

开发检查：

```sh
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```
