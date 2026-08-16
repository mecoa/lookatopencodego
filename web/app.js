"use strict";

const state = {
  config: null,
  lastSeq: 0,
  activeTab: "overview",
  modelFilter: "all",
  chartModel: "all",
  chartDays: 14,
  sessWindow: "month_rolling",
  sessModel: "all",
  sessSearch: "",
  sortKey: "last_ts",
  sortDir: -1,
  overview: null,
  models: null,
  charts: {},
};

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

async function api(path) {
  const resp = await fetch("/api" + path, { cache: "no-store" });
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return resp.json();
}

function fmtMoney(v) {
  if (v === null || v === undefined) return "—";
  return "$" + v.toFixed(v < 1 ? 4 : 2);
}

function fmtTokens(n) {
  if (n === null || n === undefined) return "—";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "G";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

function fmtDur(ms) {
  if (!ms) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m" + (s % 60) + "s";
  return Math.floor(s / 3600) + "h" + Math.floor((s % 3600) / 60) + "m";
}

function fmtTime(ts) {
  const d = new Date(ts);
  return d.toLocaleString("zh-CN", { hour12: false });
}

function barClass(pct) {
  if (pct === null || pct === undefined) return "ok";
  if (pct >= 90) return "crit";
  if (pct >= 60) return "warn";
  return "ok";
}

function progressHTML(used, limit, pct, kind) {
  if (limit === null || limit === undefined) {
    return '<span class="muted">' + used + ' / —</span>';
  }
  const p = Math.min(pct || 0, 100);
  return (
    '<div class="bar ' + barClass(pct) + '"><div style="width:' + p + '%"></div></div>' +
    '<span class="muted">' + used + " / " + limit + " · " + (pct === null ? "—" : pct.toFixed(1) + "%") + "</span>"
  );
}

function emptyModels() {
  if (!state.models) return {};
  return state.models;
}

function isVisible(model) {
  if (state.modelFilter === "all") return true;
  return model === state.modelFilter;
}

/* ---------- tabs ---------- */

function initTabs() {
  $$("#tabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$("#tabs button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      $$(".tabpane").forEach((p) => p.classList.remove("active"));
      $("#tab-" + btn.dataset.tab).classList.add("active");
      state.activeTab = btn.dataset.tab;
      if (state.activeTab === "chart") loadSeries();
      if (state.activeTab === "sessions") loadSessions();
    });
  });
}

/* ---------- overview / models ---------- */

function windowCard(label, win, limitLabel) {
  const pct = win.percent;
  const p = Math.min(pct || 0, 100);
  const reset = win.resets_at_ms
    ? new Date(win.resets_at_ms).toLocaleTimeString("zh-CN", { hour12: false })
    : "—";
  return `
    <div class="card">
      <div class="k">${label} ${limitLabel ? "· " + limitLabel : ""}</div>
      <div class="v">${pct === null || pct === undefined ? "—" : pct + "%"}</div>
      <div class="sub">${fmtMoney(win.used)} / ${fmtMoney(win.limit)} · 剩余 ${fmtMoney(win.remaining)} · 重置 ${reset}</div>
      <div class="bar ${barClass(pct)}" style="margin-top:8px"><div style="width:${p}%"></div></div>
    </div>`;
}

function renderAccount() {
  const account = (state.overview && state.overview.account) || {};
  const note = $("#account-note");
  const warn = $("#local-warn");
  if (!account.configured) {
    const why = account.reason === "disabled"
      ? "官方额度同步已在配置中禁用。"
      : "未检测到 Go API Key（已从 ~/.local/share/opencode/auth.json 自动读取，或在 config.json/env 配置）。";
    note.textContent = "⚠ " + why + " 当前仅显示本机估算，跨设备用量可能不准确。";
    warn.textContent = "";
    $("#account-cards").innerHTML = "";
    return;
  }
  const sourceLabel = { "config": "config.json", "file": "key 文件", "env": "环境变量", "auth.json": "opencode auth.json" };
  const src = account.key_source ? " · Key 来源: " + (sourceLabel[account.key_source] || account.key_source) : "";
  note.textContent = account.error
    ? "⚠ 官方同步失败：" + account.error + "（使用本机估算）"
    : "✓ 官方额度已连接（服务器端 · 跨设备权威，每 " + (state.config.account.refresh_seconds || 60) + "s 更新）" + src;
  const wins = account.windows || {};
  const card = windowCard;
  $("#account-cards").innerHTML =
    (wins.rolling ? card("官方 · 滚动 5h", wins.rolling) : "") +
    (wins.weekly ? card("官方 · 周", wins.weekly) : "") +
    (wins.monthly ? card("官方 · 月", wins.monthly) : "");
  const local = (state.overview.plan || {});
  const diffs = [];
  const pair = { "5h": "rolling", "week": "weekly", "month": "monthly" };
  for (const k of Object.keys(pair)) {
    const l = local[k];
    const o = wins[pair[k]];
    if (l && o && o.percent !== null && o.percent !== undefined &&
        Math.abs(l.pct - o.percent) > 5) {
      diffs.push("本地" + k + " " + (l.pct || 0).toFixed(0) + "% ≠ 官方 " + o.percent + "%");
    }
  }
  warn.textContent = diffs.length ? "⚠ " + diffs.join("；") + " —— 以官方为准" : "";
}

function renderPlanCards() {
  const plan = state.overview.plan || {};
  const today = state.overview.today || {};
  const cards = [
    { k: "今日用量（本机）", v: fmtMoney(today.value), sub: today.requests + " 次请求" },
    { k: "5小时 · 本地估算", v: fmtMoney(plan["5h"] && plan["5h"].value), sub: "$12.00 · " + (plan["5h"] ? (plan["5h"].pct || 0).toFixed(1) : 0) + "%", pct: plan["5h"] && plan["5h"].pct },
    { k: "周 · 本地估算", v: fmtMoney(plan.week && plan.week.value), sub: "$30.00 · " + (plan.week ? (plan.week.pct || 0).toFixed(1) : 0) + "%", pct: plan.week && plan.week.pct },
    { k: "月 · 本地估算", v: fmtMoney(plan.month && plan.month.value), sub: "$60.00 · " + (plan.month ? (plan.month.pct || 0).toFixed(1) : 0) + "%", pct: plan.month && plan.month.pct },
  ];
  $("#plan-cards").innerHTML = cards.map((c) => `
    <div class="card">
      <div class="k">${c.k}</div>
      <div class="v">${c.v}</div>
      <div class="sub">${c.sub}</div>
      <div class="bar ${barClass(c.pct)}" style="margin-top:8px"><div style="width:${Math.min(c.pct || 0, 100)}%"></div></div>
    </div>`).join("");
}

function renderModels() {
  const models = emptyModels();
  const tbody = $("#models-table tbody");
  const entries = Object.values(models).sort((a, b) => {
    const aVal = a.windows["5h"].value.used;
    const bVal = b.windows["5h"].value.used;
    return bVal - aVal || a.model.localeCompare(b.model);
  });
  tbody.innerHTML = "";
  for (const e of entries) {
    if (!isVisible(e.model)) continue;
    const tr = document.createElement("tr");
    if (!e.eligible) tr.style.opacity = "0.55";
    let name = e.model;
    if (!e.eligible) name += ' <span class="tag unknown">免费不计额</span>';
    else if (!e.configured) name += ' <span class="tag unknown">未配置价格</span>';
    const cells = [name];
    for (const w of ["5h", "week", "month"]) {
      const item = e.windows[w];
      const req = item.requests;
      const val = item.value;
      cells.push(req.limit === null ? "—" : req.used + " / " + req.limit);
      cells.push(fmtMoney(val.used));
    }
    const allowance = state.config && state.config.models[e.model] ? state.config.models[e.model].allowance_month : null;
    cells.push(allowance === null || allowance === undefined ? "—" : "$" + allowance);
    const binding5 = e.windows["5h"].binding;
    const bTag =
      binding5 === "requests" ? '<span class="tag binding-req">请求</span>' :
      binding5 === "value" ? '<span class="tag binding-val">值</span>' : '<span class="muted">—</span>';
    cells.push(bTag);
    cells.forEach((c, i) => {
      const td = document.createElement("td");
      td.innerHTML = c;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }
}

/* ---------- live ---------- */

async function pollLive() {
  const data = await api("/live?since=" + state.lastSeq);
  if (data.seq > state.lastSeq) {
    state.lastSeq = data.seq;
    for (const ev of data.events) {
      appendEvent(ev);
    }
    $("#seq-label").textContent = "seq " + state.lastSeq;
  }
}

function appendEvent(ev) {
  const feed = $("#live-feed");
  const row = document.createElement("div");
  row.className = "row";
  const type = (ev.type || "").replace(".updated.1", "").replace(/^message\./, "");
  let body = type;
  if (ev.role) body += " · role=" + ev.role;
  if (ev.model) body += ' · <span class="m">' + ev.model + "</span>";
  if (ev.part_type) body += " · part=" + ev.part_type;
  if (ev.cost !== undefined && ev.cost !== null) body += " · cost=$" + ev.cost.toFixed(5);
  if (ev.tokens) body += " · tok=" + (ev.tokens.input || 0) + "/" + (ev.tokens.output || 0);
  row.innerHTML = '<span class="t">' + fmtTime(Date.now()) + "</span> " + body;
  feed.appendChild(row);
  while (feed.children.length > 200) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
}

function renderActiveSessions() {
  const box = $("#active-sessions");
  const list = state.overview.active_sessions || [];
  box.innerHTML = "";
  if (!list.length) {
    box.innerHTML = '<div class="active-card"><span class="muted">当前无活跃会话</span></div>';
    return;
  }
  for (const a of list) {
    const div = document.createElement("div");
    div.className = "active-card";
    div.innerHTML =
      '<span class="dot' + (a.streaming ? "" : " idle") + '"></span>' +
      '<span class="title">' + esc(a.title || a.session_id) + "</span>" +
      '<span class="meta">' + esc(a.model || "—") + " · " + esc(a.agent || "—") + " · " + fmtTime(a.last_ts) +
      (a.streaming ? ' · <span style="color:var(--red)">流式中</span>' : "") + "</span>";
    box.appendChild(div);
  }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

/* ---------- charts ---------- */

const PALETTE = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#bc8cff", "#39c5cf", "#f778ba", "#e3b341", "#76e3ea", "#ffa657"];

async function loadSeries() {
  try {
    const data = await api("/series?days=" + state.chartDays + "&model=" + state.chartModel);
    renderValueChart(data);
    renderTokensChart(data);
    if (!window.Chart) {
      $("#chart-fallback").textContent = "Chart.js 未加载（离线）。图表不可用，其余功能正常。";
    } else {
      $("#chart-fallback").textContent = "";
    }
  } catch (e) {
    $("#chart-fallback").textContent = "加载图表数据失败: " + e.message;
  }
}

function renderValueChart(data) {
  const labels = data.series.map((d) => d.date.slice(5));
  const values = data.series.map((d) => Math.round(d.value * 10000) / 10000);
  const cfg = {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "Go 计价值 ($)",
        data: values,
        backgroundColor: PALETTE[0],
        borderRadius: 3,
      }],
    },
    options: {
      plugins: { title: { display: true, text: "每日 Go 计价值 ($)" }, legend: { display: false } },
      scales: { y: { ticks: { callback: (v) => "$" + v } } },
    },
  };
  drawChart("chart-value", cfg);
}

function renderTokensChart(data) {
  const labels = data.series.map((d) => d.date.slice(5));
  const inp = data.series.map((d) => d.tokens.input);
  const outp = data.series.map((d) => d.tokens.output);
  const reas = data.series.map((d) => d.tokens.reasoning);
  const cache = data.series.map((d) => d.tokens.cache_read);
  const cfg = {
    data: {
      labels,
      datasets: [
        { type: "bar", label: "输入", data: inp, backgroundColor: PALETTE[1], stack: "t", borderRadius: 3 },
        { type: "bar", label: "输出", data: outp, backgroundColor: PALETTE[0], stack: "t", borderRadius: 3 },
        { type: "bar", label: "推理", data: reas, backgroundColor: PALETTE[4], stack: "t", borderRadius: 3 },
        { type: "line", label: "缓存读", data: cache, borderColor: PALETTE[2], backgroundColor: "transparent", yAxisID: "y1", tension: 0.3, pointRadius: 2 },
      ],
    },
    options: {
      plugins: { title: { display: true, text: "每日 Token 用量" }, legend: { position: "bottom" } },
      scales: {
        x: { stacked: true },
        y: { stacked: true, ticks: { callback: (v) => fmtTokens(v) } },
        y1: { position: "right", grid: { drawOnChartArea: false }, ticks: { callback: (v) => fmtTokens(v) } },
      },
    },
  };
  drawChart("chart-tokens", cfg);
}

function drawChart(id, cfg) {
  const canvas = $("#" + id);
  if (!canvas || !window.Chart) return;
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart(canvas, cfg);
}

/* ---------- sessions ---------- */

async function loadSessions() {
  try {
    const model = state.sessModel === "all" ? "" : state.sessModel;
    const data = await api("/sessions?window=" + state.sessWindow + "&model=" + model + "&limit=500");
    renderSessions(data);
  } catch (e) {
    $("#sessions-table tbody").innerHTML = '<tr><td colspan="11">加载失败: ' + esc(e.message) + "</td></tr>";
  }
}

function renderSessions(list) {
  const tbody = $("#sessions-table tbody");
  const q = state.sessSearch.toLowerCase();
  let rows = list.filter((s) => !q || (s.title || "").toLowerCase().includes(q) || s.session_id.includes(q));
  rows = rows.sort((a, b) => {
    const av = keyVal(a);
    const bv = keyVal(b);
    if (typeof av === "string") return state.sortDir * av.localeCompare(bv);
    return state.sortDir * (av - bv);
  });
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="muted">无数据</td></tr>';
    return;
  }
  for (const s of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + fmtTime(s.last_ts) + "</td>" +
      "<td title='" + esc(s.session_id) + "'>" + esc(s.title || s.session_id) + "</td>" +
      "<td>" + esc(s.model || "—") + "</td>" +
      "<td>" + esc(s.agent || "—") + "</td>" +
      "<td>" + s.requests + "</td>" +
      "<td>" + fmtMoney(s.value) + "</td>" +
      "<td>" + fmtMoney(s.cost) + "</td>" +
      "<td>" + fmtTokens(s.tokens.input) + "</td>" +
      "<td>" + fmtTokens(s.tokens.output) + "</td>" +
      "<td>" + fmtTokens(s.tokens.cache_read) + "</td>" +
      "<td>" + fmtDur(s.duration_ms) + "</td>";
    tbody.appendChild(tr);
  }
}

function keyVal(s) {
  const k = state.sortKey;
  if (k.startsWith("tokens.")) return s.tokens[k.split(".")[1]] || 0;
  if (k === "last_ts") return s.last_ts || 0;
  if (k === "duration_ms") return s.duration_ms || 0;
  const v = s[k];
  return typeof v === "number" ? v : String(v || "");
}

function initSorting() {
  $$("#sessions-table thead th").forEach((th) => {
    const key = th.dataset.key;
    if (!key) return;
    th.addEventListener("click", () => {
      if (state.sortKey === key) state.sortDir = -state.sortDir;
      else {
        state.sortKey = key;
        state.sortDir = key === "title" || key === "model" || key === "agent" ? 1 : -1;
      }
      $$("#sessions-table thead th").forEach((t) => t.classList.remove("sorted", "desc"));
      th.classList.add("sorted");
      if (state.sortDir === -1) th.classList.add("desc");
      loadSessions();
    });
  });
}

/* ---------- filters ---------- */

function populateModelSelects() {
  const models = Object.keys(state.models || {}).sort();
  for (const id of ["models-filter", "chart-model", "sess-model"]) {
    const sel = $("#" + id);
    if (!sel) continue;
    sel.innerHTML = '<option value="all">全部</option>' +
      models.map((m) => '<option value="' + esc(m) + '">' + esc(m) + "</option>").join("");
  }
}

function initFilters() {
  $("#models-filter").addEventListener("change", (e) => {
    state.modelFilter = e.target.value;
    renderModels();
  });
  $("#chart-model").addEventListener("change", (e) => {
    state.chartModel = e.target.value;
    loadSeries();
  });
  $("#chart-days").addEventListener("change", (e) => {
    state.chartDays = parseInt(e.target.value, 10);
    loadSeries();
  });
  $("#sess-window").addEventListener("change", (e) => {
    state.sessWindow = e.target.value;
    loadSessions();
  });
  $("#sess-model").addEventListener("change", (e) => {
    state.sessModel = e.target.value;
    loadSessions();
  });
  $("#sess-search").addEventListener("input", debounce((e) => {
    state.sessSearch = e.target.value;
    loadSessions();
  }, 250));
  $("#policy-refresh").addEventListener("click", refreshPolicy);
}

async function refreshPolicy() {
  const btn = $("#policy-refresh");
  if (!confirm("从官方文档抓取最新单价/请求额度并更新 config.json？\n（将自动备份原配置）")) return;
  btn.disabled = true;
  btn.textContent = "更新中…";
  try {
    const resp = await fetch("/api/policy/refresh", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
    const diff = data.write && data.write.diff ? data.write.diff : (data.diff || {});
    alert("政策已更新。\n\n新增: " + (diff.added || []).join(", ") +
      "\n变更: " + (diff.changed || []).length + " 个模型" +
      "\n移除: " + (diff.removed || []).join(", ") +
      "\n备份: " + ((data.write && data.write.backup) || "无"));
    state.config = await api("/config");
    populateModelSelects();
    refresh();
  } catch (e) {
    alert("更新失败: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "从官方文档更新政策";
  }
}

function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

/* ---------- main loop ---------- */

async function refresh() {
  try {
    const [overview, models] = await Promise.all([api("/overview"), api("/models")]);
    state.overview = overview;
    state.models = models;
    renderPlanCards();
    renderAccount();
    renderModels();
    renderActiveSessions();
    $("#status").textContent = "更新于 " + fmtTime(Date.now()) + " · 模型数 " + Object.keys(models).length;
    if (state.activeTab === "live") pollLive();
    else if (state.activeTab === "chart") loadSeries();
    else if (state.activeTab === "sessions") loadSessions();
  } catch (e) {
    $("#status").textContent = "API 错误: " + e.message;
  }
}

async function init() {
  state.config = await api("/config");
  initTabs();
  initFilters();
  initSorting();
  const models = await api("/models");
  state.models = models;
  populateModelSelects();
  const interval = Math.max(1, state.config.refresh_interval || 2);
  refresh();
  setInterval(refresh, interval * 1000);
}

init().catch((e) => {
  $("#status").textContent = "初始化失败: " + e.message;
});
