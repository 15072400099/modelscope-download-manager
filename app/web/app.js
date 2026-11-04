/* 模搭 · 模型下载管理器 —— 前端逻辑（原生 JS，无需构建） */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const app = {
  view: "search",
  settings: null,
  tasks: [],
  library: [],
  search: { q: "", sort: "downloads", page: 1, items: [], hasMore: false, busy: false },
  modal: { repoId: "", displayName: "", revision: "master", files: [], selected: new Set(), busy: false },
  taskNodes: new Map(),
  knownStatus: new Map(),
};

/* ------------------------------------------------------------------ */
/* 工具函数                                                            */
/* ------------------------------------------------------------------ */
function fmtBytes(n) {
  n = Number(n) || 0;
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${units[i]}`;
}

function fmtSpeed(n) {
  if (!n || n <= 0) return "—";
  return fmtBytes(n) + "/s";
}

function fmtEta(seconds) {
  if (!seconds || seconds <= 0) return "";
  if (seconds < 60) return `约 ${Math.ceil(seconds)} 秒`;
  if (seconds < 3600) return `约 ${Math.ceil(seconds / 60)} 分钟`;
  return `约 ${(seconds / 3600).toFixed(1)} 小时`;
}

function fmtCount(n) {
  n = Number(n) || 0;
  if (n >= 1e8) return (n / 1e8).toFixed(1) + " 亿";
  if (n >= 1e4) return (n / 1e4).toFixed(1) + " 万";
  return String(n);
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function toast(message, type = "") {
  const box = document.createElement("div");
  box.className = "toast" + (type ? " " + type : "");
  box.textContent = message;
  $("#toasts").appendChild(box);
  setTimeout(() => {
    box.style.transition = "opacity .3s";
    box.style.opacity = "0";
    setTimeout(() => box.remove(), 320);
  }, type === "err" ? 6000 : 3200);
}

async function api(path, options = {}) {
  const config = { headers: { "Content-Type": "application/json" }, ...options };
  let data;
  try {
    const response = await fetch(path, config);
    data = await response.json();
  } catch (err) {
    return { ok: false, error: "连不上本地服务，程序可能已经关闭。请重新双击「启动下载管理器」。" };
  }
  return data;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("路径已复制", "ok");
  } catch (err) {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    toast("路径已复制", "ok");
  }
}

/* ------------------------------------------------------------------ */
/* 视图切换                                                            */
/* ------------------------------------------------------------------ */
function switchView(name) {
  if (!["search", "tasks", "library", "settings"].includes(name)) name = "search";
  app.view = name;
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === name));
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === "view-" + name));
  if (name === "library") loadLibrary(true);
  if (name === "settings") loadSettings();
}

/* ------------------------------------------------------------------ */
/* 搜索                                                                */
/* ------------------------------------------------------------------ */
function isRepoIdLike(text) {
  const value = (text || "").trim();
  if (!value) return false;
  if (value.toLowerCase().includes("modelscope.cn")) return true;
  return /^[\w.\-]+\/[\w.\-]+$/.test(value);
}

async function doSearch(reset = true) {
  const raw = $("#search-input").value.trim();
  if (!raw) { toast("先输入想找的模型名字吧"); return; }

  if (isRepoIdLike(raw)) {
    // 直接给模型 ID 或页面链接：跳过搜索，直接打开下载窗口
    const repoId = normalizeRepoId(raw);
    if (!repoId) { toast("这个链接认不出来，请用「作者/模型名」的格式", "err"); return; }
    openDownloadDialog(repoId, repoId.split("/")[1]);
    return;
  }

  if (app.search.busy) return;
  app.search.busy = true;
  if (reset) {
    app.search.page = 1;
    app.search.q = raw;
    app.search.items = [];
    $("#search-results").innerHTML = `<div class="card muted">正在搜索…</div>`;
  }
  app.search.sort = $("#search-sort").value;
  const data = await api(`/api/search?q=${encodeURIComponent(app.search.q)}&page=${app.search.page}&size=12&sort=${app.search.sort}`);
  app.search.busy = false;

  const hint = $("#search-hint");
  if (!data.ok) {
    hint.classList.remove("hidden");
    hint.textContent = data.error || "搜索失败，请稍后再试";
    $("#search-results").innerHTML = "";
    $("#load-more").classList.add("hidden");
    return;
  }
  hint.classList.add("hidden");
  app.search.items = app.search.items.concat(data.items || []);
  app.search.hasMore = !!data.has_more;
  setResultHead(`搜索「${app.search.q}」`, `${app.search.items.length} 个结果`);
  renderSearch();
}

async function loadFeatured() {
  if (app.search.items.length) return;
  const data = await api(`/api/search?q=&size=12&sort=downloads`);
  if (!data.ok) return;
  app.search.items = data.items || [];
  app.search.page = 1;
  app.search.hasMore = false;
  if (!app.search.items.length) return;
  setResultHead("热门模型", "看看大家在下载什么");
  renderSearch();
}

function setResultHead(title, sub) {
  const head = $("#result-head");
  if (!title) { head.classList.add("hidden"); return; }
  head.classList.remove("hidden");
  $("#result-title").textContent = title;
  $("#result-sub").textContent = sub || "";
}

function normalizeRepoId(text) {
  let value = (text || "").trim();
  const marker = value.toLowerCase().indexOf("/models/");
  if (marker >= 0) value = value.slice(marker + 8);
  value = value.split("?")[0].split("#")[0].replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  const parts = value.split("/").filter(Boolean);
  return parts.length >= 2 ? `${parts[0]}/${parts[1]}` : "";
}

function renderSearch() {
  const box = $("#search-results");
  if (!app.search.items.length) {
    box.innerHTML = `<div class="card"><h3>没搜到结果</h3><p class="muted small" style="margin-top:6px">换个关键词试试，或者直接粘贴模型页面链接。</p></div>`;
    $("#load-more").classList.add("hidden");
    return;
  }
  box.innerHTML = app.search.items.map((item, index) => `
    <article class="card model-card">
      <h3>${esc(item.display_name || item.repo_id)}</h3>
      <div class="repo-id">${esc(item.repo_id)}</div>
      <p class="desc">${esc(item.description || "这个模型没有写简介。")}</p>
      <div class="meta-row">
        <span>下载 <b>${fmtCount(item.downloads)}</b></span>
        <span>大小 <b>${item.file_size ? fmtBytes(item.file_size) : "未知"}</b></span>
        ${item.license ? `<span>许可 <b>${esc(item.license)}</b></span>` : ""}
      </div>
      ${(item.tasks && item.tasks.length) ? `<div class="taglist">${item.tasks.slice(0, 3).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
      <div class="card-actions">
        <button class="btn primary small" data-download="${index}">下载</button>
        <button class="btn ghost small" data-detail="${index}">看文件</button>
      </div>
    </article>
  `).join("");
  $("#load-more").classList.toggle("hidden", !app.search.hasMore);
}

/* ------------------------------------------------------------------ */
/* 下载弹窗                                                            */
/* ------------------------------------------------------------------ */
async function openDownloadDialog(repoId, displayName) {
  app.modal = { repoId, displayName: displayName || repoId, revision: "master", files: [], selected: new Set(), busy: true };
  $("#modal").classList.remove("hidden");
  $("#modal-title").textContent = displayName || repoId;
  $("#modal-sub").textContent = repoId;
  $("#modal-files").innerHTML = `<div class="muted small pad">正在读取文件列表…</div>`;
  $("#modal-summary").textContent = "正在计算大小…";
  $("#modal-start").disabled = true;

  const revisions = await api(`/api/revisions?repo_id=${encodeURIComponent(repoId)}`);
  const select = $("#modal-revision");
  select.innerHTML = (revisions.revisions || ["master"])
    .map((rev) => `<option value="${esc(rev)}">${esc(rev)}</option>`).join("");
  app.modal.revision = select.value || "master";

  await loadModalFiles();
}

async function loadModalFiles() {
  const { repoId, revision } = app.modal;
  $("#modal-files").innerHTML = `<div class="muted small pad">正在读取文件列表…</div>`;
  const data = await api(`/api/files?repo_id=${encodeURIComponent(repoId)}&revision=${encodeURIComponent(revision)}`);
  if (!data.ok) {
    $("#modal-files").innerHTML = `<div class="muted small pad">${esc(data.error || "读取失败")}</div>`;
    $("#modal-summary").textContent = "";
    return;
  }
  app.modal.files = data.files || [];
  app.modal.selected = new Set(app.modal.files.map((f) => f.path));
  app.modal.busy = false;
  $("#modal-start").disabled = false;
  renderModalFiles();
}

function renderModalFiles() {
  const { files, selected } = app.modal;
  if (!files.length) {
    $("#modal-files").innerHTML = `<div class="muted small pad">这个版本里没有文件</div>`;
    $("#modal-summary").textContent = "";
    return;
  }
  $("#modal-files").innerHTML = files.map((file) => `
    <label class="file-row">
      <input type="checkbox" data-path="${esc(file.path)}" ${selected.has(file.path) ? "checked" : ""}>
      <span class="fname">${esc(file.path)}</span>
      <span class="fsize">${fmtBytes(file.size)}</span>
    </label>
  `).join("");
  $("#modal-file-count").textContent = `(共 ${files.length} 个)`;
  updateModalSummary();
}

function updateModalSummary() {
  const { files, selected } = app.modal;
  let size = 0;
  let count = 0;
  files.forEach((file) => {
    if (selected.has(file.path)) { size += Number(file.size) || 0; count += 1; }
  });
  $("#modal-summary").innerHTML = `已选 <b>${count}</b> / ${files.length} 个文件，合计 <b>${fmtBytes(size)}</b>`;
  $("#modal-start").disabled = count === 0 || app.modal.busy;
}

function pickFiles(kind) {
  const { files } = app.modal;
  if (kind === "all") app.modal.selected = new Set(files.map((f) => f.path));
  else if (kind === "none") app.modal.selected = new Set();
  else app.modal.selected = new Set(files.filter((f) => f.path.toLowerCase().endsWith("." + kind)).map((f) => f.path));
  renderModalFiles();
}

async function startDownload() {
  const { repoId, revision, files, selected, displayName } = app.modal;
  if (!selected.size) { toast("至少选一个文件"); return; }
  const payload = {
    repo_id: repoId,
    revision,
    display_name: displayName,
    files: files.filter((f) => selected.has(f.path)).map((f) => ({ path: f.path, size: f.size })),
  };
  $("#modal-start").disabled = true;
  const data = await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
  $("#modal-start").disabled = false;
  if (!data.ok) { toast(data.error || "创建任务失败", "err"); return; }
  $("#modal").classList.add("hidden");
  toast("已经加入下载队列", "ok");
  switchView("tasks");
  pollState();
}

/* ------------------------------------------------------------------ */
/* 任务列表                                                            */
/* ------------------------------------------------------------------ */
function buildTaskNode(task) {
  const node = document.createElement("article");
  node.className = "card task";
  node.dataset.id = task.id;
  node.innerHTML = `
    <div class="task-top">
      <div>
        <div class="task-title">
          <h3></h3>
          <span class="status"></span>
        </div>
        <div class="repo-id" style="margin-top:4px"></div>
      </div>
      <div class="muted small target"></div>
    </div>
    <div class="progress-wrap">
      <div class="progress"><div class="progress-bar"></div></div>
      <div class="progress-line">
        <span class="stat-left"></span>
        <span class="stat-right"></span>
      </div>
    </div>
    <div class="task-error hidden"></div>
    <div class="task-actions"></div>
  `;
  node._refs = {
    title: $("h3", node),
    status: $(".status", node),
    repo: $(".repo-id", node),
    target: $(".target", node),
    bar: $(".progress-bar", node),
    left: $(".stat-left", node),
    right: $(".stat-right", node),
    error: $(".task-error", node),
    actions: $(".task-actions", node),
  };
  return node;
}

function actionButton(label, action, style = "ghost") {
  return `<button class="btn ${style} small" data-action="${action}">${label}</button>`;
}

function updateTaskNode(node, task) {
  const refs = node._refs;
  refs.title.textContent = task.display_name || task.repo_id;
  refs.repo.textContent = `${task.repo_id} @ ${task.revision || "master"}`;
  refs.target.textContent = task.local_dir || "";

  const statusKey = task.status === "running" || task.status === "queued" ? "running"
    : task.status === "done" ? "done"
    : task.status === "error" ? "error"
    : task.status === "paused" || task.status === "pausing" ? "paused" : "";
  refs.status.className = "status " + statusKey;
  refs.status.textContent = task.status_text || task.status;

  const percent = Math.max(0, Math.min(100, Number(task.percent) || 0));
  refs.bar.style.width = percent + "%";
  refs.bar.className = "progress-bar " + statusKey;

  const done = fmtBytes(task.bytes_done);
  const total = task.bytes_total ? fmtBytes(task.bytes_total) : "未知大小";
  const filesText = task.total_files ? `${task.files_done || 0}/${task.total_files} 个文件` : "";
  refs.left.innerHTML = `<b>${percent.toFixed(1)}%</b> · ${done} / ${total}${filesText ? " · " + filesText : ""}`;
  const parts = [];
  if (task.speed > 0) parts.push(fmtSpeed(task.speed));
  if (task.eta > 0) parts.push(fmtEta(task.eta));
  if (task.status === "done") parts.push(fmtBytes(task.bytes_total || task.bytes_done));
  refs.right.textContent = parts.join(" · ");

  if (task.error) {
    refs.error.classList.remove("hidden");
    refs.error.textContent = task.error;
  } else {
    refs.error.classList.add("hidden");
  }

  const actions = [];
  if (task.status === "running" || task.status === "queued") actions.push(actionButton("暂停", "pause"));
  if (task.status === "paused" || task.status === "pausing") actions.push(actionButton("继续", "resume", "primary"));
  if (task.status === "error") actions.push(actionButton("重试", "retry", "primary"));
  if (task.status === "done") actions.push(actionButton("打开文件夹", "open"));
  if (task.status !== "done") actions.push(actionButton("取消", "cancel"));
  actions.push(actionButton("复制路径", "copy"));
  actions.push(actionButton("删除记录", "remove", "danger"));
  const html = actions.join("");
  if (refs.actions._html !== html) {
    refs.actions.innerHTML = html;
    refs.actions._html = html;
  }
}

function renderTasks(tasks) {
  const list = $("#task-list");
  const seen = new Set();
  tasks.forEach((task, index) => {
    seen.add(task.id);
    let node = app.taskNodes.get(task.id);
    if (!node) {
      node = buildTaskNode(task);
      app.taskNodes.set(task.id, node);
    }
    updateTaskNode(node, task);
    if (list.children[index] !== node) {
      list.insertBefore(node, list.children[index] || null);
    }
  });
  app.taskNodes.forEach((node, id) => {
    if (!seen.has(id)) { node.remove(); app.taskNodes.delete(id); }
  });
  $("#tasks-empty").classList.toggle("hidden", tasks.length > 0);
}

async function handleTaskAction(taskId, action, task) {
  if (action === "open") { api("/api/open", { method: "POST", body: JSON.stringify({ path: task.local_dir }) }); return; }
  if (action === "copy") { copyText(task.local_dir); return; }
  if (action === "remove") {
    if (!confirm("只是从列表里移除，已经下载的文件不会被删除。继续吗？")) return;
    await api(`/api/tasks/${taskId}`, { method: "DELETE" });
    pollState();
    return;
  }
  const endpoints = { pause: "pause", resume: "resume", cancel: "cancel", retry: "retry" };
  const name = endpoints[action];
  if (!name) return;
  const data = await api(`/api/tasks/${taskId}/${name}`, { method: "POST" });
  if (!data.ok) toast(data.error || "操作失败", "err");
  pollState();
}

/* ------------------------------------------------------------------ */
/* 本地模型库                                                          */
/* ------------------------------------------------------------------ */
async function loadLibrary(force = false) {
  const data = await api(`/api/library${force ? "?force=1" : ""}`);
  if (!data.ok) return;
  app.library = data.items || [];
  renderLibrary(data.total_size || 0);
}

function taskForPath(path) {
  if (!path) return null;
  const target = String(path).toLowerCase();
  return app.tasks.find((task) => task.local_dir && String(task.local_dir).toLowerCase() === target) || null;
}

function renderLibrary(totalSize) {
  const list = $("#library-list");
  $("#library-empty").classList.toggle("hidden", app.library.length > 0);
  const downloading = app.library.filter((item) => {
    const task = taskForPath(item.path);
    return task && ["running"].includes(task.status);
  }).length;
  $("#library-total").textContent = app.library.length
    ? `· ${app.library.length} 个 · 共 ${fmtBytes(totalSize)}` + (downloading ? ` · ${downloading} 个下载中` : "")
    : "";

  list.innerHTML = app.library.map((item, index) => {
    const task = taskForPath(item.path);
    let badge;
    if (task && task.status === "running") {
      badge = `<span class="status running">下载中 ${task.percent}%</span>`;
    } else if (task && task.status === "queued") {
      badge = `<span class="status running">排队中 ${task.percent}%</span>`;
    } else if (task && task.status === "error") {
      badge = `<span class="status error">出错</span>`;
    } else if (task && task.status === "paused") {
      badge = `<span class="status paused">已暂停 ${task.percent}%</span>`;
    } else if (item.complete) {
      badge = `<span class="status done">已完成</span>`;
    } else {
      badge = `<span class="status paused">未完成</span>`;
    }
    const partialHint = item.partial_size
      ? `<span>其中未完成 <b>${fmtBytes(item.partial_size)}</b></span>`
      : "";
    return `
    <article class="card">
      <div class="lib-item">
        <div class="lib-main">
          <div class="task-title">
            <h3>${esc(item.repo_id || item.name)}</h3>
            ${badge}
          </div>
          <div class="meta-row" style="margin-top:6px">
            <span>占用 <b>${fmtBytes(item.size)}</b></span>
            <span>完整文件 <b>${item.files}</b></span>
            <span>版本 <b>${esc(item.revision || "master")}</b></span>
            ${partialHint}
          </div>
          <div class="lib-path">${esc(item.path)}</div>
        </div>
        <div class="row-gap">
          <button class="btn ghost small" data-lib-open="${index}">打开</button>
          <button class="btn ghost small" data-lib-copy="${index}">复制路径</button>
          <button class="btn danger ghost small" data-lib-del="${index}">删除</button>
        </div>
      </div>
    </article>`;
  }).join("");
}

/* ------------------------------------------------------------------ */
/* 设置                                                                */
/* ------------------------------------------------------------------ */
async function loadSettings() {
  const data = await api("/api/settings");
  if (!data.ok) return;
  app.settings = data.settings;
  const settings = data.settings;
  $("#set-dir").value = settings.download_dir;
  $("#set-proxy").value = settings.proxy || "";
  $("#set-parallel").value = settings.max_parallel_models;
  $("#set-files").value = settings.files_per_model;
  $("#set-parallel-val").textContent = settings.max_parallel_models;
  $("#set-files-val").textContent = settings.files_per_model;
  $("#pill-saved").textContent = "保存到 " + shortenPath(settings.download_dir);
  $("#status-url").textContent = location.origin;
}

function shortenPath(path) {
  const parts = String(path || "").split(/[\\/]/).filter(Boolean);
  if (parts.length <= 2) return path;
  return parts[parts.length - 2] + "/" + parts[parts.length - 1];
}

async function saveSettings() {
  const payload = {
    download_dir: $("#set-dir").value.trim(),
    proxy: $("#set-proxy").value.trim(),
    max_parallel_models: Number($("#set-parallel").value),
    files_per_model: Number($("#set-files").value),
  };
  const data = await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
  if (!data.ok) { toast(data.error || "保存失败", "err"); return; }
  app.settings = data.settings;
  $("#set-tip").textContent = "已保存";
  $("#pill-saved").textContent = "保存到 " + shortenPath(data.settings.download_dir);
  toast("设置已保存", "ok");
  setTimeout(() => { $("#set-tip").textContent = ""; }, 2500);
}

/* ------------------------------------------------------------------ */
/* 轮询状态                                                            */
/* ------------------------------------------------------------------ */
let pollTimer = null;

async function pollState() {
  const data = await api("/api/state");
  if (!data.ok) {
    $("#pill-running").textContent = "服务已断开";
    return;
  }
  app.settings = data.settings;
  app.tasks = data.tasks || [];
  if (data.versions) {
    $("#net-component").textContent = data.versions.curl_version || "—";
    $("#status-proxy").textContent = data.versions.proxy || "未设置";
  }
  renderTasks(app.tasks);

  const active = app.tasks.filter((t) => ["running", "queued", "pausing"].includes(t.status)).length;
  $("#pill-running").textContent = active ? `下载中 ${active}` : "空闲";
  $("#badge-tasks").textContent = app.tasks.filter((t) => ["running", "queued", "pausing", "paused", "error"].includes(t.status)).length;

  // 任务状态变化提示
  app.tasks.forEach((task) => {
    const before = app.knownStatus.get(task.id);
    app.knownStatus.set(task.id, task.status);
    if (before && before !== task.status) {
      if (task.status === "done") {
        toast(`「${task.display_name || task.repo_id}」下载完成`, "ok");
        loadLibrary(true);
      } else if (task.status === "error") {
        toast(`「${task.display_name || task.repo_id}」出错：${task.error || ""}`, "err");
      } else if (task.status === "paused") {
        toast(`「${task.display_name || task.repo_id}」已暂停，可以随时继续`, "");
      }
    }
  });

  if (data.library) {
    app.library = data.library.items || [];
    if (app.view === "library") renderLibrary(data.library.total_size || 0);
    else $("#badge-library").textContent = app.library.length;
  }
}

/* ------------------------------------------------------------------ */
/* 事件绑定                                                            */
/* ------------------------------------------------------------------ */
function bindEvents() {
  $("#tabs").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab) switchView(tab.dataset.view);
  });

  $("#search-btn").addEventListener("click", () => doSearch(true));
  $("#search-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") doSearch(true);
  });
  $("#search-sort").addEventListener("change", () => doSearch(true));
  $("#load-more").addEventListener("click", () => {
    app.search.page += 1;
    doSearch(false);
  });

  $("#quick-tags").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    $("#search-input").value = chip.dataset.q;
    doSearch(true);
  });

  $("#search-results").addEventListener("click", (event) => {
    const download = event.target.closest("[data-download]");
    const detail = event.target.closest("[data-detail]");
    const index = download ? Number(download.dataset.download) : detail ? Number(detail.dataset.detail) : -1;
    if (index < 0) return;
    const item = app.search.items[index];
    if (item) openDownloadDialog(item.repo_id, item.display_name || item.repo_id);
  });

  $("#task-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const node = button.closest(".task");
    const task = app.tasks.find((t) => t.id === node.dataset.id);
    if (task) handleTaskAction(task.id, button.dataset.action, task);
  });

  $("#pause-all").addEventListener("click", async () => {
    const running = app.tasks.filter((t) => ["running", "queued"].includes(t.status));
    for (const task of running) await api(`/api/tasks/${task.id}/pause`, { method: "POST" });
    pollState();
  });
  $("#resume-all").addEventListener("click", async () => {
    const paused = app.tasks.filter((t) => ["paused", "error"].includes(t.status));
    for (const task of paused) await api(`/api/tasks/${task.id}/resume`, { method: "POST" });
    pollState();
  });

  $("#library-refresh").addEventListener("click", () => loadLibrary(true));
  $("#library-open").addEventListener("click", () => {
    if (app.settings) api("/api/open", { method: "POST", body: JSON.stringify({ path: app.settings.download_dir }) });
  });
  $("#library-list").addEventListener("click", async (event) => {
    const open = event.target.closest("[data-lib-open]");
    const copy = event.target.closest("[data-lib-copy]");
    const del = event.target.closest("[data-lib-del]");
    if (open) {
      const item = app.library[Number(open.dataset.libOpen)];
      if (item) api("/api/open", { method: "POST", body: JSON.stringify({ path: item.path }) });
    } else if (copy) {
      const item = app.library[Number(copy.dataset.libCopy)];
      if (item) copyText(item.path);
    } else if (del) {
      const item = app.library[Number(del.dataset.libDel)];
      if (!item) return;
      if (!confirm(`确定要删除磁盘上的「${item.repo_id || item.name}」吗？\n这会真的删掉 ${fmtBytes(item.size)} 的文件，删完不能恢复。`)) return;
      const data = await api("/api/delete-folder", { method: "POST", body: JSON.stringify({ path: item.path }) });
      if (!data.ok) { toast(data.error || "删除失败", "err"); return; }
      toast("已删除", "ok");
      loadLibrary(true);
    }
  });

  $("#set-browse").addEventListener("click", async () => {
    const data = await api("/api/browse", { method: "POST", body: JSON.stringify({ initial: $("#set-dir").value }) });
    if (data.ok && data.path) $("#set-dir").value = data.path;
  });
  $("#set-parallel").addEventListener("input", (event) => { $("#set-parallel-val").textContent = event.target.value; });
  $("#set-files").addEventListener("input", (event) => { $("#set-files-val").textContent = event.target.value; });
  $("#set-save").addEventListener("click", saveSettings);
  $("#btn-log").addEventListener("click", () => {
    const base = $("#set-dir").value.trim();
    api("/api/open-log", { method: "POST", body: JSON.stringify({}) });
  });
  $("#btn-quit").addEventListener("click", async () => {
    if (!confirm("确定要退出程序吗？正在下载的任务会暂停，下次启动可以继续。")) return;
    await api("/api/shutdown", { method: "POST" });
    toast("程序已退出，可以直接关闭这个网页了");
    document.body.innerHTML = `<div style="display:grid;place-items:center;height:100vh;font-family:system-ui"><div style="text-align:center"><h2>已退出</h2><p style="color:#6d7588;margin-top:8px">现在可以关闭这个页面了。</p></div></div>`;
  });

  // 弹窗
  $("#modal-close").addEventListener("click", () => $("#modal").classList.add("hidden"));
  $("#modal-cancel").addEventListener("click", () => $("#modal").classList.add("hidden"));
  $("#modal-start").addEventListener("click", startDownload);
  $("#modal-revision").addEventListener("change", (event) => {
    app.modal.revision = event.target.value;
    loadModalFiles();
  });
  $("#modal-reload").addEventListener("click", loadModalFiles);
  $("#modal-files").addEventListener("change", (event) => {
    const box = event.target.closest("input[type=checkbox]");
    if (!box) return;
    const path = box.dataset.path;
    if (box.checked) app.modal.selected.add(path);
    else app.modal.selected.delete(path);
    updateModalSummary();
  });
  $$("[data-pick]").forEach((button) => {
    button.addEventListener("click", () => pickFiles(button.dataset.pick));
  });
  $("#modal").addEventListener("click", (event) => {
    if (event.target === $("#modal")) $("#modal").classList.add("hidden");
  });
}

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
async function boot() {
  bindEvents();
  await loadSettings();
  await pollState();
  pollTimer = setInterval(pollState, 1200);
  switchView((location.hash || "#search").slice(1));
  loadFeatured();
  const params = new URLSearchParams(location.search);
  const preset = params.get("model");
  if (preset) {
    const repoId = normalizeRepoId(preset);
    if (repoId) openDownloadDialog(repoId, repoId.split("/")[1]);
  }
  $("#search-input").focus();
}

boot();
