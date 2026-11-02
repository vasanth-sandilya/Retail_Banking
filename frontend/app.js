// BranchFlow front end – talks to the Python API in backend/server.py
const API = "/api";
let META = null;
let STATE = null;
let currentView = "dashboard";
let selectedService = null;
let fcStart = mondayOf(new Date());
let fcData = null;
let fcSelectedDay = null;

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), 2600);
}

function mondayOf(d) {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const wd = (x.getDay() + 6) % 7;
  x.setDate(x.getDate() - wd);
  return x;
}
const isoDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const hourLabel = (h) => `${h > 12 ? h - 12 : h}${h >= 12 ? "pm" : "am"}`;

// ---------------------------------------------------------------- navigation
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-view]");
  if (!b) return;
  showView(b.dataset.view);
});
function showView(v) {
  currentView = v;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  document.querySelectorAll(".view").forEach((s) => s.classList.toggle("active", s.id === "view-" + v));
  if (v === "forecast" && !fcData) loadForecast();
  refresh();
}

// ---------------------------------------------------------------- polling
async function refresh() {
  try {
    if (currentView === "assistant") return;
    if (currentView === "requests") {
      await refreshRequests();
      return;
    }
    if (currentView === "display") {
      renderBoard(await api("/display"));
      return;
    }
    STATE = await api("/state");
    if (currentView === "dashboard") renderDashboard();
    // don't re-render while staff are picking from a transfer dropdown
    if (currentView === "counters" && !$("#counterGrid select:focus")) renderCounters();
  } catch (err) {
    console.error(err);
  }
}
setInterval(refresh, 3000);
setInterval(() => ($("#clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })), 1000);

// ---------------------------------------------------------------- dashboard
function renderDashboard() {
  const k = STATE.kpi;
  const sla = META.sla_minutes;
  const kpis = [
    { label: "Waiting now", value: k.waiting, sub: `${k.serving} being served`, cls: k.waiting > 20 ? "warn" : "" },
    { label: "Avg wait", value: `${k.avg_wait}m`, sub: `Target ≤ ${sla} min`, cls: k.avg_wait > sla ? "bad" : k.avg_wait > sla * 0.7 ? "warn" : "" },
    { label: "Served today", value: k.served, sub: `${k.no_show} no-show` },
    { label: "Tokens issued", value: k.issued_total, sub: `${k.issued_this_hour} this hour` },
    { label: "SLA breaches", value: k.sla_breaches, sub: `Waits over ${sla} min`, cls: k.sla_breaches ? "bad" : "" },
    { label: "Open counters", value: `${k.open_counters}/${STATE.counters.length}`, sub: "Staffed & available" },
  ];
  $("#kpis").innerHTML = kpis
    .map((x) => `<div class="kpi ${x.cls || ""}"><div class="label">${x.label}</div><div class="value">${x.value}</div><div class="sub">${x.sub}</div></div>`)
    .join("");

  $("#alerts").innerHTML = STATE.alerts.map((a) => `<div class="alert ${a.level}">${esc(a.msg)}</div>`).join("");

  const rows = STATE.waiting.map((t) => {
    const pri = t.segment !== "regular";
    const late = t.waited > sla;
    return `<tr>
      <td class="tok">${t.token}</td>
      <td>${esc(t.name) || '<span class="muted">—</span>'}</td>
      <td><span class="cat cat-${t.category}"></span>${esc(t.service_name)}${t.ref ? ` <span class="chip">${esc(t.ref)}</span>` : ""}</td>
      <td><span class="chip ${pri ? "pri" : ""}">${esc(t.segment_name)}</span>${t.language ? ` <span class="chip">${esc(t.language)}</span>` : ""}</td>
      <td>${esc(t.counter_name)}</td>
      <td><span class="chip ${late ? "late" : ""}">${Math.round(t.waited)} min</span></td>
    </tr>`;
  });
  $("#waitingTable tbody").innerHTML = rows.join("") || `<tr><td colspan="6" class="empty">No customers waiting. Issue a token or load demo customers.</td></tr>`;

  const fc = k.forecast_this_hour;
  const max = Math.max(fc.high, k.issued_this_hour, 1);
  const pct = Math.min(100, (k.issued_this_hour / max) * 100);
  const mark = (fc.expected / max) * 100;
  $("#hourGauge").innerHTML = fc.expected
    ? `<div class="gauge-num"><b>${k.issued_this_hour}</b><span class="muted">arrivals so far · forecast ${fc.expected} (range ${fc.low}–${fc.high})</span></div>
       <div class="bar"><i style="width:${pct}%"></i><em style="left:${mark}%"></em></div>
       <div class="muted small">Marker shows the expected total for this hour.</div>`
    : `<div class="muted">Branch is outside business hours (${hourLabel(META.hours[0])}–${hourLabel(META.hours.at(-1) + 1)}). No forecast for this hour.</div>`;

  $("#eventLog").innerHTML = STATE.events.map((e) => `<li><span>${e.time}</span>${esc(e.msg)}</li>`).join("") || `<li class="muted">No activity yet.</li>`;
}

$("#btnDemo").onclick = async () => {
  try { await api("/demo", { method: "POST" }); toast("Demo customers added"); refresh(); }
  catch (e) { toast(e.message, true); }
};
$("#btnReset").onclick = async () => {
  if (!confirm("Clear all tokens and counter activity for today?")) return;
  await api("/reset", { method: "POST" });
  toast("Day reset");
  refresh();
};

// ---------------------------------------------------------------- kiosk
function renderKiosk() {
  $("#serviceGrid").innerHTML = Object.entries(META.services)
    .filter(([key]) => key !== "general_help")
    .map(([key, s]) => `<button type="button" class="svc" data-key="${key}"><span class="n"><span class="cat cat-${s.category}"></span>${esc(s.name)}</span><span class="m">~${s.avg_minutes} min · ${s.category.toLowerCase()}</span></button>`)
    .join("");
  $("#segment").innerHTML = Object.entries(META.segments).filter(([k]) => k !== "urgent").map(([k, s]) => `<option value="${k}">${esc(s.name)}</option>`).join("");
}
$("#serviceGrid").addEventListener("click", (e) => {
  const b = e.target.closest(".svc");
  if (!b) return;
  selectedService = b.dataset.key;
  document.querySelectorAll(".svc").forEach((x) => x.classList.toggle("selected", x === b));
  $("#tokenErr").textContent = "";
});
$("#tokenForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedService) { $("#tokenErr").textContent = "Please choose a service."; return; }
  try {
    const t = await api("/tokens", {
      method: "POST",
      body: { service: selectedService, segment: $("#segment").value, name: $("#custName").value, mobile: $("#mobile").value },
    });
    $("#tokenErr").textContent = "";
    renderTicket(t);
    $("#ticket").scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("#custName").value = ""; $("#mobile").value = "";
    toast(`Token ${t.token} issued`);
  } catch (err) {
    $("#tokenErr").textContent = err.message;
  }
});
function renderTicket(t) {
  $("#ticket").innerHTML = `<div class="ticket">
    <div class="t-label">Your token</div>
    <div class="t-no">${t.token}</div>
    <div class="t-counter">Please proceed to <b>${esc(t.counter_name)}</b></div>
    <div class="muted small">${esc(t.service_name)} · ${esc(t.segment_name)}${t.name ? " · " + esc(t.name) : ""}</div>
    <div class="t-grid">
      <div><span class="muted small">Estimated wait</span><b>${t.est_wait} min</b></div>
      <div><span class="muted small">Ahead of you</span><b>${t.ahead}</b></div>
    </div>
    <div class="dash"></div>
    <details><summary>Why this counter?</summary><ul>${t.routing.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></details>
    <p class="muted small">Issued ${t.issued_at.slice(11, 16)}${t.mobile ? ` · SMS alert to ••••${esc(t.mobile)}` : ""}</p>
  </div>`;
}

// ---------------------------------------------------------------- counters
function renderCounters() {
  const svcOptions = Object.entries(META.services).map(([k, s]) => `<option value="${k}">${esc(s.name)}</option>`).join("");
  $("#counterGrid").innerHTML = STATE.counters.map((c) => {
    const cur = c.current_token;
    const open = c.status === "open";
    return `<div class="counter ${c.status}" data-id="${c.id}">
      <div class="counter-top">
        <div><div class="counter-name">${esc(c.name)}</div><div class="counter-role">${esc(c.role)} · ${esc(c.staff)}</div></div>
        <span class="status ${c.status}">${c.status}</span>
      </div>
      <div class="serving-box">
        ${cur
          ? `<div class="muted small">Now serving</div><div class="tok">${cur.token}</div><div class="small">${esc(cur.service_name)}${cur.name ? " · " + esc(cur.name) : ""} <span class="chip ${cur.segment !== "regular" ? "pri" : ""}">${esc(cur.segment_name)}</span>${cur.language ? ` <span class="chip">${esc(cur.language)}</span>` : ""}${cur.ref ? ` <span class="chip">${esc(cur.ref)}</span>` : ""}</div>`
          : `<div class="muted small">${open ? "Idle — ready for next customer" : "Not accepting customers"}</div>`}
      </div>
      <div class="counter-meta"><span>Queue: <b>${c.queue_len}</b></span><span>Backlog: <b>${c.backlog} min</b></span><span>Served: <b>${c.served}</b></span></div>
      <div class="queue-line">${c.queue.length ? "Next: " + c.queue.slice(0, 5).join(", ") + (c.queue.length > 5 ? "…" : "") : "Skills: " + c.skills.map((s) => s.toLowerCase()).join(", ")}</div>
      <div class="counter-btns">
        <button class="btn sm primary" data-act="next" ${open ? "" : "disabled"}>${cur ? "Complete & next" : "Call next"}</button>
        ${cur ? `<button class="btn sm" data-act="done">Complete</button><button class="btn sm" data-act="noshow">No-show</button>` : ""}
      </div>
      ${cur ? `<div class="counter-btns"><select data-role="xfer"><option value="">Transfer to service…</option>${svcOptions}</select></div>` : ""}
      <div class="counter-btns">
        ${["open", "break", "closed"].filter((s) => s !== c.status).map((s) => `<button class="btn sm ghost" data-act="status" data-status="${s}">${s === "open" ? "Open counter" : s === "break" ? "Take break" : "Close counter"}</button>`).join("")}
      </div>
    </div>`;
  }).join("");
}
$("#counterGrid").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const id = b.closest(".counter").dataset.id;
  try {
    if (b.dataset.act === "next") {
      const r = await api(`/counters/${id}/next`, { method: "POST" });
      toast(r.token ? `Calling ${r.token.token}` : "No eligible customers waiting");
    } else if (b.dataset.act === "done") {
      await api(`/counters/${id}/complete`, { method: "POST", body: { outcome: "served" } });
    } else if (b.dataset.act === "noshow") {
      await api(`/counters/${id}/complete`, { method: "POST", body: { outcome: "no_show" } });
    } else if (b.dataset.act === "status") {
      await api(`/counters/${id}/status`, { method: "POST", body: { status: b.dataset.status } });
    }
    refresh();
  } catch (err) { toast(err.message, true); }
});
$("#counterGrid").addEventListener("change", async (e) => {
  if (e.target.dataset.role !== "xfer" || !e.target.value) return;
  const id = Number(e.target.closest(".counter").dataset.id);
  const c = STATE.counters.find((x) => x.id === id);
  try {
    const t = await api("/transfer", { method: "POST", body: { token: c.current, service: e.target.value } });
    toast(`${t.token} transferred to ${t.counter_name}`);
    refresh();
  } catch (err) { toast(err.message, true); }
});

// ---------------------------------------------------------------- forecast
async function loadForecast() {
  fcData = await api(`/forecast?start=${isoDate(fcStart)}`);
  $("#fcMethod").textContent = `Model: ${fcData.method}. Recent trend factor ×${fcData.trend}.`;
  const today = isoDate(new Date());
  if (!fcSelectedDay || !fcData.week.some((d) => d.date === fcSelectedDay)) {
    const t = fcData.week.find((d) => d.date === today && d.open);
    fcSelectedDay = (t || fcData.week.find((d) => d.open)).date;
  }
  renderForecast();
}
function heatColor(v, max) {
  // light blue-grey to muted navy
  const t = Math.max(0, Math.min(1, v / max));
  const a = [236, 241, 246], b = [47, 79, 111];
  const c = a.map((x, i) => Math.round(x + (b[i] - x) * t));
  return { bg: `rgb(${c})`, fg: t > 0.55 ? "#fff" : "#1f2933" };
}
function renderForecast() {
  const hours = META.hours;
  const max = Math.max(...fcData.week.flatMap((d) => d.hours.map((h) => h.expected)), 1);
  const head = `<tr><th></th>${hours.map((h) => `<th>${hourLabel(h)}</th>`).join("")}<th>Total</th></tr>`;
  const body = fcData.week.map((d) => {
    const label = `${d.day} ${d.date.slice(8)}/${d.date.slice(5, 7)}${d.notes.length ? `<span class="note">${esc(d.notes[0].split(" (")[0])}</span>` : ""}`;
    const cells = d.open
      ? d.hours.map((h) => { const c = heatColor(h.expected, max); return `<td style="background:${c.bg};color:${c.fg}" title="${h.low}–${h.high} expected" data-date="${d.date}">${h.expected}</td>`; }).join("")
      : `<td class="closed" colspan="${hours.length}">Closed</td>`;
    return `<tr class="${d.date === fcSelectedDay ? "sel" : ""}"><th class="rowh">${label}</th>${cells}<td class="total">${d.open ? d.total : "—"}</td></tr>`;
  }).join("");
  $("#heatmap").innerHTML = head + body;
  const l1 = heatColor(0, 1).bg, l2 = heatColor(0.5, 1).bg, l3 = heatColor(1, 1).bg;
  $("#fcLegend").innerHTML = `Low <i style="background:${l1}"></i><i style="background:${l2}"></i><i style="background:${l3}"></i> High (peak ${max}/hr)`;

  const day = fcData.week.find((d) => d.date === fcSelectedDay);
  $("#dayChartTitle").textContent = `Hourly forecast — ${day.day} ${day.date}`;
  $("#dayChart").innerHTML = barChart(day.hours);
  const cats = ["CASH", "SERVICE", "LOAN", "WEALTH", "FOREX"];
  $("#staffTable").innerHTML = `<thead><tr><th>Hour</th><th>Expected</th>${cats.map((c) => `<th>${c[0] + c.slice(1).toLowerCase()}</th>`).join("")}</tr></thead><tbody>` +
    day.hours.map((h) => `<tr><td>${hourLabel(h.hour)}</td><td><b>${h.expected}</b></td>${cats.map((c) => `<td>${h.staffing[c]}</td>`).join("")}</tr>`).join("") + "</tbody>";
}
function barChart(hours) {
  const W = 560, H = 220, pad = { l: 34, r: 10, t: 10, b: 26 };
  const max = Math.max(...hours.map((h) => h.high), 1) * 1.1;
  const bw = (W - pad.l - pad.r) / hours.length;
  const y = (v) => pad.t + (H - pad.t - pad.b) * (1 - v / max);
  const now = new Date();
  const isToday = fcSelectedDay === isoDate(now);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Hourly footfall forecast">`;
  for (let i = 0; i <= 4; i++) {
    const v = Math.round((max / 4) * i);
    s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="#e2e5ea"/><text x="${pad.l - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
  }
  hours.forEach((h, i) => {
    const x = pad.l + i * bw + bw * 0.18, w = bw * 0.64, cx = x + w / 2;
    const cur = isToday && now.getHours() === h.hour;
    s += `<rect x="${x}" y="${y(h.expected)}" width="${w}" height="${y(0) - y(h.expected)}" rx="3" fill="${cur ? "#3f7a73" : "#2f4f6f"}" opacity="${cur ? 1 : 0.85}"><title>${hourLabel(h.hour)}: ${h.expected} expected (${h.low}–${h.high})</title></rect>`;
    s += `<line x1="${cx}" x2="${cx}" y1="${y(h.low)}" y2="${y(h.high)}" stroke="#a8742a" stroke-width="2"/>`;
    s += `<text x="${cx}" y="${H - 8}" text-anchor="middle">${hourLabel(h.hour)}</text>`;
  });
  return s + `</svg>`;
}
$("#heatmap").addEventListener("click", (e) => {
  const td = e.target.closest("td[data-date]");
  if (!td) return;
  fcSelectedDay = td.dataset.date;
  renderForecast();
});
$("#fcPrev").onclick = () => { fcStart.setDate(fcStart.getDate() - 7); loadForecast(); };
$("#fcNext").onclick = () => { fcStart.setDate(fcStart.getDate() + 7); loadForecast(); };
$("#fcToday").onclick = () => { fcStart = mondayOf(new Date()); fcSelectedDay = null; loadForecast(); };

// ---------------------------------------------------------------- display board
function renderBoard(d) {
  $("#boardServing").innerHTML = d.serving.map((x) => `<div class="board-row"><span class="bt">${x.token}</span><span class="bc">${esc(x.counter)}</span></div>`).join("") || `<p class="muted">No customers being served.</p>`;
  $("#boardNext").innerHTML = d.next.map((x) => `<div class="board-row"><span class="bt">${x.token}</span><span class="bc">${esc(x.counter)}</span></div>`).join("") || `<p class="muted">Queue is empty.</p>`;
}

// ---------------------------------------------------------------- init
(async function init() {
  META = await api("/meta");
  $("#branchName").textContent = META.branch;
  renderKiosk();
  // Customer-facing devices open /?mode=kiosk: assistant only, larger type, no staff tabs
  if (new URLSearchParams(location.search).get("mode") === "kiosk") {
    document.body.classList.add("kiosk");
    showView("assistant");
  }
  refresh();
})();
