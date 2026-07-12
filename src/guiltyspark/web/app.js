/* Dashboard client. Talks only to the JSON API (/api/*), so a future Vue
 * frontend can replace this file without backend changes. */

"use strict";

const REFRESH_MS = 60_000;
let windowMinutes = 60;

const $ = (id) => document.getElementById(id);

function esc(text) {
  const div = document.createElement("div");
  div.textContent = String(text ?? "");
  return div.innerHTML;
}

function fmtTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  return date.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function fmtNs(ns) {
  return fmtTime(new Date(ns / 1e6).toISOString());
}

async function getJSON(path) {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

/* ---- masthead / uplink ---- */

function setUplink(ok, detail) {
  $("uplink-dot").className = "uplink-dot " + (ok ? "ok" : "bad");
  $("uplink-text").textContent = ok ? "Uplink nominal" : "Uplink degraded";
  $("greeting").textContent = ok
    ? "Greetings, Reclaimer. I have surveyed the installation's log streams. The relevant anomalies are catalogued below."
    : `A regrettable malfunction, Reclaimer — the archive uplink is not responding. ${detail || ""}`;
}

/* ---- overview ---- */

async function loadOverview() {
  const data = await getJSON("/api/overview");
  $("tile-findings").textContent = data.counts.findings;
  $("tile-measures").textContent = data.counts.remediations;
  const targets = data.targets.map((t) => `${t.id} [${t.mode}]`).join(" · ");
  $("footer-targets").textContent = targets
    ? `Containment protocols: ${targets}`
    : "No containment protocols configured.";
}

/* ---- anomalies (live Loki) ---- */

function severityChip(level) {
  return `<span class="sev sev-${esc(level)}">${esc(level)}</span>`;
}

function renderIncident(incident) {
  const samples = (incident.samples || [])
    .map((line) => `<div>${esc(line)}</div>`)
    .join("");
  return `
    <details class="incident">
      <summary>
        ${severityChip(incident.level)}
        <span class="inc-service">${esc(incident.service)}</span>
        <span class="inc-count">×${incident.count}</span>
        <span class="inc-when">last ${fmtNs(incident.last_seen_ns)}</span>
        <span class="inc-bucket">${esc(incident.bucket)}</span>
      </summary>
      <div class="inc-samples">${samples}</div>
    </details>`;
}

function renderIncidentList(element, incidents, emptyText) {
  element.innerHTML = incidents.length
    ? incidents.map(renderIncident).join("")
    : `<p class="empty-state">${emptyText}</p>`;
}

function renderSpark(timeline) {
  const spark = $("spark");
  const tooltip = $("spark-tooltip");
  const max = Math.max(...timeline.map((bin) => bin.count), 1);
  spark.innerHTML = timeline
    .map((bin, i) => {
      const height = bin.count === 0 ? 0 : Math.max((bin.count / max) * 100, 4);
      const cls = bin.count === 0 ? "spark-bin empty" : "spark-bin";
      return `<div class="${cls}" data-i="${i}" style="height:${height}%"></div>`;
    })
    .join("");
  spark.querySelectorAll(".spark-bin").forEach((el) => {
    el.addEventListener("mousemove", (event) => {
      const bin = timeline[Number(el.dataset.i)];
      tooltip.textContent = `${fmtTime(bin.t)} — ${bin.count} error event${bin.count === 1 ? "" : "s"}`;
      tooltip.hidden = false;
      tooltip.style.left = `${event.clientX + 12}px`;
      tooltip.style.top = `${event.clientY - 30}px`;
    });
    el.addEventListener("mouseleave", () => { tooltip.hidden = true; });
  });
}

async function loadAnomalies() {
  const data = await getJSON(`/api/anomalies?minutes=${windowMinutes}`);
  $("tile-anomalies").textContent = data.error_events;
  const unassigned = data.incidents.filter((it) => it.bucket === "unassigned");
  const contained = data.incidents.filter((it) => it.bucket !== "unassigned");
  const unassignedTile = $("tile-unassigned");
  unassignedTile.textContent = unassigned.length;
  unassignedTile.dataset.zero = String(unassigned.length === 0);
  renderIncidentList(
    $("unassigned-list"),
    unassigned,
    "None. Every observed anomaly falls within an existing containment protocol. Most satisfactory."
  );
  renderIncidentList(
    $("contained-list"),
    contained,
    "No contained anomalies in this observation window."
  );
  renderSpark(data.timeline);
  const windowLabel = windowMinutes >= 60 ? windowMinutes / 60 + "h" : windowMinutes + "m";
  $("stream-note").textContent =
    `${data.error_events} error-severity events among ${data.total_events} observed ` +
    `in the last ${windowLabel}, across the entire installation.` +
    (data.truncated
      ? " Regrettably, the survey reached the archive's event limit — the newest portion of this window is not yet represented."
      : "");
  return true;
}

/* ---- findings ---- */

function renderFinding(finding) {
  const evidence = (finding.evidence || [])
    .map((item) => `<div>· ${esc(item)}</div>`)
    .join("");
  return `
    <details class="record">
      <summary>
        <span class="sev sev-${esc(finding.severity)}">${esc(finding.severity)}</span>
        <span class="record-title">${esc(finding.title)}</span>
        <span class="record-meta">${fmtTime(finding.created_at)}</span>
      </summary>
      <div class="record-body">
        <p>${esc(finding.summary)}</p>
        <p><strong>Causal assessment:</strong> ${esc(finding.suspected_cause)}</p>
        <p><strong>Corrective protocol:</strong> ${esc(finding.recommended_fix)}</p>
        ${evidence ? `<div class="inc-samples">${evidence}</div>` : ""}
      </div>
    </details>`;
}

async function loadFindings() {
  const data = await getJSON("/api/findings?limit=30");
  $("findings-list").innerHTML = data.findings.length
    ? data.findings.map(renderFinding).join("")
    : `<p class="empty-state">The archive holds no catalogued findings yet, Reclaimer.</p>`;
}

/* ---- remediations ---- */

function renderMeasure(item) {
  const link = item.pr_url
    ? `<a href="${esc(item.pr_url)}" target="_blank" rel="noopener">review the proposal</a>`
    : "";
  const statusClass =
    item.status === "pr-opened" || item.status === "validated" ? "sev-low" : "sev-high";
  return `
    <div class="record">
      <summary style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span class="sev ${statusClass}">${esc(item.status)}</span>
        <span class="record-title">${esc(item.target_id)}</span>
        <span class="inc-count">${esc(item.fingerprint)}</span>
        <span class="record-meta">${fmtTime(item.created_at)} ${link}</span>
      </summary>
    </div>`;
}

async function loadMeasures() {
  const data = await getJSON("/api/remediations?limit=30");
  $("measures-list").innerHTML = data.remediations.length
    ? data.remediations.map(renderMeasure).join("")
    : `<p class="empty-state">No corrective measures have been required. The installation functions within tolerances.</p>`;
}

/* ---- orchestration ---- */

async function refresh() {
  const results = await Promise.allSettled([
    loadAnomalies(),
    loadOverview(),
    loadFindings(),
    loadMeasures(),
  ]);
  const anomalyResult = results[0];
  if (anomalyResult.status === "rejected") {
    setUplink(false, anomalyResult.reason.message);
    $("unassigned-list").innerHTML =
      `<p class="error-state">archive query failed: ${esc(anomalyResult.reason.message)}</p>`;
    $("contained-list").innerHTML = "";
  } else {
    setUplink(true);
  }
  $("footer-refresh").textContent = `Last survey: ${fmtTime(new Date().toISOString())} · resurveying every 60s`;
}

$("window-picker").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-minutes]");
  if (!button) return;
  windowMinutes = Number(button.dataset.minutes);
  document.querySelectorAll("#window-picker button").forEach((el) =>
    el.classList.toggle("active", el === button)
  );
  loadAnomalies().catch((exc) => setUplink(false, exc.message));
});

refresh();
setInterval(refresh, REFRESH_MS);
