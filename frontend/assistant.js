// BranchFlow Assist – voice and chat assistant. Shares api/esc/$/toast with app.js.
const IDLE_MS = 60000;      // no touch for this long: ask if the customer is still there
const IDLE_GRACE_MS = 20000; // then end the session, so the next customer sees nothing
const BALANCE_SHOW_MS = 20000;
const RESET_AFTER_END_MS = 4000;

const AS = { meta: null, lang: "en", session: null, voice: true, listening: false, rec: null, idle: null, grace: null, hideBalance: null, keypad: null, requests: null };
const ui = (k) => AS.meta.ui[AS.lang][k];
const bcp47 = () => AS.meta.languages.find((l) => l.code === AS.lang).bcp47;
const rupees = (n) => `₹${Math.abs(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

async function initAssist() {
  AS.meta = await api("/assist/meta");
  $("#asLangGrid").innerHTML = AS.meta.languages.map((l) => `<button type="button" class="lang-btn" data-lang="${l.code}" lang="${l.code}">${esc(l.name)}</button>`).join("");
  $("#asLangRow").innerHTML = AS.meta.languages.map((l) => `<button type="button" data-lang="${l.code}" lang="${l.code}">${esc(l.name)}</button>`).join("");
  $("#asChoose").textContent = AS.meta.ui.en.choose_lang;
  $("#asChooseAlt").innerHTML = AS.meta.languages.slice(1, 4).map((l) => `<span lang="${l.code}">${esc(AS.meta.ui[l.code].choose_lang)}</span>`).join(" · ");
  if (!(window.SpeechRecognition || window.webkitSpeechRecognition)) $("#asMic").dataset.off = "1";
  if ("speechSynthesis" in window) speechSynthesis.getVoices();
}

// ---------------------------------------------------------------- conversation
async function startSession(lang) {
  AS.lang = lang;
  const r = await api("/assist/start", { method: "POST", body: { lang } });
  AS.session = r.session;
  $("#asStart").hidden = true;
  $("#asChat").hidden = false;
  $("#asLog").innerHTML = "";
  renderSide({ type: "clear" });
  applyReply(r);
  resetIdle();
}

async function send(body) {
  if (!AS.session) return;
  stopSpeech();
  try {
    applyReply(await api("/assist/message", { method: "POST", body: { session: AS.session, lang: AS.lang, ...body } }));
  } catch (err) { toast(err.message, true); }
}

function applyReply(r) {
  AS.lang = r.lang;
  applyLabels();
  r.messages.forEach((m) => addBubble(m.text, "bot"));
  $("#asActions").innerHTML = (r.actions || []).map((a) => `<button type="button" class="quick" data-action="${esc(a.action)}" lang="${AS.lang}">${esc(a.label)}</button>`).join("");
  if (r.side) renderSide(r.side);
  else if (r.input === "text" && AS.keypad) renderSide({ type: "clear" });
  $("#asForm").classList.toggle("off", r.input === "keypad");
  speak(r.messages.map((m) => m.text).join(" "));
  if (r.end || r.expired) setTimeout(resetAssist, RESET_AFTER_END_MS);
}

function addBubble(text, who) {
  const b = document.createElement("div");
  b.className = `bubble ${who}`;
  b.lang = AS.lang;
  b.textContent = text;
  const log = $("#asLog");
  log.appendChild(b);
  log.scrollTop = log.scrollHeight;
}

function applyLabels() {
  $("#asInput").placeholder = ui("type");
  $("#asSend").textContent = ui("send");
  $("#asMicLabel").textContent = AS.listening ? ui("listening") : ui("speak");
  $("#asVoice").textContent = AS.voice ? ui("voice_on") : ui("voice_off");
  $("#asEnd").textContent = ui("end");
  $("#asIdleMsg").textContent = ui("still_there");
  $("#asIdleBtn").textContent = ui("continue");
  document.querySelectorAll("#asLangRow button").forEach((b) => b.classList.toggle("active", b.dataset.lang === AS.lang));
  $("#asMic").disabled = !!$("#asMic").dataset.off;
  $("#asNote").textContent = $("#asMic").dataset.off ? ui("no_mic") : "";
}

function resetAssist() {
  stopSpeech();
  stopListening();
  clearTimeout(AS.idle); clearTimeout(AS.grace); clearTimeout(AS.hideBalance);
  AS.session = null;
  AS.keypad = null;
  AS.lang = "en";
  $("#asLog").innerHTML = "";
  $("#asActions").innerHTML = "";
  $("#asInput").value = "";
  $("#asSide").innerHTML = "";
  $("#asIdle").hidden = true;
  $("#asChat").hidden = true;
  $("#asStart").hidden = false;
}

async function endSession() {
  const sid = AS.session;
  if (!sid) return;
  stopSpeech();
  const r = await api("/assist/end", { method: "POST", body: { session: sid } }).catch(() => null);
  if (r && r.messages) applyReply(r); else resetAssist();
}

// ---------------------------------------------------------------- side panel
function renderSide(side) {
  clearTimeout(AS.hideBalance);
  AS.keypad = null;
  const el = $("#asSide");
  if (side.type !== "clear") el.scrollIntoView({ behavior: "smooth", block: "nearest" }); // phones stack this below the chat
  if (side.type === "keypad") return renderKeypad(side);
  if (side.type === "token") {
    el.innerHTML = `<div class="ticket" lang="${AS.lang}">
      <div class="t-label">${esc(ui("your_token"))}</div>
      <div class="t-no">${esc(side.token)}</div>
      <div class="t-counter"><b>${esc(side.counter)}</b></div>
      <div class="muted small">${esc(side.service)}${side.ref ? ` · ${esc(side.ref)}` : ""}</div>
      <div class="t-grid">
        <div><span class="muted small">${esc(ui("wait"))}</span><b>${side.wait} ${esc(ui("min"))}</b></div>
        <div><span class="muted small">${esc(ui("ahead"))}</span><b>${side.ahead}</b></div>
      </div>
    </div>`;
  } else if (side.type === "request") {
    el.innerHTML = `<div class="ticket" lang="${AS.lang}"><div class="t-label">${esc(ui("ref"))}</div><div class="t-no sm">${esc(side.ref)}</div><div class="t-counter">${esc(side.item)}</div></div>`;
  } else if (side.type === "review") {
    el.innerHTML = `<div class="review" lang="${AS.lang}"><h2>${esc(side.title)}</h2>
      ${side.rows.map((r) => `<div class="review-row"><div><span class="muted small">${esc(r.label)}</span><b>${esc(r.value)}</b></div><button type="button" class="btn sm ghost" data-action="edit:${esc(r.key)}">${esc(side.change)}</button></div>`).join("")}</div>`;
  } else if (side.type === "balance") {
    el.innerHTML = `<div class="balance-cover" lang="${AS.lang}">
      ${side.accounts.map((a) => `<div class="muted">${esc(a.label)}</div>`).join("")}
      <button type="button" class="btn primary wide" id="asReveal">${esc(ui("view_balance"))}</button></div>`;
    AS.balanceSide = side;
  } else {
    el.innerHTML = `<div class="ticket-empty" lang="${AS.lang}">${esc(ui("side_hint"))}</div>`;
  }
}

async function revealBalance() {
  const r = await api("/assist/reveal", { method: "POST", body: { session: AS.session, lang: AS.lang } }).catch(() => null);
  if (!r || !r.accounts) return renderSide({ type: "clear" });
  $("#asSide").innerHTML = `<div class="review" lang="${AS.lang}">
    ${r.accounts.map((a) => `<div class="balance-acct"><span class="muted small">${esc(a.label)}</span><div class="balance-no">${rupees(a.balance)}</div>
      <div class="muted small">${esc(ui("recent"))}</div>
      ${a.txns.map((t) => `<div class="txn"><span>${esc(t.date.slice(8))}/${esc(t.date.slice(5, 7))} · ${esc(t.label)}</span><b class="${t.amount < 0 ? "" : "credit"}">${t.amount < 0 ? "−" : "+"}${rupees(t.amount)}</b></div>`).join("")}
    </div>`).join("")}
    <button type="button" class="btn wide" id="asHide">${esc(ui("hide"))}</button></div>`;
  AS.hideBalance = setTimeout(hideBalance, BALANCE_SHOW_MS);
}
function hideBalance() { renderSide(AS.balanceSide); }

// Mobile numbers and codes are typed here and never enter the chat log.
function renderKeypad(side) {
  AS.keypad = { mode: side.mode, value: "", max: side.mode === "mobile" ? 10 : 6, demo: side.demo_code };
  drawKeypad();
}
function drawKeypad() {
  const k = AS.keypad;
  const shown = k.mode === "otp" ? "•".repeat(k.value.length) : k.value;
  const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "clear", "0", "ok"];
  $("#asSide").innerHTML = `<div class="keypad" lang="${AS.lang}">
    <div class="lbl">${esc(ui(k.mode === "mobile" ? "keypad_mobile" : "keypad_otp"))}</div>
    <div class="key-display">${esc(shown) || "&nbsp;"}</div>
    ${k.demo ? `<div class="alert info">${esc(ui("demo_code"))}: <b>${esc(k.demo)}</b></div>` : ""}
    <div class="key-grid">${keys.map((x) => x === "ok"
      ? `<button type="button" class="btn primary key" data-key="ok" ${k.value.length ? "" : "disabled"}>${esc(ui("ok"))}</button>`
      : x === "clear" ? `<button type="button" class="btn key" data-key="clear">${esc(ui("clear"))}</button>`
      : `<button type="button" class="btn key" data-key="${x}">${x}</button>`).join("")}</div></div>`;
}
async function pressKey(x) {
  const k = AS.keypad;
  if (!k) return;
  if (x === "clear") k.value = "";
  else if (x !== "ok") { if (k.value.length < k.max) k.value += x; }
  else {
    const value = k.value;
    k.value = "";
    try { applyReply(await api("/assist/secure", { method: "POST", body: { session: AS.session, value, lang: AS.lang } })); }
    catch (err) { toast(err.message, true); }
    return;
  }
  drawKeypad();
}

// ---------------------------------------------------------------- voice
function speak(text) {
  if (!AS.voice || !text || !("speechSynthesis" in window)) return;
  const voices = speechSynthesis.getVoices();
  const norm = (v) => v.lang.replace("_", "-").toLowerCase();
  const voice = voices.find((v) => norm(v) === bcp47().toLowerCase()) || voices.find((v) => norm(v).split("-")[0] === AS.lang);
  if (!voice) {
    // Never read a regional language with a mismatched voice: it would be unintelligible.
    if (speechSynthesis.getVoices().length) $("#asNote").textContent = ui("no_voice");
    return;
  }
  const u = new SpeechSynthesisUtterance(text);
  u.voice = voice;
  u.lang = voice.lang;
  speechSynthesis.speak(u);
}
function stopSpeech() { if ("speechSynthesis" in window) speechSynthesis.cancel(); }

function toggleListening() {
  if (AS.listening) return stopListening();
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return;
  stopSpeech();
  const rec = new SR();
  rec.lang = bcp47();
  rec.interimResults = true;
  rec.continuous = false;
  rec.onresult = (e) => {
    const last = e.results[e.results.length - 1];
    $("#asInput").value = last[0].transcript;
    if (last.isFinal) { const text = $("#asInput").value.trim(); $("#asInput").value = ""; if (text) submitText(text); }
  };
  rec.onerror = (e) => { if (e.error !== "no-speech" && e.error !== "aborted") $("#asNote").textContent = ui("no_mic"); };
  rec.onend = () => { AS.listening = false; $("#asMic").classList.remove("on"); $("#asMicLabel").textContent = ui("speak"); };
  AS.rec = rec;
  AS.listening = true;
  $("#asMic").classList.add("on");
  $("#asMicLabel").textContent = ui("listening");
  rec.start();
}
function stopListening() { if (AS.rec) AS.rec.abort(); }

function submitText(text) {
  addBubble(text, "me");
  send({ text });
}

// ---------------------------------------------------------------- idle timeout
function resetIdle() {
  clearTimeout(AS.idle); clearTimeout(AS.grace);
  $("#asIdle").hidden = true;
  if (!AS.session) return;
  AS.idle = setTimeout(() => {
    $("#asIdle").hidden = false;
    AS.grace = setTimeout(endSession, IDLE_GRACE_MS);
  }, IDLE_MS);
}
["pointerdown", "keydown"].forEach((ev) => document.addEventListener(ev, resetIdle));

// ---------------------------------------------------------------- events
$("#asLangGrid").addEventListener("click", (e) => {
  const b = e.target.closest("[data-lang]");
  if (b) startSession(b.dataset.lang);
});
$("#asLangRow").addEventListener("click", (e) => {
  const b = e.target.closest("[data-lang]");
  if (b && b.dataset.lang !== AS.lang) send({ action: "lang:" + b.dataset.lang });
});
$("#view-assistant").addEventListener("click", (e) => {
  const a = e.target.closest("[data-action]");
  if (a) {
    if (a.classList.contains("quick")) addBubble(a.textContent, "me");
    return send({ action: a.dataset.action });
  }
  const key = e.target.closest("[data-key]");
  if (key) return pressKey(key.dataset.key);
  if (e.target.id === "asReveal") return revealBalance();
  if (e.target.id === "asHide") { clearTimeout(AS.hideBalance); hideBalance(); }
});
$("#asForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("#asInput").value.trim();
  if (!text) return;
  $("#asInput").value = "";
  submitText(text);
});
$("#asMic").onclick = toggleListening;
$("#asEnd").onclick = endSession;
$("#asIdleBtn").onclick = resetIdle;
$("#asVoice").onclick = () => {
  AS.voice = !AS.voice;
  if (!AS.voice) stopSpeech();
  applyLabels();
};

// ---------------------------------------------------------------- staff: requests
async function refreshRequests() {
  if ($("#requestTable select:focus")) return;
  const d = await api("/requests");
  const s = d.stats;
  const kpis = [
    { label: "Assistant sessions", value: s.sessions, sub: `${s.active} active now` },
    { label: "Open items", value: d.records.filter((r) => r.status !== "done").length, sub: "Requests and forms" },
    { label: "Urgent", value: d.records.filter((r) => r.urgent && r.status !== "done").length, sub: "Lost card blocks", cls: d.records.some((r) => r.urgent && r.status !== "done") ? "bad" : "" },
    { label: "Not understood", value: s.unresolved, sub: "Messages with no match" },
    { label: "Languages used", value: s.languages.length, sub: "Since the server started" },
    { label: "Intent model", value: d.claude ? "Claude" : "Keywords", sub: d.demo ? "Demo mode on" : "Live mode" },
  ];
  $("#reqKpis").innerHTML = kpis.map((x) => `<div class="kpi ${x.cls || ""}"><div class="label">${x.label}</div><div class="value">${x.value}</div><div class="sub">${x.sub}</div></div>`).join("");
  $("#requestTable tbody").innerHTML = d.records.map((r) => `<tr>
    <td class="tok">${esc(r.ref)}</td>
    <td>${esc(r.name)}${r.urgent ? ' <span class="chip late">Urgent</span>' : ""}</td>
    <td><span class="chip">${esc(r.language)}</span></td>
    <td class="small">${r.details.map((x) => `<div><span class="muted">${esc(x.label)}:</span> ${esc(x.value)}</div>`).join("") || '<span class="muted">—</span>'}</td>
    <td>${esc(r.token) || '<span class="muted">—</span>'}</td>
    <td>${esc(r.created)}</td>
    <td><select data-ref="${esc(r.ref)}">${["open", "progress", "done"].map((st) => `<option value="${st}" ${st === r.status ? "selected" : ""}>${{ open: "Open", progress: "In progress", done: "Done" }[st]}</option>`).join("")}</select></td>
  </tr>`).join("") || `<tr><td colspan="7" class="empty">No requests yet. They appear here when customers use the assistant.</td></tr>`;
  const max = Math.max(...s.languages.map((l) => l.count), 1);
  $("#langMix").innerHTML = s.languages.map((l) => `<div class="mix-row"><span>${esc(l.name)}</span><div class="bar"><i style="width:${(l.count / max) * 100}%"></i></div><b>${l.count}</b></div>`).join("") || `<div class="muted">No sessions yet.</div>`;
}
$("#requestTable").addEventListener("change", async (e) => {
  if (!e.target.dataset.ref) return;
  try {
    await api(`/requests/${e.target.dataset.ref}`, { method: "POST", body: { status: e.target.value } });
    toast(`${e.target.dataset.ref} updated`);
    e.target.blur();
    refreshRequests();
  } catch (err) { toast(err.message, true); }
});

initAssist();
