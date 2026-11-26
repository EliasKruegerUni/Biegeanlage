/* Biegbert HMI – Bedienoberfläche für die drei Betriebswege.
 *
 * Kommunikation: WebSocket /ws zum Python-Backend (Protokoll: docs/hmi-backend.md).
 * Das HMI entscheidet nichts: Jede Aktion wird als Kommando geschickt, die SPS prüft
 * und antwortet. Freigaben von Knöpfen hier sind nur Bedienhilfe.
 */
"use strict";
(() => {

// ── Hilfsfunktionen ───────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const qa = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));
const f = (v, d = 2) => (v === null || v === undefined || !isFinite(v)) ? "–" : (+v).toFixed(d);
const num = (id, def = 0) => { const v = parseFloat($(id).value); return isFinite(v) ? v : def; };
const D2R = Math.PI / 180;
function el(tag, attrs = {}, html = "") { const e = document.createElement(tag); Object.assign(e, attrs); if (html) e.innerHTML = html; return e; }
function toast(text, kind = "info", ms = 4500) {
  const t = el("div", { className: kind }, esc(text)); $("toast").appendChild(t);
  setTimeout(() => t.remove(), ms);
}
function download(name, data) {
  const a = el("a", { href: URL.createObjectURL(new Blob([data], { type: "application/json" })), download: name });
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

// ── Zustand ───────────────────────────────────────────────────────────
const S = {
  hello: null, st: null, alarms: { list: [] }, plans: [], plan: null, connected: false,
  geometry: { v_at_z2_zero_mm: 24 }, axes: {}, timing: { two_step_ms: 10000 },
  rtt: [], seq: [], seqState: [], seqMode: "all", seqRun: null,
};
const AXES = ["Z1", "Z2", "X", "A", "W"];
const AXIS_NAME = { Z1: "Vorschub", Z2: "Führung", X: "Matrizenauslenkung", A: "Matrizendrehung", W: "Rohrdrehung" };
const MODE_TEXT = { Automatic: "Automatik", Pause: "Pause", SingleAxis: "Einzelachsen", ReturnToBase: "Rückkehr Grundstellung" };

// ── WebSocket ─────────────────────────────────────────────────────────
let ws = null, reqId = 0, retry = 500;
const pending = new Map();
function wsUrl() {
  const p = new URLSearchParams(location.search).get("backend");
  if (p) return p;
  if (location.protocol.startsWith("http")) return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  return "ws://localhost:8765/ws";
}
function connect() {
  setPill("pBackend", "wait");
  try { ws = new WebSocket(wsUrl()); } catch (e) { scheduleReconnect(); return; }
  ws.onopen = () => { retry = 500; S.connected = true; setPill("pBackend", "ok"); clog({ level: "info", source: "HMI", text: `Verbunden mit ${wsUrl()}` }); };
  ws.onclose = () => {
    if (S.connected) clog({ level: "error", source: "HMI", text: "Verbindung zum Backend verloren" });
    S.connected = false; setPill("pBackend", ""); for (const [, p] of pending) p.reject(new Error("Verbindung getrennt"));
    pending.clear(); scheduleReconnect();
  };
  ws.onmessage = (ev) => { let m; try { m = JSON.parse(ev.data); } catch (_) { return; } onMessage(m); };
}
function scheduleReconnect() { setTimeout(connect, retry); retry = Math.min(5000, retry * 1.6); }
function send(m) { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(m)); return true; } return false; }
function request(m, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    const id = ++reqId; m.id = id;
    if (!send(m)) { reject(new Error("Keine Verbindung zum Backend")); return; }
    const to = setTimeout(() => { pending.delete(id); reject(new Error("Zeitüberschreitung Backend")); }, timeoutMs);
    pending.set(id, { resolve: (r) => { clearTimeout(to); resolve(r); }, reject: (e) => { clearTimeout(to); reject(e); } });
  });
}
async function cmd(name, o = {}, { quiet = false } = {}) {
  try {
    const r = await request({ type: "cmd", cmd: name, ...o, quiet });
    if (!r.ok) toast(r.text || `${name} abgelehnt`, r.result === "Timeout" ? "error" : "warning");
    return r;
  } catch (e) { toast(e.message, "error"); return { ok: false, text: e.message }; }
}
// Heartbeat: Ping alle 100 ms aus einem Worker-Takt (Hintergrund-Tabs drosseln Timer im Hauptthread stärker)
const tickWorker = new Worker(URL.createObjectURL(new Blob(
  ["setInterval(() => postMessage(0), 100);"], { type: "text/javascript" })));
tickWorker.onmessage = () => send({ type: "ping", t: performance.now() });

function onMessage(m) {
  switch (m.type) {
    case "hello":
      S.hello = m; S.axes = m.axes; S.geometry = m.geometry; S.timing = m.timing;
      $("clog").innerHTML = ""; LOGS.length = 0;
      (m.log || []).forEach(clog);
      S.plans = m.plans || []; renderPlanList();
      if (m.alarms && m.alarms.list) onAlarms(m.alarms);
      buildAxisCards(); updatePrV();
      break;
    case "status": S.st = m; dirty = true; checkSeqRun(); break;
    case "alarms": onAlarms(m); break;
    case "log": clog(m); break;
    case "pong": { const rtt = performance.now() - m.t; S.rtt.push(rtt); if (S.rtt.length > 30) S.rtt.shift(); break; }
    case "reply": { const p = pending.get(m.id); if (p) { pending.delete(m.id); p.resolve(m); } break; }
  }
}

// ── Konsole ───────────────────────────────────────────────────────────
const LOGS = [];
const lvOn = () => Object.fromEntries(qa("[data-lv]").map(c => [c.dataset.lv, c.checked]));
function logLine(e) {
  const d = new Date((e.t || Date.now() / 1000) * 1000);
  const ts = d.toLocaleTimeString("de-DE") + "." + String(d.getMilliseconds()).padStart(3, "0");
  return el("div", { className: e.level || "info" },
    `<span class="t">${ts}</span> <span class="s">[${esc(e.source || "")}]</span> ${esc(e.text)}`);
}
function logVisible(e) {
  const fl = $("cFilter").value.trim().toLowerCase();
  return lvOn()[e.level || "info"] !== false && (!fl || (e.source + " " + e.text).toLowerCase().includes(fl));
}
function clog(e) {
  LOGS.push(e); if (LOGS.length > 3000) LOGS.shift();
  if (!logVisible(e)) return;
  const box = $("clog"); box.appendChild(logLine(e));
  while (box.childNodes.length > 1500) box.firstChild.remove();
  if ($("cFollow").checked) box.scrollTop = box.scrollHeight;
}
function relog() { const box = $("clog"); box.innerHTML = ""; LOGS.filter(logVisible).forEach(e => box.appendChild(logLine(e))); box.scrollTop = box.scrollHeight; }
qa("[data-lv]").forEach(c => c.onchange = relog);
$("cFilter").oninput = relog;
$("cClear").onclick = () => { LOGS.length = 0; $("clog").innerHTML = ""; };
const hist = []; let histI = 0;
$("cIn").addEventListener("keydown", async (ev) => {
  if (ev.key === "ArrowUp") { histI = Math.max(0, histI - 1); ev.target.value = hist[histI] || ""; ev.preventDefault(); return; }
  if (ev.key === "ArrowDown") { histI = Math.min(hist.length, histI + 1); ev.target.value = hist[histI] || ""; ev.preventDefault(); return; }
  if (ev.key !== "Enter") return;
  const text = ev.target.value.trim(); if (!text) return;
  hist.push(text); histI = hist.length; ev.target.value = "";
  const box = $("clog"); box.appendChild(el("div", { className: "echo" }, "› " + esc(text)));
  if (text === "clear") { $("cClear").click(); return; }
  try {
    const r = await request({ type: "debug", text });
    box.appendChild(el("div", { className: r.ok ? "reply" : "error" }, esc(r.text || (r.ok ? "ok" : "Fehler"))));
  } catch (e) { box.appendChild(el("div", { className: "error" }, esc(e.message))); }
  box.scrollTop = box.scrollHeight;
});

// ── Kopfzeile ─────────────────────────────────────────────────────────
function setPill(id, cls) { const p = $(id); p.classList.remove("ok", "wait"); if (cls) p.classList.add(cls); }
function twoStep(btn) {
  let armed = 0, timer = null;
  const label = btn.textContent;
  const disarm = () => { armed = 0; clearInterval(timer); btn.classList.remove("armed"); btn.textContent = label; };
  btn.onclick = async () => {
    if (!armed) {
      const r = await cmd(btn.dataset.c1);
      if (!r.ok) return;
      armed = Date.now() + (S.timing.two_step_ms || 10000);
      btn.classList.add("armed");
      const upd = () => { const s = Math.ceil((armed - Date.now()) / 1000); if (s <= 0) disarm(); else btn.textContent = `${label} bestätigen (${s} s)`; };
      upd(); timer = setInterval(upd, 250);
    } else { disarm(); await cmd(btn.dataset.c2); }
  };
}
twoStep($("bResume")); twoStep($("bSingle"));
$("bPause").onclick = () => cmd("ModePause");
$("bBase").onclick = () => cmd("ModeReturnToBase");
$("bAck").onclick = $("bAck2").onclick = () => cmd("AlarmAck", { value: 0 });
$("bTheme").onclick = () => {
  const t = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = t; try { localStorage.setItem("biegbert.theme", t); } catch (_) {}
  $("bTheme").textContent = t === "light" ? "☾" : "☀"; dirty = true; drawPlanCharts();
};
$("bTheme").textContent = document.documentElement.dataset.theme === "light" ? "☾" : "☀";

function renderHeader(st) {
  setPill("pPlc", st.plc && st.plc.ok ? "ok" : "");
  $("pPlc").lastChild.textContent = st.plc ? `SPS (${st.plc.kind === "sim" ? "Simulation" : "ADS"})` : "SPS";
  setPill("pHb", st.hmi_alive ? "ok" : "");
  $("pSim").hidden = !st.simulation;
  $("pEstop").hidden = !st.estop;
  const m = $("mode"); m.textContent = MODE_TEXT[st.mode] || st.mode || "–";
  m.className = "mode " + ({ Automatic: "auto", Pause: "pause", SingleAxis: "single" }[st.mode] || "");
  const k = st.kin || {};
  $("modeSub").textContent = `Kinematik ${k.state || "–"}${st.step_g2 && st.step_g2 !== 20 ? " · " + st.step_g2_text : ""}${st.step_g3 && st.step_g3 !== 30 ? " · " + st.step_g3_text : ""}`;
  $("bPause").disabled = !["Automatic", "ReturnToBase"].includes(st.mode);
  if (!$("bResume").classList.contains("armed")) $("bResume").disabled = st.mode !== "Pause";
  if (!$("bSingle").classList.contains("armed")) $("bSingle").disabled = st.mode !== "Pause";
  $("bBase").disabled = st.mode !== "SingleAxis";
  if (S.rtt.length) $("cLatency").textContent = `Ping ${f(S.rtt.reduce((a, b) => a + b, 0) / S.rtt.length, 0)} ms`;
}

// ── Seitenleiste: Telemetrie ──────────────────────────────────────────
function renderTele(st) {
  const rows = AXES.map(n => {
    const a = st.axes[n]; const u = S.axes[n] ? S.axes[n].unit : "";
    return `<tr><td class="st"><span class="dot ${a.state}" title="${a.state}"></span></td><td><b>${n}</b></td>
      <td class="n">${f(a.pos, 2)}</td><td class="n dim">${f(a.vel, 1)}</td><td class="n dim">${f(a.torque, 2)}</td><td class="dim">${u}</td></tr>`;
  }).join("");
  $("teleAxes").innerHTML = `<tr><th></th><th>Achse</th><th class="n">Ist</th><th class="n">v</th><th class="n">M [Nm]</th><th></th></tr>${rows}`;
  $("teleClamps").innerHTML = st.clamps.map((c, i) => `<div class="${c.state}" title="${c.state}${c.valve ? ", Ventil an" : ""}">${i + 1}<br>${
    { Open: "offen", Clamped: "zu", Closing: "schließt", Opening: "öffnet", Fault: "Fehler" }[c.state] || c.state}</div>`).join("");
  $("pressure").textContent = st.io.pressure_ok ? "Druck ok" : "⚠ Druck fehlt";
  $("pressure").style.color = st.io.pressure_ok ? "" : "var(--bad)";
  const k = st.kin;
  $("teleSeq").innerHTML = `<div>Kinematik</div><div>${k.state} (${k.mode})${k.error ? ` – Fehler ${k.error_id}` : ""}</div>
    <div>Plan</div><div>${k.plan_id || "–"}</div><div>Master Z1</div><div>${f(k.master, 2)} mm</div>
    <div>G2</div><div>${esc(st.step_g2_text)}</div><div>G3</div><div>${esc(st.step_g3_text)}</div>
    <div>Bogen</div><div>${st.bend || "–"}</div><div>Z1-Grenze</div><div>${f(st.z1_max, 1)} mm</div>
    <div>Letztes Kommando</div><div>#${st.ack.id} ${st.ack.result}</div>`;
  $("teleBase").innerHTML = st.base_missing.length ? "Grundstellung fehlt: " + st.base_missing.map(esc).join(" · ") : "";
}
function onAlarms(a) {
  S.alarms = a;
  const list = a.list || [];
  $("alarmCount").textContent = list.length ? `(${list.length})` : "";
  $("alarmList").innerHTML = list.length ? list.map(x => `<li class="${x.severity} ${x.active ? "" : "inactive"}">
      <b>${esc(x.text)}</b>${x.source ? " – " + esc(x.source) : ""}
      <div class="meta">${x.severity} · ${x.time ? new Date(x.time * 1000).toLocaleTimeString("de-DE") : ""} · ${x.active ? "aktiv" : "behoben"}${x.acked ? "" : " · nicht quittiert"}${x.detail ? " · Detail " + (x.detail > 255 ? "0x" + x.detail.toString(16) : x.detail) : ""}</div></li>`).join("")
    : `<li class="empty">keine Alarme</li>`;
}

// Verlauf
const TR = { buf: [], sig: { "Z1 Position": s => s.axes.Z1.pos, "Z1 Geschwindigkeit": s => s.axes.Z1.vel, "Z1 Moment": s => s.axes.Z1.torque,
  "Z2 Position": s => s.axes.Z2.pos, "X Position": s => s.axes.X.pos, "X Geschwindigkeit": s => s.axes.X.vel,
  "A Position": s => s.axes.A.pos, "W Position": s => s.axes.W.pos } };
for (const id of ["trA", "trB"]) for (const k of Object.keys(TR.sig)) $(id).appendChild(el("option", { value: k, textContent: k }));
$("trA").value = "X Position"; $("trB").value = "Z1 Geschwindigkeit";
let trLast = 0;
function trendPush(st) {
  const t = performance.now(); if (t - trLast < 100) return; trLast = t;
  TR.buf.push({ t, s: st }); while (TR.buf.length && t - TR.buf[0].t > 30000) TR.buf.shift();
}
function prepCanvas(c) {
  const r = c.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  if (c.width !== Math.round(r.width * dpr) || c.height !== Math.round(r.height * dpr)) { c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr); }
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, r.width, r.height);
  return [g, r.width, r.height];
}
function drawTrend() {
  const c = $("trend"); if (!c.offsetParent) return;
  const [g, W, H] = prepCanvas(c); const now = performance.now();
  g.font = "11px system-ui"; g.strokeStyle = cssVar("--line"); g.lineWidth = 1;
  for (let i = 1; i < 6; i++) { const x = W * i / 6; g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke(); }
  [["trA", cssVar("--accent")], ["trB", cssVar("--info")]].forEach(([id, col], j) => {
    const fn = TR.sig[$(id).value]; const pts = TR.buf.map(b => [b.t, fn(b.s)]); if (pts.length < 2) return;
    let lo = Math.min(...pts.map(p => p[1])), hi = Math.max(...pts.map(p => p[1])); if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.1; lo -= pad; hi += pad;
    g.strokeStyle = col; g.lineWidth = 1.6; g.beginPath();
    pts.forEach(([t, v], i) => { const x = W - (now - t) / 30000 * W, y = H - (v - lo) / (hi - lo) * H; i ? g.lineTo(x, y) : g.moveTo(x, y); });
    g.stroke(); g.fillStyle = col; g.fillText(`${f(pts[pts.length - 1][1], 2)}  [${f(lo, 1)} … ${f(hi, 1)}]`, 6 + j * (W / 2), 13);
  });
  g.fillStyle = cssVar("--dim"); g.fillText("−30 s", 4, H - 4);
}

// ── Tabs ──────────────────────────────────────────────────────────────
qa(".tabs button").forEach(b => b.onclick = () => showTab(b.dataset.tab));
function showTab(t) {
  qa(".tabs button").forEach(x => x.classList.toggle("on", x.dataset.tab === t));
  qa(".tab").forEach(x => x.classList.toggle("on", x.id === "tab-" + t));
  try { localStorage.setItem("biegbert.tab", t); } catch (_) {}
  dirty = true; requestAnimationFrame(drawPlanCharts);
}
try { const t = localStorage.getItem("biegbert.tab"); if (t) showTab(t); } catch (_) {}

// ── 1 Biegeplan ───────────────────────────────────────────────────────
async function uploadFile(file) {
  if (!file) return;
  const isStep = /\.(step|stp)$/i.test(file.name);
  const content = isStep ? "" : await file.text();
  try {
    const r = await request({ type: "plan.upload", filename: file.name, content, default_radius: num("lraR", 45) });
    if (r.plan) { addPlan(r.plan); selectPlan(r.plan); }
    toast(r.ok ? `Plan ${r.plan.plan_id} berechnet` : (r.text || "Plan fehlerhaft – siehe Meldungen"), r.ok ? "ok" : "error");
  } catch (e) { toast(e.message, "error"); }
}
$("file").onchange = (e) => { uploadFile(e.target.files[0]); e.target.value = ""; };
const drop = $("drop");
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); uploadFile(e.dataTransfer.files[0]); };

function addPlan(rec) {
  const sum = { plan_id: rec.plan_id, name: rec.name, kind: rec.kind, ok: rec.report.ok !== false, summary: rec.report.summary || {}, created: rec.created };
  S.plans = S.plans.filter(p => p.plan_id !== rec.plan_id).concat(sum); renderPlanList();
}
function renderPlanList() {
  const tb = $("planList").tBodies[0];
  tb.innerHTML = S.plans.slice().reverse().map(p => `<tr class="click ${S.plan && S.plan.plan_id === p.plan_id ? "sel" : ""}" data-id="${p.plan_id}">
    <td>${p.plan_id}</td><td>${esc(p.name)}</td><td>${p.kind === "geo" ? "Geometrie" : "Maschine"}</td>
    <td class="n">${f(p.summary.length_mm, 1)}</td><td class="n">${p.summary.bends ?? "–"}</td><td>${p.ok ? "✓" : "<span style='color:var(--bad)'>Fehler</span>"}</td></tr>`).join("")
    || `<tr><td colspan="6" class="dim">Noch keine Pläne</td></tr>`;
  qa("tr[data-id]", tb).forEach(tr => tr.onclick = async () => {
    const r = await request({ type: "plan.get", plan_id: +tr.dataset.id }); if (r.ok) selectPlan(r.plan);
  });
}
function selectPlan(rec) {
  S.plan = rec; renderPlanList();
  const rep = rec.report || {}, sm = rep.summary || {}, m = rec.machine;
  $("planDetail").hidden = false;
  $("pdTitle").textContent = `Plan ${rec.plan_id} – ${rec.name}`;
  $("pdSummary").innerHTML = `<div>Länge</div><div>${f(sm.length_mm, 1)} mm</div><div>Bögen</div><div>${sm.bends ?? "–"}</div>
    <div>Z1 Start</div><div>${f(sm.z1_start_mm, 1)} mm</div><div>Z1 Ende</div><div>${f(sm.z1_end_mm, 1)} mm</div>
    <div>Z1-Grenze</div><div>${f(sm.z1_limit_mm, 1)} mm</div><div>v</div><div>${f(sm.v_mm, 1)} mm</div>
    <div>Abschnitte</div><div>${sm.machine_sections ?? (m ? m.bends.length : "–")}</div><div>Stützpunkte</div><div>${sm.points ?? "–"}</div>`;
  $("pdMsgs").innerHTML = (rep.messages || []).map(x => `<li class="${x.level}">${x.element != null ? "Element " + (x.element + 1) + ": " : ""}${esc(x.text)}</li>`).join("")
    || `<li class="info">Keine Meldungen</li>`;
  $("pdElements").innerHTML = `<tr><th>#</th><th>Typ</th><th class="n">L [mm]</th><th class="n">∠ [°]</th><th class="n">R [mm]</th><th class="n">Z1 von</th><th class="n">bis</th></tr>` +
    (rep.elements || []).map(e => `<tr><td>${e.index + 1}</td><td>${{ straight: "Gerade", bend: "Bogen", bend_var: "Bogen var.", rotation: "Rotation" }[e.type] || e.type}</td>
      <td class="n">${f(e.length_mm, 1)}</td><td class="n">${e.angle_deg != null ? f(e.angle_deg, 1) : "–"}</td>
      <td class="n">${e.r_min != null ? (Math.abs(e.r_max - e.r_min) < 0.05 ? f(e.r_min, 1) : f(e.r_min, 1) + "–" + f(e.r_max, 1)) : "–"}</td>
      <td class="n">${f(e.s0, 1)}</td><td class="n">${f(e.s1, 1)}</td></tr>`).join("");
  $("pdMachine").innerHTML = m ? `<tr><th>#</th><th class="n">Z1 Start</th><th class="n">Z1 Ende</th><th class="n">v [mm/s]</th><th class="n">Punkte</th><th>Rotation</th><th>NH</th></tr>` +
    m.bends.map((b, i) => `<tr><td>${i + 1}</td><td class="n">${f(b.z1_start_mm, 1)}</td><td class="n">${f(b.z1_fin_mm, 1)}</td>
      <td class="n">${f(b.feed_mm_s, 1)}</td><td class="n">${b.points.length}</td>
      <td>${(b.rotations || []).map(r => `${f(r.w_deg, 0)}° @ ${f(r.z1_mm, 0)}`).join(", ") || "–"}</td><td>${(b.clamps || []).join(",") || "–"}</td></tr>`).join("")
    : `<tr><td class="dim">Kein Maschinenplan (Fehler)</td></tr>`;
  preview.pts = rep.centerline || null; preview.fit = true;
  drawPlanCharts(); dirty = true;
}
$("pdJsonGeo").onclick = () => S.plan && S.plan.geo && download(`biegeplan_${S.plan.plan_id}.json`, JSON.stringify(S.plan.geo, null, 2));
$("pdJsonMachine").onclick = () => S.plan && S.plan.machine && download(`maschinenplan_${S.plan.plan_id}.json`, JSON.stringify(S.plan.machine, null, 2));
$("pdLoad").onclick = async () => {
  if (!S.plan) return;
  try { const r = await request({ type: "plan.load", plan_id: S.plan.plan_id }); toast(r.text || (r.ok ? "Plan geladen" : "Fehler"), r.ok ? "ok" : "warning"); }
  catch (e) { toast(e.message, "error"); }
};
$("pdMat").onclick = () => S.plan && cmd("MaterialConfirm", { value: S.plan.plan_id });
$("pdStart").onclick = () => cmd("SeqStart");
$("pdQ1").onclick = () => cmd("SeqConfirmQ1");
$("pdQ2").onclick = () => cmd("SeqConfirmQ2");
$("pdEnt").onclick = () => cmd("SeqPartRemoved");
function renderPlanSteps(st) {
  if (!S.plan) return;
  const k = st.kin, loaded = k.plan_id === S.plan.plan_id && k.state !== "Idle";
  const matMissing = st.base_missing.includes("Material nicht bestätigt");
  $("pdLoad").disabled = !S.plan.machine || ["Coupling", "Running", "Holding", "Held"].includes(k.state);
  $("pdMat").disabled = !loaded || !matMissing;
  $("pdStart").disabled = !loaded || matMissing || k.state !== "Loaded" || ![20, 27].includes(st.step_g2);
  $("pdQ1").hidden = st.step_g2 !== 24;
  $("pdQ2").hidden = st.step_g2 !== 25;
  $("pdEnt").hidden = st.step_g2 !== 27;
  const hints = [];
  if (st.mode !== "Automatic") hints.push("Start nur in Automatik");
  const notReady = AXES.filter(n => !st.axes[n].enabled || st.axes[n].error);
  if (notReady.length) hints.push(`nicht freigegeben/gestört: ${notReady.join(", ")}`);
  if (!S.plan.machine) hints.push("Plan enthält Fehler");
  else if (!loaded) hints.push(k.plan_id ? `SPS hat Plan ${k.plan_id} geladen` : "noch nicht übertragen");
  if (st.step_g2 === 20 && st.base_missing.some(b => /Null-Position|Niederhalter/.test(b)))
    hints.push("erster Start nur aus der Grundstellung (alle Achsen 0, Niederhalter offen)");
  if (st.step_g2 && st.step_g2 !== 20) hints.push(st.step_g2_text);
  $("pdHint").textContent = hints.join(" · ");
}

// Profil u(s), φ(s)
function drawProfile() {
  const c = $("pdProfile"); if (!c.offsetParent || !S.plan) return;
  const [g, W, H] = prepCanvas(c); const p = (S.plan.report || {}).profile; if (!p || !p.s.length) return;
  const L = 44, R = 44, T = 14, B = 22, w = W - L - R, h = H - T - B;
  const s0 = p.s[0], s1 = p.s[p.s.length - 1] || s0 + 1;
  const uMax = Math.max(1, ...p.u.map(Math.abs)), phMax = Math.max(1, ...p.phi.map(Math.abs));
  const X = s => L + (s - s0) / (s1 - s0 || 1) * w, Yu = u => T + h / 2 - u / uMax * h / 2 * 0.95, Yp = v => T + h / 2 - v / phMax * h / 2 * 0.95;
  g.font = "11px system-ui"; g.strokeStyle = cssVar("--line"); g.fillStyle = cssVar("--dim"); g.lineWidth = 1;
  g.beginPath(); g.moveTo(L, T + h / 2); g.lineTo(L + w, T + h / 2); g.stroke();
  for (let i = 0; i <= 5; i++) { const s = s0 + (s1 - s0) * i / 5, x = X(s); g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + h); g.stroke(); g.fillText(f(s, 0), x - 10, H - 6); }
  g.fillText(`u ±${f(uMax, 1)} mm`, 4, 12); g.fillText(`φ ±${f(phMax, 1)}°`, W - R - 20, 12);
  const line = (arr, Y, col, dash) => { g.strokeStyle = col; g.lineWidth = 1.8; g.setLineDash(dash || []); g.beginPath();
    arr.forEach((v, i) => { const x = X(p.s[i]), y = Y(v); i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke(); g.setLineDash([]); };
  line(p.u, Yu, cssVar("--accent")); line(p.phi, Yp, cssVar("--info"), [6, 4]);
  g.fillStyle = cssVar("--accent"); g.fillText("— u (X)", L + 4, T + h - 4); g.fillStyle = cssVar("--info"); g.fillText("- - φ (A)", L + 60, T + h - 4);
  (S.plan.report.elements || []).filter(e => e.type === "rotation").forEach(e => {
    g.strokeStyle = cssVar("--ok"); g.setLineDash([4, 3]); g.beginPath(); g.moveTo(X(e.s0), T); g.lineTo(X(e.s0), T + h); g.stroke(); g.setLineDash([]);
    g.fillStyle = cssVar("--ok"); g.fillText(`W ${f(e.angle_deg, 0)}°`, X(e.s0) + 3, T + 10);
  });
  if (S.st && S.st.kin.plan_id === S.plan.plan_id) {
    const z = S.st.axes.Z1.pos; if (z >= s0 && z <= s1) { g.strokeStyle = cssVar("--bad"); g.lineWidth = 2; g.beginPath(); g.moveTo(X(z), T); g.lineTo(X(z), T + h); g.stroke(); }
  }
  g.fillStyle = cssVar("--dim"); g.fillText("Z1 [mm]", W - R + 4, H - 6);
}
// Vorschau (einfache 3D-Projektion der Mittellinie)
const preview = { yaw: 0.8, pitch: 0.45, pts: null, fit: true, scale: 1, cx: 0, cy: 0, cz: 0 };
(function () {
  const c = $("pdPreview"); let drag = null;
  c.onpointerdown = (e) => { drag = { x: e.clientX, y: e.clientY }; c.setPointerCapture(e.pointerId); };
  c.onpointermove = (e) => { if (!drag) return; preview.yaw += (e.clientX - drag.x) * 0.01; preview.pitch = Math.max(-1.5, Math.min(1.5, preview.pitch + (e.clientY - drag.y) * 0.01)); drag = { x: e.clientX, y: e.clientY }; drawPreview(); };
  c.onpointerup = () => drag = null;
  c.onwheel = (e) => { e.preventDefault(); preview.scale *= Math.exp(-e.deltaY * 0.001); drawPreview(); };
})();
function drawPreview() {
  const c = $("pdPreview"); if (!c.offsetParent) return;
  const [g, W, H] = prepCanvas(c); const P = preview.pts; if (!P || P.length < 2) return;
  if (preview.fit) {
    const mn = [0, 1, 2].map(i => Math.min(...P.map(p => p[i]))), mx = [0, 1, 2].map(i => Math.max(...P.map(p => p[i])));
    preview.cx = (mn[0] + mx[0]) / 2; preview.cy = (mn[1] + mx[1]) / 2; preview.cz = (mn[2] + mx[2]) / 2;
    const ext = Math.max(1, ...[0, 1, 2].map(i => mx[i] - mn[i])); preview.scale = Math.min(W, H) * 0.8 / ext; preview.fit = false;
  }
  const cy = Math.cos(preview.yaw), sy = Math.sin(preview.yaw), cp = Math.cos(preview.pitch), sp = Math.sin(preview.pitch);
  const proj = ([x, y, z]) => { x -= preview.cx; y -= preview.cy; z -= preview.cz;
    const x1 = x * cy - z * sy, z1 = x * sy + z * cy, y1 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
    return [W / 2 + x1 * preview.scale, H / 2 - y1 * preview.scale, z2]; };
  // Achsenkreuz
  const o = proj([preview.cx, preview.cy, preview.cz]);
  [[[40, 0, 0], "#d9675a", "x"], [[0, 40, 0], "#5fbf77", "y"], [[0, 0, 40], "#6fa8dc", "z"]].forEach(([d, col, n]) => {
    const p0 = proj([preview.cx - preview.cx + 0, 0, 0]), p1 = proj(d); g.strokeStyle = col; g.lineWidth = 1; g.beginPath(); g.moveTo(p0[0], p0[1]); g.lineTo(p1[0], p1[1]); g.stroke();
    g.fillStyle = col; g.font = "10px system-ui"; g.fillText(n, p1[0] + 2, p1[1]); });
  g.lineCap = "round"; g.lineJoin = "round";
  g.strokeStyle = cssVar("--tube"); g.lineWidth = Math.max(2, Math.min(14, 14.9 * preview.scale)); g.beginPath();
  P.forEach((p, i) => { const q = proj(p); i ? g.lineTo(q[0], q[1]) : g.moveTo(q[0], q[1]); }); g.stroke();
  const s = proj(P[0]); g.fillStyle = cssVar("--accent"); g.beginPath(); g.arc(s[0], s[1], 4, 0, 7); g.fill();
  g.fillStyle = cssVar("--dim"); g.font = "11px system-ui"; g.fillText("● Rohrende (Anschlag)", 6, H - 6);
  void o;
}
function drawPlanCharts() { drawProfile(); drawPreview(); }

// ── 2 Halbautomatisch ─────────────────────────────────────────────────
let elType = "straight";
const EL_FORMS = {
  straight: [["length_mm", "Länge", 50, "mm"]],
  bend: [["angle_deg", "Winkel", 90, "°"], ["radius_mm", "Radius", 45, "mm"]],
  bend_var: [["length_mm", "Länge", 60, "mm"], ["radius_start_mm", "R Anfang", 0, "mm"], ["radius_end_mm", "R Ende", 45, "mm"], ["direction", "Richtung", 1, "±1"]],
  rotation: [["angle_deg", "Drehung W", 90, "°"]],
};
const lastVals = {};
function renderElForm() {
  qa("#elType button").forEach(b => b.classList.toggle("on", b.dataset.t === elType));
  $("elForm").innerHTML = EL_FORMS[elType].map(([k, lbl, def, u]) =>
    `<label>${lbl}<input type="number" data-k="${k}" value="${lastVals[elType + k] ?? def}" step="${k === "direction" ? 2 : 0.5}"></label><em>${u}</em>`).join("");
  qa("#elForm input").forEach(i => i.oninput = () => { lastVals[elType + i.dataset.k] = i.value; elCalc(); });
  elCalc();
}
qa("#elType button").forEach(b => b.onclick = () => { elType = b.dataset.t; renderElForm(); });
function readEl() {
  const e = { type: elType };
  qa("#elForm input").forEach(i => e[i.dataset.k] = parseFloat(i.value));
  if (e.type === "bend_var") { if (e.direction < 0) e.direction = -1; else delete e.direction; }
  return e;
}
const vNow = () => (S.geometry.v_at_z2_zero_mm || 24) - num("prZ2", 0);
function uFromR(R, v) { return R < v ? NaN : R - Math.sqrt(R * R - v * v); }
function elCalc() {
  const e = readEl(), v = vNow(); let t = "";
  const aMax = 60, af = num("prAf", 1);
  const chk = (R) => { if (!R) return ""; const u = uFromR(R, v); if (!isFinite(u)) return `<span class="bad">R < v = ${f(v, 1)} mm – nicht herstellbar</span>`;
    const phi = Math.asin(v / R) / D2R * af; return `u = ${f(u, 2)} mm, φ = ${f(phi, 1)}°${Math.abs(phi) > aMax ? ' <span class="bad">> 60°</span>' : ""}`; };
  if (e.type === "bend") t = `Bogenlänge ${f(Math.abs(e.angle_deg) * D2R * e.radius_mm, 1)} mm · ${chk(e.radius_mm)}`;
  if (e.type === "bend_var") {
    const k0 = e.radius_start_mm ? 1 / e.radius_start_mm : 0, k1 = e.radius_end_mm ? 1 / e.radius_end_mm : 0;
    t = `Winkel ≈ ${f(e.length_mm * (k0 + k1) / 2 / D2R, 1)}° (Krümmung linear) · Anfang: ${chk(e.radius_start_mm) || "gerade"} · Ende: ${chk(e.radius_end_mm) || "gerade"}`;
  }
  if (e.type === "rotation") t = "Dreht das Rohr (W) vor dem nächsten Element.";
  if (e.type === "straight") t = "Vorschub ohne Auslenkung.";
  $("elCalc").innerHTML = t;
}
function elText(e) {
  if (e.type === "straight") return `Gerade ${f(e.length_mm, 1)} mm`;
  if (e.type === "bend") return `Bogen ${f(e.angle_deg, 1)}°, R ${f(e.radius_mm, 1)} mm`;
  if (e.type === "bend_var") return `Bogen veränderlich ${f(e.length_mm, 1)} mm, R ${e.radius_start_mm ? f(e.radius_start_mm, 1) : "∞"} → ${e.radius_end_mm ? f(e.radius_end_mm, 1) : "∞"} mm${e.direction < 0 ? " (−X)" : ""}`;
  if (e.type === "rotation") return `Rotation W ${f(e.angle_deg, 1)}°`;
  return e.type;
}
function elLen(e) { return e.type === "straight" || e.type === "bend_var" ? e.length_mm : e.type === "bend" ? Math.abs(e.angle_deg) * D2R * e.radius_mm : 0; }
$("elAdd").onclick = () => {
  const e = readEl();
  for (const [k] of EL_FORMS[e.type]) if (k !== "direction" && !isFinite(e[k])) { toast(`Wert „${k}“ fehlt`, "warning"); return; }
  S.seq.push(e); S.seqState.push("offen"); renderSeq();
};
function renderSeq() {
  const STC = { offen: "", läuft: "color:var(--info)", fertig: "color:var(--ok)", Fehler: "color:var(--bad)" };
  $("seqTable").innerHTML = `<tr><th>#</th><th>Element</th><th class="n">Länge</th><th>Status</th><th></th></tr>` +
    (S.seq.map((e, i) => `<tr><td>${i + 1}</td><td>${esc(elText(e))}</td><td class="n">${f(elLen(e), 1)}</td>
      <td style="${STC[S.seqState[i]] || ""}">${S.seqState[i]}</td>
      <td><button class="small" data-up="${i}">↑</button> <button class="small" data-dn="${i}">↓</button> <button class="small" data-rm="${i}">✕</button></td></tr>`).join("")
    || `<tr><td colspan="5" class="dim">Noch keine Elemente – links eingeben und „Hinzufügen“</td></tr>`);
  const mv = (i, j) => { if (j < 0 || j >= S.seq.length) return; [S.seq[i], S.seq[j]] = [S.seq[j], S.seq[i]]; [S.seqState[i], S.seqState[j]] = [S.seqState[j], S.seqState[i]]; renderSeq(); };
  qa("[data-up]").forEach(b => b.onclick = () => mv(+b.dataset.up, +b.dataset.up - 1));
  qa("[data-dn]").forEach(b => b.onclick = () => mv(+b.dataset.dn, +b.dataset.dn + 1));
  qa("[data-rm]").forEach(b => b.onclick = () => { S.seq.splice(+b.dataset.rm, 1); S.seqState.splice(+b.dataset.rm, 1); renderSeq(); });
  const tot = S.seq.reduce((a, e) => a + elLen(e), 0);
  $("seqHint").textContent = S.seq.length ? `Gesamtlänge ${f(tot, 1)} mm` : "";
  saveSeq();
}
const SEQ_INPUTS = ["prZ2", "prFeed", "prTrans", "prAf", "maD", "maW", "maY", "maF"];
function saveSeq() {
  try { localStorage.setItem("biegbert.seq", JSON.stringify({ seq: S.seq, state: S.seqState,
    inputs: Object.fromEntries(SEQ_INPUTS.map(id => [id, $(id).value])) })); } catch (_) {}
}
function restoreSeq() {
  try {
    const d = JSON.parse(localStorage.getItem("biegbert.seq") || "null"); if (!d) return;
    S.seq = d.seq || []; S.seqState = (d.state || []).map(s => s === "läuft" ? "offen" : s);
    while (S.seqState.length < S.seq.length) S.seqState.push("offen");
    for (const [id, v] of Object.entries(d.inputs || {})) if ($(id) && v !== "") $(id).value = v;
  } catch (_) {}
}
SEQ_INPUTS.forEach(id => $(id).addEventListener("change", saveSeq));
qa("#seqMode button").forEach(b => b.onclick = () => {
  S.seqMode = b.dataset.m; qa("#seqMode button").forEach(x => x.classList.toggle("on", x === b));
  $("seqAll").hidden = S.seqMode !== "all"; $("seqStep").hidden = S.seqMode !== "step";
});
function processParams() { return { z2_mm: num("prZ2", 0), feed_mm_s: num("prFeed", 30), transition_mm: num("prTrans", 30), a_factor: num("prAf", 1) }; }
function materialParams() { return { diameter_mm: num("maD", 15), wall_mm: num("maW", 2), yield_mpa: num("maY", 300), force_limit_n: num("maF", 16000) }; }
function geoFromSeq(name = "Halbautomatisch") {
  return { format: "biegbert-geo", version: 1, name, material: materialParams(), process: processParams(), elements: S.seq };
}
function updatePrV() { $("prV").textContent = f(vNow(), 1); elCalc(); }
$("prZ2").oninput = updatePrV; $("prAf").oninput = elCalc;
$("seqBuild").onclick = async () => {
  if (!S.seq.length) { toast("Keine Elemente", "warning"); return; }
  try {
    const r = await request({ type: "plan.build", plan: geoFromSeq() });
    if (r.plan) { addPlan(r.plan); selectPlan(r.plan); showTab("plan"); }
    toast(r.ok ? `Plan ${r.plan.plan_id} erzeugt – weiter unter 1 · Biegeplan` : (r.text || "Plan fehlerhaft"), r.ok ? "ok" : "error");
  } catch (e) { toast(e.message, "error"); }
};
$("seqSave").onclick = () => download("abfolge.json", JSON.stringify(geoFromSeq(), null, 2));
$("seqLoad").onchange = async (ev) => {
  const file = ev.target.files[0]; ev.target.value = ""; if (!file) return;
  try {
    const d = JSON.parse(await file.text());
    if (d.format !== "biegbert-geo") throw new Error("Kein biegbert-geo-Plan");
    S.seq = d.elements || []; S.seqState = S.seq.map(() => "offen");
    const p = d.process || {}, m = d.material || {};
    [["prZ2", p.z2_mm], ["prFeed", p.feed_mm_s], ["prTrans", p.transition_mm], ["prAf", p.a_factor],
     ["maD", m.diameter_mm], ["maW", m.wall_mm], ["maY", m.yield_mpa], ["maF", m.force_limit_n]].forEach(([id, v]) => { if (v != null) $(id).value = v; });
    updatePrV(); renderSeq();
  } catch (e) { toast(e.message, "error"); }
};
$("seqClear").onclick = () => { S.seq = []; S.seqState = []; S.seqRun = null; renderSeq(); };
$("seqReset").onclick = () => { S.seqState = S.seq.map(() => "offen"); S.seqRun = null; renderSeq(); };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function waitFor(pred, ms) { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (S.st && pred(S.st)) return true; await sleep(100); } return false; }
$("seqNext").onclick = async () => {
  const i0 = S.seqState.findIndex(s => s === "offen" || s === "Fehler");
  if (i0 < 0) { toast("Alle Elemente ausgeführt", "ok"); return; }
  let i1 = i0; while (i1 < S.seq.length && S.seq[i1].type === "rotation") i1++;
  if (i1 >= S.seq.length) { toast("Rotation am Ende ohne folgendes Element – geometrisch ohne Wirkung", "warning"); return; }
  if (!S.st || S.st.mode !== "Automatic") { toast("Schrittbetrieb benötigt die Betriebsart Automatik", "warning"); return; }
  const notReady = AXES.filter(n => !S.st.axes[n].enabled || S.st.axes[n].error);
  if (notReady.length) { toast(`Achse nicht freigegeben oder gestört: ${notReady.join(", ")} – unter 3 · Manuell „Alle Achsen freigeben“`, "warning", 7000); return; }
  const idx = []; for (let i = i0; i <= i1; i++) idx.push(i);
  $("seqNext").disabled = true;
  try {
    // vorherigen Schritt abschließen: Q1 (Niederhalter öffnen), Q2 (Biegekopf zurück) bis Übergabe S27
    if (S.st.step_g2 === 24) {
      const q = await cmd("SeqConfirmQ1"); if (!q.ok) return;
      if (!await waitFor(st => st.step_g2 === 25, 5000)) { toast("Q1 nicht übernommen", "error"); return; }
    }
    if (S.st.step_g2 === 25) {
      if (!await waitFor(st => st.clamps.every(c => c.state === "Open"), 10000)) { toast("Niederhalter öffnen nicht", "error"); return; }
      const q = await cmd("SeqConfirmQ2"); if (!q.ok) return;
    }
    if (S.st.step_g2 >= 25 && S.st.step_g2 <= 27) {
      if (!await waitFor(st => st.step_g2 === 27 && st.kin.state === "Loaded", 30000)) { toast("Vorheriger Schritt nicht abgeschlossen", "error"); return; }
    }
    if (![20, 27].includes(S.st.step_g2)) { toast(`Ablauf steht in ${S.st.step_g2_text}`, "warning"); return; }
    const r = await request({ type: "step.run", elements: idx.map(i => S.seq[i]), process: processParams(), material: materialParams() });
    if (r.plan) addPlan(r.plan);
    if (!r.ok) { idx.forEach(i => S.seqState[i] = "Fehler"); toast(r.text || "Schritt abgelehnt", "error"); }
    else { idx.forEach(i => S.seqState[i] = "läuft"); S.seqRun = { idx, plan: r.plan.plan_id, seen: false }; }
    renderSeq();
  } catch (e) { toast(e.message, "error"); }
  finally { $("seqNext").disabled = false; }
};
function checkSeqRun() {
  const r = S.seqRun, st = S.st; if (!r || !st) return;
  if (st.kin.plan_id !== r.plan) return;
  if (st.step_g2 >= 21 && st.step_g2 <= 23) r.seen = true;
  if (st.kin.state === "Fault") { r.idx.forEach(i => S.seqState[i] = "Fehler"); S.seqRun = null; renderSeq(); }
  else if (r.seen && st.step_g2 >= 24) { r.idx.forEach(i => S.seqState[i] = "fertig"); S.seqRun = null; renderSeq(); }
  else if (r.seen && st.step_g2 === 20) { r.idx.forEach(i => S.seqState[i] = "Fehler"); S.seqRun = null; renderSeq(); }
}

// ── 3 Manuell ─────────────────────────────────────────────────────────
let jogTarget = null;
function buildAxisCards() {
  const box = $("axisCards"); box.innerHTML = "";
  for (const n of AXES) {
    const a = S.axes[n] || { unit: "", v_setup: 10 };
    const c = el("div", { className: "axcard", id: "ax" + n });
    c.innerHTML = `<div class="head"><span class="name">${n} <small class="dim">${AXIS_NAME[n]}</small></span><span class="badge" data-f="state">–</span></div>
      <div class="pos"><span data-f="pos">–</span> <small>${a.unit}</small></div>
      <div class="meta">Soll <span data-f="set">–</span> · v <span data-f="vel">–</span> · M <span data-f="tq">–</span> Nm</div>
      <div class="row"><button data-a="en">Freigabe</button><button data-a="reset">Reset</button><button data-a="halt" class="danger">Halt</button></div>
      <div class="row"><input type="number" data-f="tgt" step="0.1" title="Ziel ${a.unit}"><input type="number" data-f="v" value="${a.v_setup}" step="0.5" title="Geschwindigkeit">
        <button data-a="go">Fahren</button></div>
      <div class="row"><button class="jog" data-d="-1">−</button><button class="jog" data-d="1">+</button></div>
      <div class="meta">${a.min != null ? `Bereich ${a.min} … ${a.max} ${a.unit}` : "Modulo 360°"} · Einrichten max ${a.v_setup} ${a.unit}/s</div>`;
    c.querySelector('[data-a="en"]').onclick = () => cmd(S.st && S.st.axes[n].enabled ? "AxisDisable" : "AxisEnable", { axis: n });
    c.querySelector('[data-a="reset"]').onclick = () => cmd("AxisReset", { axis: n });
    c.querySelector('[data-a="halt"]').onclick = () => cmd("AxisHalt", { axis: n });
    c.querySelector('[data-a="go"]').onclick = () => {
      const t = parseFloat(c.querySelector('[data-f="tgt"]').value), v = parseFloat(c.querySelector('[data-f="v"]').value);
      if (!isFinite(t) || !isFinite(v)) { toast("Ziel und Geschwindigkeit eingeben", "warning"); return; }
      cmd("AxisMoveAbs", { axis: n, position: t, velocity: v });
    };
    c.querySelectorAll(".jog").forEach(b => {
      const start = (e) => { e.preventDefault(); b.setPointerCapture && e.pointerId != null && b.setPointerCapture(e.pointerId); jogTarget = { n, b };
        b.classList.add("active"); send({ type: "jog", axis: n, dir: +b.dataset.d, factor: num("jogF", 30) }); };
      const stop = () => { if (jogTarget && jogTarget.b === b) { send({ type: "jog", axis: n, dir: 0, factor: 0 }); b.classList.remove("active"); jogTarget = null; } };
      b.onpointerdown = start; b.onpointerup = stop; b.onpointercancel = stop; b.onlostpointercapture = stop;
    });
    box.appendChild(c);
  }
}
window.addEventListener("blur", () => { if (jogTarget) { send({ type: "jog", axis: jogTarget.n, dir: 0, factor: 0 }); jogTarget.b.classList.remove("active"); jogTarget = null; } });
$("jogF").oninput = () => $("jogFv").textContent = $("jogF").value + " %";
$("enAll").onclick = async () => { for (const n of AXES) await cmd("AxisEnable", { axis: n }, { quiet: true }); };
$("haltAll").onclick = async () => { for (const n of AXES) await cmd("AxisHalt", { axis: n }, { quiet: true }); };
$("feedGo").onclick = () => {
  const end = isFinite(parseFloat($("feedEnd").value)) ? num("feedEnd") : (S.st ? Math.floor(S.st.z1_max) : 0);
  cmd("AxisMoveAbs", { axis: "Z1", position: end, velocity: num("feedV", 10) });
};
$("feedStop").onclick = () => cmd("AxisHalt", { axis: "Z1" });
$("aTan").onclick = () => {
  if (!S.st) return; const u = S.st.axes.X.pos, v = vNowFromStatus();
  const phi = Math.abs(u) < 1e-6 ? 0 : Math.sign(u) * Math.asin(Math.min(1, 2 * Math.abs(u) * v / (u * u + v * v))) / D2R;
  cmd("AxisMoveAbs", { axis: "A", position: +phi.toFixed(3), velocity: S.axes.A ? S.axes.A.v_setup : 20 });
};
function vNowFromStatus() { return (S.geometry.v_at_z2_zero_mm || 24) - (S.st ? S.st.axes.Z2.pos : 0); }
function buildClampCards() {
  $("clampCards").innerHTML = [1, 2, 3, 4, 5, 6].map(i => `<div class="clamp" id="cl${i}"><b>NH ${i}</b><span data-f="st">–</span>
    <div class="row"><button class="small" data-close="${i}">zu</button><button class="small" data-open="${i}">auf</button></div></div>`).join("");
  qa("[data-close]").forEach(b => b.onclick = () => cmd("ClampClose", { clamp: +b.dataset.close }));
  qa("[data-open]").forEach(b => b.onclick = () => cmd("ClampOpen", { clamp: +b.dataset.open }));
}
buildClampCards();
$("clOpenAll").onclick = async () => { for (let i = 1; i <= 6; i++) await cmd("ClampOpen", { clamp: i }, { quiet: true }); };
$("clCloseFree").onclick = async () => {
  const z = S.st ? S.st.axes.Z1.pos : 0, zones = (S.geometry.clamp_zones || []);
  for (let i = 1; i <= 6; i++) { const zn = zones[Math.ceil(i / 2) - 1]; if (zn && z > zn[0] - 1 && z < zn[1]) continue; await cmd("ClampClose", { clamp: i }, { quiet: true }); }
};
qa("[data-fault]").forEach(b => b.onclick = () => cmd("SimInjectFault", { value: +b.dataset.fault }));
qa("[data-input]").forEach(b => b.onclick = () => { const [n, v] = b.dataset.input.split(":"); cmd("SimSetInput", { value: +n, position: +v }); });
function renderManual(st) {
  const single = st.mode === "SingleAxis";
  $("manBanner").className = "banner" + (single ? " ok" : "");
  $("manBanner").innerHTML = single ? "Betriebsart <b>Einzelachsen</b> – Einzelbewegungen und Tippen möglich (Heartbeat muss leben)."
    : `Einzelbewegungen sind nur in der Betriebsart <b>Einzelachsen</b> möglich (aktuell: ${MODE_TEXT[st.mode] || st.mode}). Wechsel: Pause → „Einzelachsen“ (zweistufig).`;
  for (const n of AXES) {
    const a = st.axes[n], c = $("ax" + n); if (!c) continue;
    const b = c.querySelector('[data-f="state"]'); b.textContent = a.state; b.className = "badge " + a.state;
    c.querySelector('[data-f="pos"]').textContent = f(a.pos, n === "X" ? 3 : 2);
    c.querySelector('[data-f="set"]').textContent = f(a.set_pos, 2);
    c.querySelector('[data-f="vel"]').textContent = f(a.vel, 2);
    c.querySelector('[data-f="tq"]').textContent = f(a.torque, 2);
    const en = c.querySelector('[data-a="en"]'); en.textContent = a.enabled ? "Freigabe aus" : "Freigabe ein";
    en.classList.toggle("primary", !a.enabled);
    c.querySelector('[data-a="go"]').disabled = !single || !a.enabled;
    c.querySelectorAll(".jog").forEach(j => j.disabled = !single || !a.enabled);
  }
  if (document.activeElement !== $("feedEnd") && !$("feedEnd").value) $("feedEnd").placeholder = f(st.z1_max, 0);
  const z = st.axes.Z1.pos, zones = S.geometry.clamp_zones || [];
  const ahead = zones.map((zn, k) => ({ k: k + 1, lo: zn[0] })).filter(o => o.lo > z && [2 * o.k - 2, 2 * o.k - 1].some(i => st.clamps[i].state !== "Open"));
  $("feedHint").innerHTML = `Z1 = ${f(z, 1)} mm · Restweg bis Kollision ${f(st.z1_max - z, 1)} mm` +
    (ahead.length ? ` · <span style="color:var(--warn)">geschlossene Niederhalter voraus: Paar ${ahead.map(o => `${o.k} ab Z1 ${f(o.lo, 0)}`).join(", ")}</span>` : "");
  const u = st.axes.X.pos, v = vNowFromStatus(), R = Math.abs(u) > 1e-6 ? (u * u + v * v) / (2 * Math.abs(u)) : Infinity;
  const phi = Math.abs(u) < 1e-6 ? 0 : Math.sign(u) * Math.asin(Math.min(1, 2 * Math.abs(u) * v / (u * u + v * v))) / D2R;
  $("dieKv").innerHTML = `<div>X (u)</div><div>${f(u, 3)} mm</div><div>A</div><div>${f(st.axes.A.pos, 2)}°</div>
    <div>v</div><div>${f(v, 2)} mm</div><div>R(u,v)</div><div>${isFinite(R) ? f(R, 1) + " mm" : "∞"}</div>
    <div>φ tangential</div><div>${f(phi, 2)}° (Abweichung A ${f(st.axes.A.pos - phi, 2)}°)</div>`;
  st.clamps.forEach((c, i) => { const d = $("cl" + (i + 1)); d.className = "clamp " + c.state;
    d.querySelector('[data-f="st"]').textContent = { Open: "offen", Clamped: "geklemmt", Closing: "schließt", Opening: "öffnet", Fault: "Fehler" }[c.state] || c.state; });
  $("simCard").hidden = !st.simulation;
}

// ── Parameter ─────────────────────────────────────────────────────────
$("paApply").onclick = async () => {
  try {
    const r = await request({ type: "param", param: { plan_id: num("paPlan"), diameter_mm: num("paD"), wall_mm: num("paW"), yield_mpa: num("paY"),
      force_limit_n: num("paF"), feed_override_pct: num("paFo"), single_axis_velo_pct: num("paSv") } });
    toast(r.text || (r.ok ? "übernommen" : "abgelehnt"), r.ok ? "ok" : "warning");
  } catch (e) { toast(e.message, "error"); }
};
function renderParam(st) {
  const p = st.param;
  $("paActive").innerHTML = `<div>Plan-ID</div><div>${p.plan_id}</div><div>Ø</div><div>${f(p.diameter_mm, 2)} mm</div>
    <div>Wand</div><div>${f(p.wall_mm, 2)} mm</div><div>Rp0,2</div><div>${f(p.yield_mpa, 0)} MPa</div>
    <div>Kraftgrenze</div><div>${f(p.force_limit_n, 0)} N</div><div>Vorschub-Override</div><div>${f(p.feed_override_pct, 0)} %</div>
    <div>Einzelachs-Geschw.</div><div>${f(p.single_axis_velo_pct, 0)} %</div>`;
}

// ── Zeichenschleife ───────────────────────────────────────────────────
let dirty = false, lastChart = 0;
function frame(t) {
  requestAnimationFrame(frame);
  const st = S.st;
  if (st) trendPush(st);
  if (dirty && st && st.axes) {
    dirty = false;
    renderHeader(st); renderTele(st); renderManual(st); renderParam(st); renderPlanSteps(st);
  }
  if (t - lastChart > 100) { lastChart = t; drawTrend(); if (S.plan && st && st.kin.plan_id === S.plan.plan_id) drawProfile(); }
}
window.addEventListener("resize", () => { dirty = true; drawPlanCharts(); });
restoreSeq(); renderElForm(); renderSeq(); renderPlanList();
connect();
requestAnimationFrame(frame);
})();
