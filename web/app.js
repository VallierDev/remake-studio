const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const state = {
  runs: [],
  selected: null,
  detail: null,
  view: "result",
  fingerprint: "",
  polling: false,
};
const statuses = {
  completed: "已完成",
  scouted: "已找片",
  running: "运行中",
  queued: "等待中",
  failed: "失败",
  interrupted: "已中断",
  incomplete: "待继续",
};
const stages = [
  ["searching", "找参考"],
  ["decomposing", "REA 拆解"],
  ["planning", "生成分镜"],
  ["generating", "Flow 生成"],
  ["finishing", "成片验收"],
];
const assetURL = (name, file) =>
  "/api/runs/" +
  encodeURIComponent(name) +
  "/artifact/" +
  file.split("/").map(encodeURIComponent).join("/");
const formatCount = (value) =>
  value == null ? "—" : Number(value).toLocaleString("zh-CN");
const shortDate = (value) =>
  new Date(value).toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
const phaseTitle = (value) =>
  ({
    queued: "等待前一任务完成",
    searching: "正在寻找参考视频",
    discovered: "已找到参考视频",
    decomposing: "正在分析镜头和字幕",
    planning: "正在提炼分镜",
    generating: "正在通过 Flow 生成",
    finishing: "正在添加标题并验收",
    completed: "成片已就绪",
  })[value] || "任务待继续";

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "请求未完成，请检查输入或稍后重试",
    );
  }
  return response.json();
}

function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("#toast").hidden = true), 4500);
}

function showCreate() {
  $("#form-error").textContent = "";
  $("#create-dialog").showModal();
  $("#create-form [name=keywords]").focus();
}

function renderRuns() {
  $("#run-count").textContent = state.runs.length;
  $("#run-list").innerHTML =
    state.runs
      .map(
        (run) => `
    <button class="run-item ${run.run === state.selected ? "active" : ""}" data-run="${escapeHTML(run.run)}"
      aria-current="${run.run === state.selected ? "page" : "false"}">
      <span class="run-title">${escapeHTML(run.title)}</span>
      <span class="run-meta"><span>${shortDate(run.created)}</span>
      <span class="run-status"><i class="dot ${escapeHTML(run.status)}"></i>${statuses[run.status] || run.status}</span></span>
    </button>`,
      )
      .join("") || '<div class="loading">还没有任务，点击上方新建。</div>';
}

function renderProgress(detail) {
  let current =
    {
      queued: -1,
      searching: 0,
      discovered: 0,
      decomposing: 1,
      planning: 2,
      generating: 3,
      finishing: 4,
      completed: 5,
    }[detail.stage] ?? 0;
  const scoutDone =
    detail.status === "scouted" ||
    (detail.options.mode === "scout" && detail.status === "completed");
  if (scoutDone) current = 1;
  $("#pipeline").innerHTML = stages
    .map(
      ([key, title], index) => `
    ${index ? '<div class="step-connector"></div>' : ""}
    <div class="step ${index < current ? "done" : index === current && !scoutDone ? "active" : ""}">
      <span class="step-icon">${index < current ? "✓" : index + 1}</span><span>${title}</span>
    </div>`,
    )
    .join("");
}

async function selectRun(name) {
  state.selected = name;
  state.fingerprint = "";
  state.detail = await api("/api/runs/" + encodeURIComponent(name));
  history.replaceState(null, "", "?run=" + encodeURIComponent(name));
  renderRuns();
  renderDetail();
}

function renderDetail() {
  const detail = state.detail;
  if (!detail) return;
  $("#task-title").textContent = detail.title;
  $("#breadcrumb").textContent = detail.title;
  $("#task-subtitle").textContent = detail.keywords || detail.run;
  $("#ref-count").textContent = detail.candidate_count || "";
  $("#task-actions").innerHTML = `
    ${detail.has_video ? '<a class="primary" href="' + assetURL(detail.run, "final.mp4") + '" download="remake-' + escapeHTML(detail.run) + '.mp4">↓ 下载成片</a>' : ""}
    ${detail.can_resume ? '<button class="secondary" id="resume-task">继续生成</button>' : ""}
  `;
  renderProgress(detail);
  const notice = $("#task-notice");
  notice.hidden = true;
  notice.className = "notice";
  if (detail.status === "failed" || detail.status === "interrupted") {
    notice.textContent = detail.can_resume
      ? "任务已停止。查看运行日志定位原因，继续生成会保留已有提交记录。"
      : "任务已停止。查看运行日志后，可用新的关键词或筛选条件创建任务。";
    notice.hidden = false;
    api("/api/runs/" + encodeURIComponent(detail.run) + "/log")
      .then((log) => {
        if (state.selected === detail.run && log.error)
          notice.textContent = log.error;
      })
      .catch(() => {});
  } else if (detail.status === "running" || detail.status === "queued") {
    notice.className = "notice info";
    notice.textContent =
      phaseTitle(detail.stage) + "，你可以离开这个页面，任务会在本机继续运行。";
    notice.hidden = false;
  }
  renderWorkspace();
}

function videoView(detail) {
  const final = detail.final;
  const model = detail.options.model || detail.acceptance?.model || "Flow";
  const source =
    detail.candidates.find((row) => row.eligible) || detail.candidates[0];
  const content = detail.has_video
    ? `
    <video id="result-video" controls playsinline preload="metadata" poster="${detail.has_poster ? assetURL(detail.run, "poster.jpg") : ""}" src="${assetURL(detail.run, "final.mp4")}"></video>
  `
    : `<div class="empty" style="border:0;min-height:0"><div class="empty-symbol">▷</div>
    <h2>${detail.candidate_count && detail.status !== "running" ? "参考视频已找到" : phaseTitle(detail.stage)}</h2>
    <p>${detail.candidate_count ? "切换到参考视频，查看已核实的播放和互动数据。" : "任务的结果会在这里出现。"} </p>
    ${detail.candidate_count ? '<button class="secondary" data-select-view="references">查看参考视频</button>' : ""}
    </div>`;
  return `<div class="preview-layout">
    <div><div class="video-stage">${content}${detail.has_video ? '<span class="stage-tag">成片预览</span>' : ""}</div>
      <div class="video-caption"><span>${detail.has_video ? "9:16 竖屏" : "视频工作区"}</span><span>${final ? final.width + " × " + final.height + " · " + final.duration.toFixed(2) + " 秒" : ""}</span></div>
      ${detail.has_preview ? '<div class="filmstrip"><p>逐秒抽帧 · 点击查看完整画面</p><button data-image="' + assetURL(detail.run, "preview.jpg") + '"><img src="' + assetURL(detail.run, "preview.jpg") + '" alt="成片逐秒抽帧"></button></div>' : ""}
    </div>
    <aside class="detail-panel"><h3>任务概览</h3>
      <div class="result-state"><span class="dot ${escapeHTML(detail.status)}"></span>${statuses[detail.status] || detail.status}</div>
      <div class="detail-stats"><div class="detail-stat"><strong>${detail.candidate_count}</strong><span>已核实候选</span></div><div class="detail-stat"><strong>${detail.eligible_count}</strong><span>符合筛选</span></div></div>
      <div class="detail-line"><div class="detail-label">关键词</div><div class="detail-value">${escapeHTML(detail.keywords || "直接导入参考")}</div></div>
      ${source ? '<div class="detail-line"><div class="detail-label">入选参考</div><div class="detail-value">@' + escapeHTML(source.author || "TikTok") + '</div><div class="detail-label" style="margin-top:5px">' + formatCount(source.views) + ' 播放</div><a class="source-link" href="' + safeSource(source.url) + '" target="_blank" rel="noopener noreferrer">在 TikTok 打开 ↗</a></div>' : ""}
      ${detail.caption ? '<div class="detail-line"><div class="detail-label">成片标题</div><div class="detail-value">' + escapeHTML(detail.caption) + "</div></div>" : ""}
      ${detail.has_video ? '<div class="detail-file"><span>画面留白检查</span><strong>' + (detail.layout.large_flat_bands?.length ? "需要复核" : "已通过") + "</strong></div>" : ""}
      ${detail.shots ? '<div class="detail-file"><span>REA 镜头数</span><strong>' + detail.shots + " 段</strong></div>" : ""}
      ${detail.has_report ? '<div class="detail-file"><span>原始分析</span><a class="source-link" href="' + assetURL(detail.run, "rea/report.html") + '" target="_blank" rel="noopener">打开报告 ↗</a></div>' : ""}
    </aside>
  </div>`;
}

function safeSource(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" &&
      ["www.tiktok.com", "tiktok.com"].includes(url.hostname)
      ? escapeHTML(url.href)
      : "#";
  } catch {
    return "#";
  }
}

function referenceView(detail) {
  if (!detail.candidates.length)
    return '<div class="empty"><h2>还没有参考视频</h2><p>找片阶段完成后，已核实的候选会出现在这里。</p></div>';
  return `<div class="reference-summary"><div><h2>已核实 ${detail.candidate_count} 条，入选 ${detail.eligible_count} 条</h2><p>数据来自 TikTok 回源，入选条件同时检查播放和加权互动率。</p></div>
    <a class="subtle" download href="${assetURL(detail.run, "candidates.json")}">导出数据</a></div>
    <div class="table-wrap"><table><thead><tr><th>参考视频</th><th>播放</th><th>点赞</th><th>加权互动率</th><th>筛选</th><th></th></tr></thead><tbody>
    ${detail.candidates
      .map(
        (row) => `<tr>
      <td class="video-cell"><div class="candidate-caption" title="${escapeHTML(row.description)}">${escapeHTML(row.description || "TikTok 视频")}</div><div class="candidate-author">@${escapeHTML(row.author || "TikTok")}</div></td>
      <td>${formatCount(row.views)}</td><td>${formatCount(row.likes)}</td><td>${(row.weighted_engagement_rate * 100).toFixed(2)}%</td>
      <td>${row.eligible ? '<span class="eligible-pill">入选</span>' : '<span class="not-eligible">未入选</span>'}</td>
      <td><a class="open-link" href="${safeSource(row.url)}" target="_blank" rel="noopener noreferrer" aria-label="在 TikTok 打开视频">↗</a></td></tr>`,
      )
      .join("")}
    </tbody></table></div>
    ${detail.errors.length ? '<p class="generation-note" style="margin-top:14px">' + detail.errors.length + " 条候选未通过回源核实，已从表格中排除。</p>" : ""}
  `;
}

function storyboardView(detail) {
  if (!detail.has_storyboard)
    return '<div class="empty"><h2>等待镜头分析</h2><p>生成任务会自动下载入选原片，并用 REA 输出切点与分镜图。</p></div>';
  return `<section class="story-section"><div class="section-header"><h2>REA 逐镜抽帧 ${detail.shots ? "· " + detail.shots + " 段" : ""}</h2><a href="${assetURL(detail.run, "rea/report.html")}" target="_blank" rel="noopener">查看完整报告 ↗</a></div>
    <img class="storyboard-image" data-image="${assetURL(detail.run, "rea/storyboard.jpg")}" src="${assetURL(detail.run, "rea/storyboard.jpg")}" alt="REA 原片分镜"></section>
    ${
      detail.storyboard.shots?.length
        ? '<section class="story-section"><div class="section-header"><h2>复刻分镜</h2><a href="' +
          assetURL(detail.run, "flow-prompt.txt") +
          '" target="_blank">查看生成提示词 ↗</a></div>' +
          detail.storyboard.shots
            .map(
              (shot, index) =>
                '<div class="shot"><div class="shot-time">' +
                ["0–2.3 秒", "2.3–5.7 秒", "5.7–8 秒"][index] +
                "</div><div><p>" +
                escapeHTML(shot.observation) +
                '</p><p class="adaptation">' +
                escapeHTML(shot.adaptation) +
                "</p></div></div>",
            )
            .join("") +
          "</section>"
        : ""
    }
  `;
}

async function renderWorkspace() {
  const detail = state.detail;
  if (!detail) return;
  document
    .querySelectorAll("[data-view]")
    .forEach((button) =>
      button.setAttribute(
        "aria-selected",
        String(button.dataset.view === state.view),
      ),
    );
  if (state.view === "result") $("#workspace").innerHTML = videoView(detail);
  if (state.view === "references")
    $("#workspace").innerHTML = referenceView(detail);
  if (state.view === "storyboard")
    $("#workspace").innerHTML = storyboardView(detail);
  if (state.view === "logs") {
    const log = await api(
      "/api/runs/" + encodeURIComponent(detail.run) + "/log",
    );
    if (state.selected !== detail.run || state.view !== "logs") return;
    $("#workspace").innerHTML = log.text
      ? '<pre class="log-panel"></pre>'
      : '<p class="log-empty">这个历史任务没有工作台日志。新任务会在这里持续记录运行过程。</p>';
    const panel = $(".log-panel");
    if (panel) {
      panel.textContent = log.text;
      panel.scrollTop = panel.scrollHeight;
    }
  }
}

async function refresh() {
  if (state.polling) return;
  state.polling = true;
  try {
    state.runs = await api("/api/runs");
    if (!state.selected && state.runs.length) {
      const requested = new URLSearchParams(location.search).get("run");
      state.selected = state.runs.some((run) => run.run === requested)
        ? requested
        : state.runs.find((run) => run.has_video)?.run || state.runs[0].run;
    }
    renderRuns();
    if (state.selected) {
      const detail = await api(
        "/api/runs/" + encodeURIComponent(state.selected),
      );
      if (detail.run !== state.selected) return;
      const fingerprint = JSON.stringify(detail);
      if (fingerprint !== state.fingerprint) {
        state.detail = detail;
        state.fingerprint = fingerprint;
        renderDetail();
      } else if (
        state.view === "logs" &&
        ["running", "queued"].includes(detail.status)
      ) {
        await renderWorkspace();
      }
    }
  } catch (error) {
    toast(error.message);
  } finally {
    state.polling = false;
  }
}

$("#new-task").addEventListener("click", showCreate);
$("#empty-new").addEventListener("click", showCreate);
document.addEventListener("click", async (event) => {
  const run = event.target.closest("[data-run]");
  if (run) {
    try {
      await selectRun(run.dataset.run);
    } catch (error) {
      toast(error.message);
    }
  }
  const close = event.target.closest("[data-close]");
  if (close) document.getElementById(close.dataset.close).close();
  const tab = event.target.closest("[data-view],[data-select-view]");
  if (tab) {
    state.view = tab.dataset.view || tab.dataset.selectView;
    try {
      await renderWorkspace();
    } catch (error) {
      toast(error.message);
    }
  }
  const image = event.target.closest("[data-image]");
  if (image) {
    $("#large-image").src = image.dataset.image;
    $("#image-dialog").showModal();
  }
  if (event.target.closest("#resume-task")) {
    try {
      await api("/api/runs/" + encodeURIComponent(state.selected) + "/resume", {
        method: "POST",
      });
      toast("任务已加入队列");
      await refresh();
    } catch (error) {
      toast(error.message);
    }
  }
});

$("#create-form").addEventListener("change", () => {
  const generation = $("#create-form [name=mode]:checked").value === "generate";
  $("#generation-fields").hidden = !generation;
  $("#create-submit").textContent = generation ? "生成成片" : "开始找片";
});
$("#create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target));
  for (const name of ["min_views", "min_engagement", "limit"])
    data[name] = Number(data[name]);
  $("#create-submit").disabled = true;
  $("#form-error").textContent = "";
  try {
    const created = await api("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    $("#create-dialog").close();
    state.selected = created.run;
    state.fingerprint = "";
    state.view = "result";
    history.replaceState(null, "", "?run=" + encodeURIComponent(created.run));
    await refresh();
    toast("任务已加入队列");
  } catch (error) {
    $("#form-error").textContent = error.message;
  } finally {
    $("#create-submit").disabled = false;
  }
});
$("#environment-button").addEventListener("click", async () => {
  $("#environment-dialog").showModal();
  $("#environment-details").innerHTML =
    '<div class="loading">正在检查本机环境…</div>';
  try {
    const system = await api("/api/system");
    $("#environment-details").innerHTML = [
      ["Flow2API 本机服务", system.flow2api, "已就绪", "将在生成时启动"],
      ["当前 Chrome 会话", system.chrome_configured, "已配置", "待绑定"],
      ["Glance Bridge", system.glance, "已安装", "待安装"],
      ["REA-Video", system.rea, "已安装", "待安装"],
      ["FFmpeg", system.ffmpeg, "已安装", "待安装"],
      ["yt-dlp", system.ytdlp, "已安装", "待安装"],
    ]
      .map(
        ([name, ok, on, off]) =>
          '<div class="environment-row"><span>' +
          name +
          '</span><strong class="' +
          (ok ? "" : "off") +
          '">' +
          (ok ? on : off) +
          "</strong></div>",
      )
      .join("");
  } catch (error) {
    $("#environment-details").textContent = error.message;
  }
});
refresh();
setInterval(refresh, 3500);
