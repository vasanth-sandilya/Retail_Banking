// Mitra appointments – staff calendar, no-show risk and overbooking. Shares api/esc/$/toast with app.js.
const AP = { date: isoDate(new Date()), data: null, selected: null };
const pct = (p) => `${Math.round(p * 100)}%`;
const niceDate = (iso) => new Date(`${iso}T00:00`).toLocaleDateString([], { weekday: "long", day: "numeric", month: "short" });
const LEVEL_NAMES = { cautious: "Cautious", balanced: "Balanced", aggressive: "Aggressive" };

async function refreshAppointments() {
  if ($("#apLevel:focus")) return;
  AP.data = await api(`/appointments?date=${AP.date}`);
  renderAppointments();
}

function renderAppointments() {
  const d = AP.data;
  const k = d.kpi;
  $("#apDate").textContent = niceDate(d.date);
  const kpis = [
    { label: "Booked", value: k.booked, sub: "Still to arrive" },
    { label: "Predicted no-shows", value: k.predicted_no_shows, sub: "Sum of risk scores" },
    { label: "Likely idle time", value: `${k.idle_minutes}m`, sub: "Across all advisors" },
    { label: "High risk", value: k.high_risk, sub: "30% or above", cls: k.high_risk ? "warn" : "" },
    { label: "Overbooked slots", value: k.overbooked_slots, sub: `${d.candidates.length} more possible` },
    { label: "Model AUC", value: d.model.auc, sub: "Synthetic history" },
  ];
  $("#apKpis").innerHTML = kpis.map((x) => `<div class="kpi ${x.cls || ""}"><div class="label">${x.label}</div><div class="value">${x.value}</div><div class="sub">${x.sub}</div></div>`).join("");
  renderGrid(d);
  renderDetail();
  $("#apCalls").innerHTML = d.call_list.slice(0, 6).map((a) => `<li><button type="button" class="link" data-ref="${a.ref}">${a.time} ${esc(a.name)}</button><span class="chip late">${pct(a.risk)}</span></li>`).join("") || `<li class="muted">No unconfirmed high-risk appointments.</li>`;
  $("#apEnabled").checked = d.policy.enabled;
  $("#apLevel").value = d.policy.level;
  $("#apLevel").disabled = !d.policy.enabled;
  $("#apModel").textContent = `${d.model.method}. Average no-show rate ${pct(d.model.base_rate)}.`;
}

function renderGrid(d) {
  const key = (advisor, time) => `${advisor}|${time}`;
  const cells = {};
  d.appointments.forEach((a) => (cells[key(a.advisor_id, a.time)] ||= []).push(a));
  const spare = new Set(d.candidates.map((c) => key(c.advisor_id, c.time)));
  const head = `<tr><th></th>${d.advisors.map((a) => `<th>${esc(a.name)}<span class="muted">${esc(a.role)} · ${esc(a.desk)}</span></th>`).join("")}</tr>`;
  const rows = d.times.map((t) => `<tr><th class="rowh">${t}</th>${d.advisors.map((adv) => {
    const list = cells[key(adv.id, t)] || [];
    const hint = spare.has(key(adv.id, t)) ? `<span class="ap-spare">Can overbook</span>` : "";
    return `<td class="${list.filter((a) => a.overbooked).length ? "doubled" : ""}">${list.map(pill).join("")}${hint}</td>`;
  }).join("")}</tr>`).join("");
  $("#apGrid").innerHTML = head + rows;
}

function pill(a) {
  const done = ["completed", "no_show"].includes(a.status);
  const state = a.status === "no_show" ? "No-show" : a.status === "completed" ? "Done" : a.status === "checked_in" ? "Checked in" : a.late ? "Late" : pct(a.risk);
  return `<button type="button" class="ap-pill ${done ? "past" : a.band} ${a.ref === AP.selected ? "sel" : ""}" data-ref="${a.ref}">
    <span>${esc(a.name)}</span><b>${state}</b></button>`;
}

function renderDetail() {
  const a = AP.data.appointments.find((x) => x.ref === AP.selected);
  if (!a) {
    $("#apDetail").innerHTML = `<div class="muted">Select an appointment to see why it is flagged and what to do next.</div>`;
    return;
  }
  const booked = a.status === "booked";
  const today = a.date === isoDate(new Date());
  const buttons = [
    booked && !a.confirmed ? ["confirm", "Mark confirmed", "primary"] : null,
    booked && today ? ["checkin", "Check in", "teal"] : null,
    a.status === "checked_in" ? ["complete", "Complete", "primary"] : null,
    booked && a.started ? ["no_show", "Mark no-show", ""] : null,
    booked ? ["cancel", "Cancel", "ghost"] : null,
  ].filter(Boolean);
  $("#apDetail").innerHTML = `
    <div class="ap-title"><b>${esc(a.name)}</b><span class="chip ${a.band === "high" ? "late" : ""}">${pct(a.risk)} no-show risk</span></div>
    <div class="muted small">${esc(a.ref)} · ${a.time} with ${esc(a.advisor)} · booked by ${esc(a.channel)}${a.language ? ` · ${esc(a.language)}` : ""}</div>
    <div class="ap-tags">
      ${a.segment !== "regular" ? `<span class="chip pri">${a.segment === "senior" ? "Senior citizen" : "Differently abled"}</span>` : ""}
      ${a.confirmed ? `<span class="chip pri">Confirmed</span>` : ""}
      ${a.overbooked ? `<span class="chip late">Overbooked slot</span>` : ""}
      ${a.token ? `<span class="chip">Token ${esc(a.token)}</span>` : ""}
    </div>
    ${a.reasons.length ? `<div class="muted small">What raises the risk</div><ul class="ap-reasons">${a.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
    <div class="counter-btns">${buttons.map(([act, label, cls]) => `<button type="button" class="btn sm ${cls}" data-act="${act}">${label}</button>`).join("")}</div>`;
}

async function loadSlots() {
  const params = new URLSearchParams({ type: $("#apType").value, date: AP.date, segment: $("#apSegment").value });
  const { slots } = await api(`/appointments/slots?${params}`);
  $("#apSlot").innerHTML = slots.map((s) => `<option value="${s.time}">${s.time} · ${esc(s.advisor)}${s.overbook ? " (overbooked)" : ""}</option>`).join("") || `<option value="">No slots this day</option>`;
}

function moveDay(days) {
  const d = new Date(`${AP.date}T00:00`);
  d.setDate(d.getDate() + days);
  goToDay(isoDate(d));
}
function goToDay(iso) {
  AP.date = iso;
  AP.selected = null;
  refreshAppointments();
  loadSlots();
}

$("#apPrev").onclick = () => moveDay(-1);
$("#apNext").onclick = () => moveDay(1);
$("#apToday").onclick = () => goToDay(isoDate(new Date()));
["#apType", "#apSegment"].forEach((sel) => $(sel).addEventListener("change", loadSlots));

$("#view-appointments").addEventListener("click", async (e) => {
  const open = e.target.closest("[data-ref]");
  if (open) {
    AP.selected = open.dataset.ref;
    renderGrid(AP.data);
    renderDetail();
    return;
  }
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  try {
    await api(`/appointments/${AP.selected}/${b.dataset.act}`, { method: "POST" });
    toast("Appointment updated");
    await refreshAppointments();
    loadSlots();
  } catch (err) { toast(err.message, true); }
});

$("#apForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const a = await api("/appointments", {
      method: "POST",
      body: { type: $("#apType").value, date: AP.date, time: $("#apSlot").value, name: $("#apName").value, mobile: $("#apMobile").value, segment: $("#apSegment").value, channel: $("#apChannel").value },
    });
    $("#apErr").textContent = "";
    $("#apName").value = ""; $("#apMobile").value = "";
    AP.selected = a.ref;
    toast(`Booked ${a.ref} with ${a.advisor}`);
    await refreshAppointments();
    loadSlots();
  } catch (err) { $("#apErr").textContent = err.message; }
});

async function savePolicy() {
  await api("/appointments/policy", { method: "POST", body: { enabled: $("#apEnabled").checked, level: $("#apLevel").value } });
  $("#apLevel").blur();
  await refreshAppointments();
  loadSlots();
}
$("#apEnabled").addEventListener("change", savePolicy);
$("#apLevel").addEventListener("change", savePolicy);
$("#apLevel").innerHTML = Object.entries(LEVEL_NAMES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
loadSlots();
