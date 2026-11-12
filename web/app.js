const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const finderMode = document.documentElement.dataset.edition === "finder";
const state = {
  page:
    location.pathname === "/settings"
      ? "settings"
      : new URLSearchParams(location.search).has("run") &&
          new URLSearchParams(location.search).get("view") !== "viral"
        ? "task"
        : "viral",
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
  cancelled: "已暂停",
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

async function showCreate() {
  $("#create-form").reset();
  $("#generation-fields").hidden = true;
  if (finderMode)
    $("#create-form [value=generate]").closest("label").hidden = true;
  $("#create-submit").textContent = "开始找片";
  $("#form-error").textContent = "";
  $("#create-dialog").showModal();
  try {
    const config = await api("/api/settings");
    const topic = config.topics.find((item) => item.id === config.topic);
    $("#form-direction").textContent = "当前内容方向 · " + topic.label;
    $("#create-form [name=product]").value = topic.label;
  } catch (error) {
    $("#form-error").textContent = error.message;
  }
}

function renderRuns() {
  $("#run-count").textContent = state.runs.length;
  $("#run-list").innerHTML =
    state.runs
      .map(
        (run) => `
    <button class="run-item ${state.page === "task" && run.run === state.selected ? "active" : ""}" data-run="${escapeHTML(run.run)}"
      aria-current="${state.page === "task" && run.run === state.selected ? "page" : "false"}">
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
  state.page = "task";
  if (finderMode) state.view = "references";
  $("#viral-nav").classList.remove("active");
  $("#settings-nav").classList.remove("active");
  $("#pipeline").hidden = false;
  $(".workspace-tabs").hidden = false;
  state.selected = name;
  state.fingerprint = "";
  const detail = await api("/api/runs/" + encodeURIComponent(name));
  if (state.page !== "task" || state.selected !== name) return;
  state.detail = detail;
  history.replaceState(null, "", "/?run=" + encodeURIComponent(name));
  renderRuns();
  renderDetail();
}

function renderDetail() {
  if (state.page !== "task") return;
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
  if (finderMode) {
    $("#pipeline").hidden = true;
    document
      .querySelectorAll('[data-view="result"],[data-view="storyboard"]')
      .forEach((element) => (element.hidden = true));
  }
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
    if (state.page === "viral") {
      await loadVideos();
      return;
    }
    if (state.page === "settings") {
      await refreshUpdateStatus();
      return;
    }
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

function openVideoLibrary() {
  state.page = "viral";
  state.videoFingerprint = "";
  $("#viral-nav").classList.add("active");
  $("#settings-nav").classList.remove("active");
  $("#task-title").textContent = "爆款视频列表";
  $("#task-subtitle").textContent = finderMode
    ? "搜索商品、筛选原片，复制链接到 YouArt Desktop 复刻。"
    : "搜索商品，找到相关爆款，选中一条开始复刻。";
  $("#breadcrumb").textContent = "爆款视频";
  $("#task-actions").innerHTML = finderMode
    ? '<button class="secondary" data-youart-guide>去 YouArt 复刻 · 使用指南</button>'
    : '<button class="secondary" id="video-settings">自动更新设置</button>';
  $("#pipeline").hidden = true;
  $("#task-notice").hidden = true;
  $(".workspace-tabs").hidden = true;
  history.replaceState(null, "", "/videos");
  $("#workspace").innerHTML = `
    <form id="video-discovery-form" class="video-discovery-form">
      <input id="video-query" type="search" aria-label="搜索商品视频" maxlength="200" placeholder="搜索商品，例如圣诞挂件、圣诞树、圣诞晚礼服" autocomplete="off">
      <button class="primary" type="submit">搜索视频</button>
    </form>
    <div class="search-suggestions"><span>试试</span><button data-product-search="圣诞挂件">圣诞挂件</button><button data-product-search="圣诞树">圣诞树</button><button data-product-search="圣诞晚礼服">圣诞晚礼服</button></div>
    <p id="discovery-status" class="discovery-status" role="status"></p>
    <details class="library-filter-options"><summary>筛选与排序</summary>
    <div class="video-filters">
      <label class="field"><span>最低播放量</span><input id="video-min-views" type="number" value="0" min="0" step="10000" data-video-filter></label>
      <label class="field"><span>最低互动率 %</span><input id="video-min-rate" type="number" value="0" min="0" step="0.5" data-video-filter></label>
      <label class="field"><span>排序</span><select id="video-sort" data-video-filter><option value="views">播放量从高到低</option><option value="engagement">互动率从高到低</option></select></label>
    </div>
    </details>
    <p id="video-library-count" class="library-count">正在汇总已核实视频…</p>
    <div id="video-library-results"></div>`;
  $("#video-query").value = state.searchQuery || "";
  renderRuns();
  loadVideos().catch(showLibraryError);
}

function showLibraryError(error) {
  const results = $("#video-library-results");
  if (!results) return;
  $("#video-library-count").textContent = "视频列表加载失败";
  results.innerHTML =
    '<div class="notice" role="alert"><p></p><button class="secondary" id="retry-video-library">重新加载列表</button></div>';
  results.querySelector("p").textContent = error.message;
  $("#retry-video-library").addEventListener("click", () =>
    loadVideos().catch(showLibraryError),
  );
}

async function loadVideos() {
  if (state.page !== "viral" || !$("#video-query")) return;
  const query = new URLSearchParams({
    q: state.searchQuery || "",
    min_views: Math.max(0, Number($("#video-min-views").value) || 0),
    min_engagement: Math.max(0, Number($("#video-min-rate").value) || 0),
    sort: $("#video-sort").value,
  });
  const request = (state.videoRequest || 0) + 1;
  state.videoRequest = request;
  const result = await api("/api/videos?" + query);
  if (state.page !== "viral" || request !== state.videoRequest) return;
  const job = state.runs.find((item) => item.run === state.searchRun);
  const searchStatus = job?.status || state.searchStatus;
  const searching =
    state.searchRun && ["queued", "running"].includes(searchStatus);
  const status = $("#discovery-status");
  if (status)
    status.textContent =
      searchStatus === "queued"
        ? "搜索已加入队列，即将查找“" + state.searchQuery + "”相关视频…"
        : searching
          ? "正在联网寻找并核实“" +
            state.searchQuery +
            "”相关视频，结果会自动出现…"
          : state.searchRun && ["failed", "interrupted"].includes(searchStatus)
            ? "搜索未完成，可在左侧打开搜索任务查看原因。"
            : state.searchQuery
              ? "“" + state.searchQuery + "”的相关视频"
              : finderMode
                ? "复制链接，在 YouArt Desktop 的“爆款复刻”里粘贴，并说明要改成什么。"
                : "点击“复刻”会使用当前 Flow 账号生成视频。";
  const fingerprint = JSON.stringify({ result, searching, searchStatus });
  if (fingerprint === state.videoFingerprint) return;
  state.videoFingerprint = fingerprint;
  $("#video-library-count").textContent =
    result.total === result.verified_total
      ? "全部 " + result.verified_total + " 条已收录视频"
      : "已收录 " +
        result.verified_total +
        " 条，筛选后 " +
        result.total +
        " 条";
  $("#video-library-results").innerHTML = result.videos.length
    ? `
    <div class="table-wrap"><table><thead><tr><th>视频 / 作者</th><th>播放</th><th>点赞</th><th>加权互动率</th><th>数据更新</th><th>${finderMode ? "去复刻" : "复刻"}</th></tr></thead><tbody>
    ${result.videos
      .map(
        (row) => `<tr>
      <td class="video-cell"><a class="candidate-caption library-video-title" href="${safeSource(row.url)}" target="_blank" rel="noopener noreferrer" title="${escapeHTML(row.description)}">${escapeHTML(row.description || "TikTok 视频")}</a><div class="candidate-author">@${escapeHTML(row.author || "TikTok")}</div></td>
      <td class="video-views">${formatCount(row.views)}</td><td>${formatCount(row.likes)}</td><td>${((row.weighted_engagement_rate || 0) * 100).toFixed(2)}%</td>
      <td class="video-updated">${row.checked_at ? shortDate(row.checked_at) : "—"}</td>
      <td>${finderMode ? '<button class="primary copy-video" data-copy-video="' + escapeHTML(row.url) + '">复制链接</button>' : '<button class="primary remake-video" title="生成一条视频，会使用 Flow 积分" data-remake-video="' + escapeHTML(row.id) + '">复刻</button>'}</td>
    </tr>`,
      )
      .join("")}</tbody></table></div>`
    : searching
      ? '<div class="empty"><h2>正在寻找相关视频</h2><p>正在核实 TikTok 原片的播放和互动数据。</p></div>'
      : '<div class="empty"><h2>暂时没有相关视频</h2><p>换一个商品名称，或调整筛选条件再试。</p></div>';
}

const readableTime = (value) =>
  value
    ? new Date(typeof value === "number" ? value * 1000 : value).toLocaleString(
        "zh-CN",
        {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        },
      )
    : "尚未完成首轮";

async function refreshUpdateStatus() {
  if (!["viral", "settings"].includes(state.page)) return;
  const config = await api("/api/settings");
  const running = ["queued", "running"].includes(config.current_job?.status);
  const failed = ["failed", "interrupted"].includes(config.current_job?.status);
  const topic =
    config.topics.find((item) => item.id === config.topic)?.label || "";
  const bar = $("#auto-update-bar");
  if (bar)
    bar.innerHTML = `
    <div><strong>${running ? "正在更新列表" : failed ? "上次更新失败，请查看任务" : config.enabled ? "自动更新已开启" : "自动更新已暂停"}</strong>
    <span>${escapeHTML(topic)} · 每 ${config.interval_hours} 小时 · 最近完成 ${readableTime(config.last_success)}</span></div>
    <div class="actions"><button class="secondary" id="update-videos-now" ${running ? "disabled" : ""}>${running ? "正在更新" : "立即更新"}</button><button class="subtle" id="video-settings">设置</button></div>`;
  const status = $("#settings-update-status");
  if (status)
    status.innerHTML = `
    <div class="environment-row"><span>当前状态</span><strong>${running ? "正在找新视频并刷新数据" : failed ? "上次更新失败" : config.enabled ? "自动更新已开启" : "已暂停"}</strong></div>
    <div class="environment-row"><span>最近完成</span><span>${readableTime(config.last_success)}</span></div>
    <div class="environment-row"><span>下次计划</span><span>${config.enabled ? readableTime(config.next_run) : "已暂停"}</span></div>
    ${config.current_job ? '<div class="environment-row"><span>最近更新任务</span><button class="source-task" data-run="' + escapeHTML(config.current_job.run) + '">查看进度与日志</button></div>' : ""}`;
  return config;
}

async function openSettings() {
  state.page = "settings";
  $("#settings-nav").classList.add("active");
  $("#viral-nav").classList.remove("active");
  $("#task-title").textContent = "设置";
  $("#task-subtitle").textContent =
    "选好内容方向，工作台会自动维护你的视频列表。";
  $("#breadcrumb").textContent = "设置";
  $("#task-actions").innerHTML = "";
  $("#pipeline").hidden = true;
  $("#task-notice").hidden = true;
  $(".workspace-tabs").hidden = true;
  history.replaceState(null, "", "/settings");
  $("#workspace").innerHTML = '<div class="loading">正在读取设置…</div>';
  renderRuns();
  try {
    const config = await api("/api/settings");
    if (state.page !== "settings") return;
    $("#workspace").innerHTML = `
      <div class="settings-layout"><form id="settings-form" class="settings-card">
        <h2>视频列表自动更新</h2>
        <label class="toggle-row"><span><strong>自动维护视频列表</strong><small>发现新视频，并刷新已收录视频的播放和互动数据</small></span><input name="enabled" type="checkbox" ${config.enabled ? "checked" : ""}></label>
        <label class="field"><span>内容方向</span><select name="topic">${config.topics.map((item) => '<option value="' + item.id + '" ' + (config.topic === item.id ? "selected" : "") + ">" + item.label + "</option>").join("")}</select></label>
        <p class="settings-hint">系统会按这个方向使用预设关键词，你可以直接浏览结果。</p>
        <label class="field"><span>更新频率</span><select name="interval_hours">${[1, 6, 12, 24].map((value) => '<option value="' + value + '" ' + (config.interval_hours === value ? "selected" : "") + ">每 " + value + " 小时</option>").join("")}</select></label>
        <p class="settings-hint">更新只找片和核实数据。视频生成由你在新建任务时启动。</p>
        <div id="settings-error" class="form-error" role="alert"></div>
        <div class="dialog-footer"><button class="primary" type="submit">保存设置</button></div>
      </form>
      <section class="settings-card"><h2>更新状态</h2><div id="settings-update-status"></div>
        <p class="settings-hint">工作台服务运行时，关闭浏览器页面也会继续更新。电脑唤醒后会补跑到期任务。</p>
        <button class="secondary" id="settings-update-now">立即更新一次</button>
        <h2 class="settings-subheading">生成环境</h2>
        <p class="settings-hint">在当前 Chrome 登录 Flow 后，点击连接，视频生成会复用这个账号。</p>
        <button class="secondary" id="connect-current-chrome">连接当前 Chrome</button>
        <p id="chrome-connect-status" class="settings-hint"></p>
      </section></div>`;
    if (finderMode) {
      $("#connect-current-chrome")?.remove();
      $("#chrome-connect-status")?.remove();
      document
        .querySelectorAll(".settings-subheading")
        .forEach((element) => (element.hidden = true));
      const help = [...document.querySelectorAll(".settings-hint")].find(
        (element) => element.textContent.includes("当前 Chrome 登录 Flow"),
      );
      if (help)
        help.textContent =
          "找到原片后，复制链接到 YouArt Desktop 的“爆款复刻”入口，再说明商品和修改目标。";
    }
    $("#settings-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      const button = event.target.querySelector("button[type=submit]");
      button.disabled = true;
      $("#settings-error").textContent = "";
      try {
        await api("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            enabled: form.has("enabled"),
            topic: form.get("topic"),
            interval_hours: Number(form.get("interval_hours")),
          }),
        });
        await refreshUpdateStatus();
        toast("设置已保存");
      } catch (error) {
        $("#settings-error").textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
    await refreshUpdateStatus();
  } catch (error) {
    toast(error.message);
  }
}

$("#settings-nav").addEventListener("click", openSettings);
document.addEventListener("click", async (event) => {
  if (event.target.closest("#video-settings")) openSettings();
  const connect = event.target.closest("#connect-current-chrome");
  if (connect) {
    connect.disabled = true;
    $("#chrome-connect-status").textContent = "正在连接当前 Chrome…";
    try {
      await api("/api/chrome/connect", { method: "POST" });
      $("#chrome-connect-status").textContent = "当前 Chrome 已连接";
    } catch (error) {
      $("#chrome-connect-status").textContent = error.message;
    } finally {
      connect.disabled = false;
    }
  }
  const button = event.target.closest(
    "#update-videos-now,#settings-update-now",
  );
  if (button) {
    button.disabled = true;
    try {
      await api("/api/videos/update", { method: "POST" });
      await refreshUpdateStatus();
      toast("更新已加入队列");
    } catch (error) {
      button.disabled = false;
      toast(error.message);
    }
  }
});

async function discoverProduct(query) {
  query = query.trim();
  if (!query) {
    state.searchQuery = "";
    state.searchRun = null;
    state.searchStatus = null;
    state.videoFingerprint = "";
    await loadVideos();
    return;
  }
  const created = await api("/api/videos/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  state.searchQuery = query;
  state.searchRun = created.run;
  state.searchStatus = created.status;
  state.videoFingerprint = "";
  await refresh();
}

document.addEventListener("submit", async (event) => {
  if (event.target.id !== "video-discovery-form") return;
  event.preventDefault();
  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    await discoverProduct($("#video-query").value);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
});
document.addEventListener("click", async (event) => {
  const suggestion = event.target.closest("[data-product-search]");
  if (suggestion) {
    $("#video-query").value = suggestion.dataset.productSearch;
    try {
      await discoverProduct(suggestion.dataset.productSearch);
    } catch (error) {
      toast(error.message);
    }
  }
  const copy = event.target.closest("[data-copy-video]");
  if (copy) {
    try {
      const field = document.createElement("textarea");
      field.value = copy.dataset.copyVideo;
      field.style.cssText = "position:fixed;left:-9999px;top:0";
      document.body.appendChild(field);
      field.select();
      const copied = document.execCommand("copy");
      field.remove();
      if (!copied) await navigator.clipboard.writeText(copy.dataset.copyVideo);
      toast("视频链接已复制");
      $("#youart-guide-dialog").showModal();
    } catch (error) {
      toast("复制未完成，请打开原片后复制地址");
    }
  }
  const remake = event.target.closest("[data-remake-video]");
  if (remake) {
    remake.disabled = true;
    remake.dataset.requestId ||= crypto.randomUUID();
    try {
      const created = await api(
        "/api/videos/" +
          encodeURIComponent(remake.dataset.remakeVideo) +
          "/remake",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            product: state.searchQuery || "",
            request_id: remake.dataset.requestId,
          }),
        },
      );
      state.view = "result";
      await selectRun(created.run);
      toast("已开始复刻选中的视频");
    } catch (error) {
      remake.disabled = false;
      toast(error.message);
    }
  }
});

$("#viral-nav").addEventListener("click", openVideoLibrary);
document.addEventListener("input", (event) => {
  if (!event.target.matches("[data-video-filter]")) return;
  clearTimeout(state.videoFilterTimer);
  state.videoFilterTimer = setTimeout(
    () => loadVideos().catch((error) => toast(error.message)),
    300,
  );
});

document.addEventListener("click", async (event) => {
  if (event.target.closest("[data-youart-guide]"))
    $("#youart-guide-dialog").showModal();
  if (event.target.closest("#copy-youart-prompt")) {
    const input = $("#youart-example-prompt");
    input.select();
    try {
      if (!document.execCommand("copy"))
        await navigator.clipboard.writeText(input.value);
      toast("示例提示词已复制");
    } catch {
      toast("请选中示例提示词后复制");
    }
  }
});

if (finderMode) {
  document.title = "TikTok 爆款视频查找器";
  $("#new-task").innerHTML = '<span aria-hidden="true">＋</span>找新视频';
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
    state.page = "task";
    $("#settings-nav").classList.remove("active");
    $("#viral-nav").classList.remove("active");
    $("#pipeline").hidden = false;
    $(".workspace-tabs").hidden = false;
    state.selected = created.run;
    state.fingerprint = "";
    state.view = finderMode ? "references" : "result";
    history.replaceState(null, "", "/?run=" + encodeURIComponent(created.run));
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
    $(".environment-help").textContent = finderMode
      ? "找到视频后，复制链接到 YouArt Desktop 的爆款复刻入口。"
      : "在“设置”中连接当前 Chrome，并保持 Chrome 与 Glance Bridge 连接。";
    $("#environment-details").innerHTML = [
      ["Flow2API 本机服务", system.flow2api, "已就绪", "将在生成时启动"],
      ["当前 Chrome 会话", system.chrome_configured, "已配置", "待绑定"],
      ["Glance Bridge", system.glance, "已安装", "待安装"],
      ["REA-Video", system.rea, "已安装", "待安装"],
      ["FFmpeg", system.ffmpeg, "已安装", "待安装"],
      ["yt-dlp", system.ytdlp, "已安装", "待安装"],
    ]
      .filter(([name]) => !finderMode || name === "yt-dlp")
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
if (state.page === "viral") openVideoLibrary();
if (state.page === "settings") openSettings();
refresh();
setInterval(refresh, 3500);
