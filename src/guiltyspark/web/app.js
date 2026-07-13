/* Dashboard client. Talks only to the JSON API (/api/*), so a future Vue
 * frontend can replace this file without backend changes. */

"use strict";

const REFRESH_MS = 60_000;
let windowMinutes = 60;
let unassignedByFp = {};

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

async function sendJSON(method, path, body) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const response = await fetch(path, opts);
  const payload = await response.json().catch(() => ({}));
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

function renderIncident(incident, opts = {}) {
  const samples = (incident.samples || [])
    .map((line) => `<div>${esc(line)}</div>`)
    .join("");
  const tail = opts.allowIgnore
    ? `<span class="inc-actions"><button type="button" class="btn btn-danger" data-ignore="${esc(incident.fingerprint)}" title="Designate this anomaly as noise">Silence</button></span>`
    : `<span class="inc-bucket">${esc(incident.bucket)}</span>`;
  return `
    <details class="incident">
      <summary>
        ${severityChip(incident.level)}
        <span class="inc-service">${esc(incident.service)}</span>
        <span class="inc-count">×${incident.count}</span>
        <span class="inc-when">last ${fmtNs(incident.last_seen_ns)}</span>
        ${tail}
      </summary>
      <div class="inc-samples">${samples}</div>
    </details>`;
}

function renderIncidentList(element, incidents, emptyText, opts = {}) {
  element.innerHTML = incidents.length
    ? incidents.map((incident) => renderIncident(incident, opts)).join("")
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
  unassignedByFp = {};
  unassigned.forEach((it) => { unassignedByFp[it.fingerprint] = it; });
  const unassignedTile = $("tile-unassigned");
  unassignedTile.textContent = unassigned.length;
  unassignedTile.dataset.zero = String(unassigned.length === 0);
  renderIncidentList(
    $("unassigned-list"),
    unassigned,
    "None. Every observed anomaly falls within an existing containment protocol. Most satisfactory.",
    { allowIgnore: true }
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

/* ---- silenced anomalies ---- */

async function loadSilenced() {
  // Do not clobber a note the operator is actively typing on refresh.
  const active = document.activeElement;
  if (active && active.matches && active.matches("[data-note-input]")) return;
  const data = await getJSON("/api/anomalies/ignored");
  const list = data.ignored || [];
  $("silenced-count").textContent =
    list.length ? `${list.length} suppressed` : "none suppressed";
  $("silenced-list").innerHTML = list.length
    ? list.map(renderSilenced).join("")
    : `<p class="empty-state">Nothing has been silenced. Every anomaly remains under my full attention, Reclaimer.</p>`;
}

function renderSilenced(item) {
  const fp = esc(item.fingerprint);
  const level = item.level ? severityChip(item.level) : "";
  const service = item.service || item.fingerprint;
  const count = item.count
    ? `<span class="inc-count">×${item.count}</span>`
    : "";
  const sample = item.sample
    ? `<div class="inc-samples"><div>${esc(item.sample)}</div></div>`
    : "";
  return `
    <details class="incident">
      <summary>
        ${level}
        <span class="inc-service">${esc(service)}</span>
        ${count}
        <span class="inc-when">silenced ${fmtTime(item.created_at)}</span>
        <span class="inc-actions">
          <button type="button" class="btn" data-restore="${fp}">Restore</button>
        </span>
      </summary>
      <div class="silenced-body">
        <div class="silenced-fp">fingerprint ${fp}</div>
        ${sample}
        <div class="note-editor">
          <label for="note-${fp}">Triage note</label>
          <textarea id="note-${fp}" data-note-input="${fp}" rows="2"
            placeholder="Record why this anomaly is noise, for future reference.">${esc(item.note)}</textarea>
          <button type="button" class="btn btn-primary" data-note-save="${fp}">Save note</button>
        </div>
      </div>
    </details>`;
}

/* ---- containment protocols (target editor) ---- */

let protocols = [];

async function loadProtocols() {
  const data = await getJSON("/api/targets");
  protocols = data.targets || [];
  $("protocols-list").innerHTML = protocols.length
    ? protocols.map(renderProtocol).join("")
    : `<p class="empty-state">No containment protocols are established. The installation is unmonitored until you establish one, Reclaimer.</p>`;
}

function renderProtocol(target) {
  return `
    <div class="protocol-row">
      <span class="protocol-id">${esc(target.id)}</span>
      <span class="mode-chip" data-mode="${esc(target.mode)}">${esc(target.mode)}</span>
      <span class="protocol-repo">${esc(target.github_repo)}</span>
      <span class="protocol-actions">
        <button type="button" class="btn" data-edit="${esc(target.id)}">Amend</button>
        <button type="button" class="btn btn-danger" data-remove="${esc(target.id)}">Decommission</button>
      </span>
      <span class="protocol-query">${esc(target.loki_query)}</span>
    </div>`;
}

function openProtocolForm(target) {
  const form = $("protocol-form");
  const editing = Boolean(target);
  $("protocol-form-title").textContent = editing
    ? `Amend protocol · ${target.id}`
    : "Establish protocol";
  form.elements.id.value = target ? target.id : "";
  form.elements.id.readOnly = editing;
  form.elements.mode.value = target ? target.mode : "observe";
  form.elements.loki_url.value = target ? target.loki_url : "";
  form.elements.github_repo.value = target ? target.github_repo : "";
  form.elements.loki_query.value = target ? target.loki_query : "";
  form.elements.base_branch.value = target ? target.base_branch : "main";
  form.elements.max_changed_files.value = target ? target.max_changed_files : 12;
  form.elements.test_commands.value = target ? (target.test_commands || []).join("\n") : "";
  form.elements.allowed_paths.value = target ? (target.allowed_paths || []).join("\n") : "";
  setProtocolError("");
  form.hidden = false;
  form.elements.id.focus();
}

function protocolFormPayload(form) {
  const lines = (value) =>
    String(value || "")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
  return {
    id: form.elements.id.value.trim(),
    mode: form.elements.mode.value,
    loki_url: form.elements.loki_url.value.trim(),
    github_repo: form.elements.github_repo.value.trim(),
    loki_query: form.elements.loki_query.value.trim(),
    base_branch: form.elements.base_branch.value.trim() || "main",
    max_changed_files: Number(form.elements.max_changed_files.value) || 12,
    test_commands: lines(form.elements.test_commands.value),
    allowed_paths: lines(form.elements.allowed_paths.value),
  };
}

function setProtocolError(message) {
  const box = $("protocols-error");
  box.textContent = message;
  box.hidden = !message;
}

/* ---- orchestration ---- */

async function refresh() {
  const results = await Promise.allSettled([
    loadAnomalies(),
    loadOverview(),
    loadFindings(),
    loadMeasures(),
    loadSilenced(),
    loadProtocols(),
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

/* ---- interactions: silence / restore / protocols ---- */

async function guard(action) {
  try {
    await action();
  } catch (exc) {
    setUplink(false, exc.message);
  }
}

$("unassigned-list").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-ignore]");
  if (!button) return;
  event.preventDefault();
  const fingerprint = button.dataset.ignore;
  const incident = unassignedByFp[fingerprint] || {};
  guard(async () => {
    await sendJSON("POST", "/api/anomalies/ignore", {
      fingerprint,
      service: incident.service || "",
      level: incident.level || "",
      count: incident.count || 0,
      sample: (incident.samples && incident.samples[0]) || "",
    });
    await Promise.all([loadAnomalies(), loadSilenced()]);
  });
});

$("silenced-list").addEventListener("click", (event) => {
  const restoreBtn = event.target.closest("button[data-restore]");
  if (restoreBtn) {
    event.preventDefault();
    const fingerprint = restoreBtn.dataset.restore;
    guard(async () => {
      await sendJSON(
        "DELETE",
        `/api/anomalies/ignore?fingerprint=${encodeURIComponent(fingerprint)}`
      );
      await Promise.all([loadAnomalies(), loadSilenced()]);
    });
    return;
  }
  const saveBtn = event.target.closest("button[data-note-save]");
  if (!saveBtn) return;
  event.preventDefault();
  const fingerprint = saveBtn.dataset.noteSave;
  const input = document.getElementById(`note-${fingerprint}`);
  const note = input ? input.value : "";
  const original = saveBtn.textContent;
  guard(async () => {
    await sendJSON("POST", "/api/anomalies/note", { fingerprint, note });
    saveBtn.textContent = "Recorded";
    setTimeout(() => { saveBtn.textContent = original; }, 1500);
  });
});

$("protocols-list").addEventListener("click", (event) => {
  const editBtn = event.target.closest("button[data-edit]");
  if (editBtn) {
    const target = protocols.find((item) => item.id === editBtn.dataset.edit);
    if (target) openProtocolForm(target);
    return;
  }
  const removeBtn = event.target.closest("button[data-remove]");
  if (!removeBtn) return;
  const id = removeBtn.dataset.remove;
  if (!window.confirm(`Decommission containment protocol "${id}"? I shall cease monitoring its target.`)) {
    return;
  }
  guard(async () => {
    await sendJSON("DELETE", `/api/targets?id=${encodeURIComponent(id)}`);
    await Promise.all([loadProtocols(), loadOverview(), loadAnomalies()]);
  });
});

$("protocol-add").addEventListener("click", () => openProtocolForm(null));
$("protocol-cancel").addEventListener("click", () => {
  $("protocol-form").hidden = true;
});

$("protocol-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = protocolFormPayload(event.target);
  try {
    await sendJSON("POST", "/api/targets", payload);
  } catch (exc) {
    setProtocolError(`I regret I cannot commit this protocol, Reclaimer: ${exc.message}`);
    return;
  }
  $("protocol-form").hidden = true;
  await Promise.all([loadProtocols(), loadOverview(), loadAnomalies()]).catch((exc) =>
    setUplink(false, exc.message)
  );
});

refresh();
setInterval(refresh, REFRESH_MS);
