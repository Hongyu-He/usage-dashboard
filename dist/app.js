"use strict";

// Pure data helpers are also exercised by Node; no bundler or CDN required.
const AGENTS = ["codex", "claude"];
const COMPONENTS = ["inputTokens", "outputTokens", "cacheReadTokens", "cacheCreationTokens"];
function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const stamp = Date.parse(value + "T00:00:00Z");
  return Number.isFinite(stamp) && new Date(stamp).toISOString().slice(0, 10) === value;
}
function shiftDay(day, offset) {
  const date = new Date(day + "T00:00:00Z");
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}
function dateRange(since, until) {
  if (!validDate(since) || !validDate(until) || since > until) throw new Error("请选择有效的起止日期，开始不能晚于结束。");
  if ((Date.parse(until) - Date.parse(since)) / 86400000 > 3660) throw new Error("一次最多查看 10 年。");
  const dates = [];
  for (let day = since; day <= until; day = shiftDay(day, 1)) dates.push(day);
  return dates;
}
function summarize(rows, since, until) {
  const days = dateRange(since, until).map(date => ({ date, codex: { cost: 0, tokens: 0 }, claude: { cost: 0, tokens: 0 } }));
  return summarizePeriods(rows, days);
}
function summarizeIntraday(data) {
  if (!data) throw new Error("15分钟数据尚未就绪，请刷新数据后重试。");
  const start = Date.parse(data.start), end = Date.parse(data.end), step = data.bucketSeconds * 1000;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end - start !== 86400000 || step !== 900000 || !Array.isArray(data.rows)) throw new Error("15分钟数据不完整，请刷新数据后重试。");
  const periods = [];
  for (let stamp = Math.floor(start / step) * step; stamp < end; stamp += step) {
    periods.push({ date: new Date(Math.max(start, stamp)).toISOString(), end: new Date(Math.min(end, stamp + step)).toISOString(), codex: { cost: 0, tokens: 0 }, claude: { cost: 0, tokens: 0 } });
  }
  const result = summarizePeriods(data.rows.map(row => ({ ...row, date: new Date(row.start).toISOString() })), periods);
  return { ...result, intraday: true, start: data.start, end: data.end };
}
function summarizePeriods(rows, days) {
  const index = new Map(days.map(day => [day.date, day]));
  const totals = Object.fromEntries(AGENTS.map(agent => [agent, { cost: 0, tokens: 0 }]));
  const mix = Object.fromEntries(COMPONENTS.map(key => [key, 0]));
  const models = new Map();
  for (const row of rows) {
    if (!index.has(row.date)) continue;
    for (const metric of ["cost", "tokens"]) {
      totals[row.agent][metric] += row[metric];
      index.get(row.date)[row.agent][metric] += row[metric];
    }
    for (const key of COMPONENTS) mix[key] += row[key];
    for (const model of row.models) {
      const key = row.agent + ":" + model.name;
      const current = models.get(key) || { agent: row.agent, name: model.name, tokens: 0 };
      current.tokens += model.tokens;
      models.set(key, current);
    }
  }
  return { days, totals, mix, models: [...models.values()].sort((a, b) => b.tokens - a.tokens) };
}
function seriesFor(days, metric, cumulative) {
  const running = { codex: 0, claude: 0 };
  return days.map(day => {
    const result = { date: day.date, end: day.end };
    for (const agent of AGENTS) {
      running[agent] += day[agent][metric];
      result[agent] = cumulative ? running[agent] : day[agent][metric];
    }
    return result;
  });
}
function csvFor(days) {
  const intraday = Boolean(days[0]?.end);
  return [(intraday ? "start_utc,end_utc" : "date") + ",codex_cost_usd,claude_cost_usd,total_cost_usd,codex_tokens,claude_tokens,total_tokens",
    ...days.map(day => [...(intraday ? [day.date, day.end] : [day.date]), day.codex.cost.toFixed(6), day.claude.cost.toFixed(6),
      (day.codex.cost + day.claude.cost).toFixed(6), day.codex.tokens, day.claude.tokens,
      day.codex.tokens + day.claude.tokens].join(","))].join("\r\n");
}
// Monotone cubic interpolation: pass through each sample without inventing
// peaks, negative usage, or dips in a cumulative series.
function smoothPath(points) {
  if (!points.length) return "";
  const slopes = points.slice(1).map((p, i) => (p.y - points[i].y) / (p.x - points[i].x));
  const tangents = points.map((_, i) => {
    if (i === 0) return slopes[0] || 0;
    if (i === points.length - 1) return slopes[i - 1] || 0;
    const a = slopes[i - 1], b = slopes[i];
    return a * b <= 0 ? 0 : 2 * a * b / (a + b);
  });
  let path = `M ${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], dx = (b.x - a.x) / 3;
    path += ` C ${a.x + dx},${a.y + dx * tangents[i - 1]} ${b.x - dx},${b.y - dx * tangents[i]} ${b.x},${b.y}`;
  }
  return path;
}
if (typeof module !== "undefined") module.exports = { validDate, shiftDay, dateRange, summarize, summarizeIntraday, seriesFor, csvFor, smoothPath };

if (typeof document !== "undefined") {
  const $ = id => document.getElementById(id);
  const money = value => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
  const integer = value => new Intl.NumberFormat("en-US").format(value);
  const compact = value => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(value);
  const element = (tag, text, cls) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; };
  const svgEl = (tag, attrs, text) => {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
    if (text !== undefined) el.textContent = text;
    return el;
  };
  let payload = null, status = null, summary = null, preset = "last30", pollRunning = false, offline = false;
  function localTime(stamp) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: payload.timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(stamp)).map(part => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
  }
  function periodLabel(period) {
    if (!period.end) return period.date;
    const start = localTime(period.date), end = localTime(period.end);
    return `${start} — ${start.slice(0, 10) === end.slice(0, 10) ? end.slice(11) : end}`;
  }
  function showRangeError(message = "") { $("range-error").textContent = message; $("range-error").hidden = !message; }
  function setPreset(value) {
    if (!payload) return;
    if (value === "last24") {
      try { summarizeIntraday(payload.intraday); }
      catch (error) { showRangeError(error.message); return; }
      $("since").value = localTime(payload.intraday.start).slice(0, 10);
      $("until").value = localTime(payload.intraday.end).slice(0, 10);
      if (preset !== "last24") $("view").value = "daily";
      preset = value; render(); return;
    }
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: payload.timezone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date()).map(part => [part.type, part.value]));
    const today = `${parts.year}-${parts.month}-${parts.day}`;
    const until = payload.until < today ? payload.until : today;
    let since = payload.since;
    if (value === "week") since = shiftDay(today, -6);
    if (value === "last30") since = shiftDay(today, -29);
    if (value === "month") since = today.slice(0, 8) + "01";
    since = since < payload.since ? payload.since : since;
    // A stale snapshot cannot show a later day as zero usage.
    if (since > until) since = until;
    $("since").value = since; $("until").value = until; preset = value;
    render();
  }
  function selectRange(since, until) {
    if (!payload) throw new Error("数据尚未就绪。");
    dateRange(since, until);
    if (since < payload.since || until > payload.until) throw new Error(`可用数据范围：${payload.since} 至 ${payload.until}。请先刷新以获得更新日期。`);
    $("since").value = since; $("until").value = until; preset = "custom"; render();
    return { since, until, totals: summary.totals };
  }
  function render() {
    if (!payload) return;
    try { summary = preset === "last24" ? summarizeIntraday(payload.intraday) : summarize(payload.rows, $("since").value, $("until").value); showRangeError(); }
    catch (error) { showRangeError(error.message); return; }
    document.querySelectorAll("[data-preset]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.preset === preset)));
    for (const agent of AGENTS) {
      $(agent + "-cost").textContent = money(summary.totals[agent].cost);
      $(agent + "-tokens").textContent = integer(summary.totals[agent].tokens) + " tokens";
    }
    const totalTokens = summary.totals.codex.tokens + summary.totals.claude.tokens;
    $("combined-cost").textContent = money(summary.totals.codex.cost + summary.totals.claude.cost);
    $("combined-tokens").textContent = integer(totalTokens) + " tokens";
    $("range-label").textContent = summary.intraday ? `${localTime(summary.start)} — ${localTime(summary.end)} · 近24小时 · 每15分钟 · ${payload.timezone}` : `${$("since").value} — ${$("until").value} · ${summary.days.length} 天`;
    $("table-note").textContent = summary.intraday ? `${payload.timezone} · 截至最近采集开始时刻 · 首尾时段按24小时窗口截取` : `${payload.timezone} · 最新一天统计截至采集时刻`;
    $("period-view").textContent = summary.intraday ? "每15分钟" : "每日";
    $("table-title").textContent = summary.intraday ? "15分钟明细" : "每日明细";
    $("period-heading").textContent = summary.intraday ? "时段" : "日期";
    for (const agent of AGENTS) $(agent + "-caption").textContent = summary.intraday ? "每15分钟等效费用" : "每日等效费用";
    $("provenance").textContent = `${payload.ccusageVersion} · ${payload.timezone}`;
    $("models").replaceChildren();
    for (const model of summary.models) {
      const row = element("div", undefined, "model-row"), label = element("div", undefined, "model-label");
      label.append(element("span", `${model.name} · ${model.agent === "codex" ? "Codex" : "Claude"}`), element("span", `${compact(model.tokens)} · ${(model.tokens / (totalTokens || 1) * 100).toFixed(1)}%`));
      const bar = svgEl("svg", { viewBox: "0 0 100 2", width: "100%", height: "7", "aria-hidden": "true" });
      bar.append(svgEl("rect", { width: "100", height: "2", fill: "var(--bg)" }), svgEl("rect", { width: model.tokens / (summary.models[0]?.tokens || 1) * 100, height: "2", fill: `var(--${model.agent})` }));
      row.append(label, bar); $("models").append(row);
    }
    if (!summary.models.length) $("models").append(element("div", "该区间没有记录到用量", "empty"));
    $("token-mix").replaceChildren();
    const labels = ["输入（不含缓存）", "输出（含推理）", "缓存读取", "缓存写入"];
    COMPONENTS.forEach((key, index) => {
      const row = element("div", undefined, "mix-row"), value = element("strong", integer(summary.mix[key]));
      value.append(element("small", `${(summary.mix[key] / (totalTokens || 1) * 100).toFixed(1)}%`));
      row.append(element("span", labels[index]), value); $("token-mix").append(row);
    });
    $("daily-rows").replaceChildren();
    for (const day of [...summary.days].reverse()) {
      const tr = element("tr");
      for (const value of [periodLabel(day), money(day.codex.cost), money(day.claude.cost), money(day.codex.cost + day.claude.cost), integer(day.codex.tokens + day.claude.tokens)]) tr.append(element("td", value));
      $("daily-rows").append(tr);
    }
    drawSummaryCharts();
    drawChart();
  }
  function areaSeries(svg, points, agent, id, baseline) {
    const defs = svgEl("defs", {}), gradient = svgEl("linearGradient", { id, x1: "0", y1: "0", x2: "0", y2: "1" });
    gradient.append(svgEl("stop", { offset: "0%", "stop-color": `var(--${agent})`, "stop-opacity": ".24" }), svgEl("stop", { offset: "100%", "stop-color": `var(--${agent})`, "stop-opacity": ".015" }));
    defs.append(gradient); svg.append(defs);
    const path = smoothPath(points);
    const area = svgEl("path", { d: `${path} L ${points.at(-1).x},${baseline} L ${points[0].x},${baseline} Z`, fill: `url(#${id})` });
    const line = svgEl("path", { d: path, class: `trend-line line-${agent}` });
    svg.append(area);
    return line;
  }
  function drawSummaryCharts() {
    const total = summary.totals.codex.cost + summary.totals.claude.cost;
    const shares = AGENTS.map(agent => total > 0 ? summary.totals[agent].cost / total * 100 : 0);
    const ring = svgEl("svg", { viewBox: "0 0 108 108", role: "img", "aria-label": total > 0 ? `费用占比：Codex ${shares[0].toFixed(1)}%，Claude Code ${shares[1].toFixed(1)}%` : "区间没有记录到费用，占比不适用" });
    ring.append(svgEl("circle", { cx: 54, cy: 54, r: 42, fill: "none", stroke: "currentColor", "stroke-opacity": ".14", "stroke-width": 11 }));
    let offset = 0;
    AGENTS.forEach((agent, i) => {
      if (shares[i] > 0) ring.append(svgEl("circle", { cx: 54, cy: 54, r: 42, fill: "none", stroke: `var(--${agent})`, "stroke-width": 11, pathLength: 100, "stroke-dasharray": `${shares[i]} ${100 - shares[i]}`, "stroke-dashoffset": -offset, transform: "rotate(-90 54 54)" }));
      offset += shares[i];
      $(agent + "-share").textContent = total > 0 ? `占比 ${shares[i].toFixed(1)}%` : "占比 —";
    });
    ring.append(svgEl("text", { x: 54, y: 59, "text-anchor": "middle", class: "ring-label" }, total > 0 ? "费用占比" : "暂无费用"));
    const legend = element("div", undefined, "share-legend");
    AGENTS.forEach((agent, i) => {
      const row = element("div", undefined, "share-row");
      row.append(element("i", undefined, agent + "-fill"), element("span", agent === "codex" ? "Codex" : "Claude Code"), element("b", total > 0 ? `${shares[i].toFixed(1)}%` : "—"));
      legend.append(row);
    });
    $("cost-share").replaceChildren(ring, legend);
    // The same scale on both cards makes their amplitudes comparable.
    const maximum = Math.max(1, ...summary.days.flatMap(day => AGENTS.map(agent => day[agent].cost))) * 1.08;
    for (const agent of AGENTS) {
      const svg = svgEl("svg", { viewBox: "0 0 300 86", preserveAspectRatio: "none", role: "img", "aria-label": `${agent === "codex" ? "Codex" : "Claude Code"} 区间${summary.intraday ? "每15分钟" : "每日"}等效费用趋势；两张迷你图刻度相同，详细数值见下方明细` });
      const points = summary.days.map((day, i) => ({ x: summary.days.length === 1 ? 150 : 4 + i * 292 / (summary.days.length - 1), y: 78 - day[agent].cost / maximum * 70 }));
      svg.append(svgEl("line", { x1: 4, x2: 296, y1: 78, y2: 78, class: "spark-baseline" }));
      svg.append(areaSeries(svg, points, agent, `spark-${agent}`, 78));
      svg.append(svgEl("circle", { cx: points.at(-1).x, cy: points.at(-1).y, r: 3, fill: `var(--${agent})` }));
      $(agent + "-sparkline").replaceChildren(svg);
    }
  }
  function drawChart() {
    if (!summary) return;
    const metric = $("metric").value, cumulative = $("view").value === "cumulative";
    const points = seriesFor(summary.days, metric, cumulative);
    const width = Math.max(280, $("trend-chart").clientWidth), height = 300;
    const left = 62, right = 12, top = 16, bottom = 34, innerWidth = width - left - right, innerHeight = height - top - bottom;
    const step = innerWidth / points.length;
    const maximum = Math.max(1, ...points.flatMap(point => AGENTS.map(agent => point[agent]))) * 1.08;
    const x = i => left + step * (i + 0.5), y = value => top + innerHeight * (1 - value / maximum);
    const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", tabindex: "0", "aria-label": `${cumulative ? "累计" : summary.intraday ? "每15分钟" : "每日"}${metric === "cost" ? "等效费用" : "Token"}趋势；左右方向键选择时段，详细数值也列于下方表格。` });
    for (let i = 0; i <= 4; i++) {
      const value = maximum * i / 4, gy = y(value);
      svg.append(svgEl("line", { x1: left, x2: width - right, y1: gy, y2: gy, class: "grid" }));
      svg.append(svgEl("text", { x: left - 10, y: gy + 4, "text-anchor": "end" }, (metric === "cost" ? "$" : "") + compact(value)));
    }
    const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(innerWidth / (summary.intraday ? 115 : 75)))));
    points.forEach((point, i) => {
      if (i % labelEvery === 0) svg.append(svgEl("text", { x: x(i), y: height - 8, "text-anchor": "middle" }, summary.intraday ? localTime(point.date).slice(5) : point.date.slice(5)));
    });
    const lines = AGENTS.map(agent => areaSeries(svg, points.map((point, i) => ({ x: x(i), y: y(point[agent]) })), agent, `trend-${agent}`, top + innerHeight));
    svg.append(...lines);
    const marker = svgEl("line", { x1: x(points.length - 1), x2: x(points.length - 1), y1: top, y2: top + innerHeight, class: "selection" });
    svg.append(marker);
    const dots = AGENTS.map(agent => svgEl("circle", { r: 4.5, fill: `var(--${agent})`, stroke: "var(--surface)", "stroke-width": 2 }));
    svg.append(...dots);
    let selected = points.length - 1;
    function select(index) {
      selected = Math.max(0, Math.min(points.length - 1, index));
      marker.setAttribute("x1", x(selected)); marker.setAttribute("x2", x(selected));
      const point = points[selected], format = metric === "cost" ? money : integer;
      dots.forEach((dot, i) => { dot.setAttribute("cx", x(selected)); dot.setAttribute("cy", y(point[AGENTS[i]])); });
      $("chart-detail").textContent = `${periodLabel(point)}${cumulative ? " · 区间累计" : ""}　Codex ${format(point.codex)}　Claude ${format(point.claude)}${metric === "tokens" ? " tokens" : ""}`;
    }
    svg.addEventListener("pointermove", event => { const rect = svg.getBoundingClientRect(); select(Math.floor(((event.clientX - rect.left) / rect.width * width - left) / step)); });
    svg.addEventListener("pointerdown", event => { const rect = svg.getBoundingClientRect(); select(Math.floor(((event.clientX - rect.left) / rect.width * width - left) / step)); });
    svg.addEventListener("keydown", event => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); select(event.key === "Home" ? 0 : event.key === "End" ? points.length - 1 : selected + (event.key === "ArrowRight" ? 1 : -1)); } });
    $("trend-chart").replaceChildren(svg); select(selected);
  }
  function renderStatus() {
    const stamp = status?.collectedAt || payload?.collectedAt;
    const time = stamp ? new Date(stamp).toLocaleString("zh-CN", { timeZone: payload?.timezone || "Asia/Shanghai", hour12: false }) : "尚无成功采集";
    $("status").textContent = offline ? `连接已断开 · 保留当前画面 · ${time}` : `${status?.refreshing ? "正在采集" : status?.error ? "采集失败，保留上次结果" : "最近更新"} · ${time}`;
    $("refresh").disabled = Boolean(status?.refreshing) && !offline;
    $("refresh").textContent = status?.refreshing && !offline ? "采集中…" : "刷新数据";
    const interval = Math.round((status?.refreshSeconds || 900) / 60);
    $("next-refresh").textContent = `每 ${interval} 分钟自动采集 · 关闭网页仍会继续`;
    $("alerts").replaceChildren();
    const messages = (payload?.warnings || []).map(warning => warning.message);
    if (status?.error) messages.unshift("最近一次采集失败：" + status.error);
    if (offline) messages.unshift("无法连接开发机。请检查 SSH 隧道和开发机是否在线；恢复后页面会自动重连。");
    if (stamp && Date.now() - Date.parse(stamp) > (status?.refreshSeconds || 900) * 2000) messages.unshift("当前展示的是旧快照，请注意最近更新时间。");
    messages.forEach(message => $("alerts").append(element("div", message, "alert")));
  }
  async function getJSON(path, options = {}) {
    const response = await fetch(path, { cache: "no-store", signal: AbortSignal.timeout(10000), ...options });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  }
  async function poll() {
    if (pollRunning) return;
    pollRunning = true;
    try {
      status = await getJSON("/api/status"); offline = false;
      if (status.collectedAt && payload?.collectedAt !== status.collectedAt) {
        payload = await getJSON("/api/usage");
        for (const id of ["since", "until"]) { $(id).min = payload.since; $(id).max = payload.until; }
        if (preset !== "custom") setPreset(preset); else render();
      }
    } catch (error) { offline = true; }
    finally { renderStatus(); pollRunning = false; }
  }
  $("presets").addEventListener("click", event => { if (event.target.dataset.preset) setPreset(event.target.dataset.preset); });
  $("apply").addEventListener("click", () => { try { selectRange($("since").value, $("until").value); } catch (error) { showRangeError(error.message); } });
  for (const id of ["metric", "view"]) $(id).addEventListener("change", drawChart);
  $("refresh").addEventListener("click", async () => {
    $("refresh").disabled = true;
    try {
      status = await getJSON("/api/status");
      await getJSON("/api/refresh", { method: "POST", headers: { "X-Refresh-Token": status.csrfToken } });
      status.refreshing = true; offline = false;
    } catch (error) { showRangeError("刷新请求失败：" + error.message); }
    renderStatus(); setTimeout(poll, 1500);
  });
  $("export").addEventListener("click", () => {
    if (!summary) return;
    const url = URL.createObjectURL(new Blob(["\ufeff", csvFor(summary.days)], { type: "text/csv;charset=utf-8" }));
    const a = element("a"); a.href = url; a.download = `usage-${summary.intraday ? "last24-15min-" : ""}${$("since").value}-${$("until").value}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  function setTheme(theme) { document.documentElement.dataset.theme = theme; $("theme").textContent = theme === "dark" ? "浅色模式" : "深色模式"; }
  let savedTheme;
  try { savedTheme = localStorage.getItem("usage-theme"); } catch (_) { /* storage may be disabled */ }
  setTheme(savedTheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  $("theme").addEventListener("click", () => { const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; setTheme(theme); try { localStorage.setItem("usage-theme", theme); } catch (_) { /* optional preference */ } });
  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawChart, 150); });
  // Optional page-scoped WebMCP interface; identical validation and state to Apply.
  if (document.modelContext?.registerTool) {
    const lifecycle = new AbortController();
    try {
      Promise.resolve(document.modelContext.registerTool({
        name: "set_usage_date_range",
        title: "筛选用量日期",
        description: "Set the visible usage date range, then return the displayed Codex and Claude totals. Does not rescan logs or change saved data.",
        inputSchema: { type: "object", properties: { since: { type: "string", description: "Start date, YYYY-MM-DD" }, until: { type: "string", description: "End date, YYYY-MM-DD" } }, required: ["since", "until"], additionalProperties: false },
        annotations: { readOnlyHint: false, untrustedContentHint: false },
        execute(input) {
          if (!input || typeof input.since !== "string" || typeof input.until !== "string" || Object.keys(input).some(key => !["since", "until"].includes(key))) throw new Error("Expected only since and until date strings.");
          return selectRange(input.since, input.until);
        }
      }, { signal: lifecycle.signal })).catch(error => console.warn("Optional WebMCP registration failed", error));
    } catch (error) { console.warn("Optional WebMCP unavailable", error); }
    window.addEventListener("pagehide", () => lifecycle.abort(), { once: true });
  }
  poll(); setInterval(poll, 10000);
}
