/* Dashboard client. Talks only to the JSON API (/api/*), so a future Vue
 * frontend can replace this file without backend changes. */

"use strict";

const REFRESH_MS = 60_000;
let windowMinutes = 60;
let unassignedByFp = {};

const $ = (id) => document.getElementById(id);
let groupsById = {};

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

function renderGroup(group) {
  const members = (group.members || [])
    .map((incident) => renderIncident(incident, { allowIgnore: true }))
    .join("");
  const services = (group.services || []).join(", ");
  const label =
    group.fingerprints && group.fingerprints.length > 1
      ? `Silence all ${group.fingerprints.length}`
      : "Silence";
  return `
    <details class="incident incident-group">
      <summary>
        ${severityChip(group.level)}
        <span class="inc-service">${esc(group.title)}</span>
        <span class="inc-count">×${group.count}</span>
        <span class="inc-when">last ${fmtNs(group.last_seen_ns)}</span>
        <span class="inc-actions">
          <button type="button" class="btn" data-pattern="${esc(group.id)}" title="Silence this class and future variants via a pattern">Silence pattern…</button>
          <button type="button" class="btn btn-danger" data-ignore-group="${esc((group.fingerprints || []).join(","))}" title="Silence exactly these anomalies now">${label}</button>
        </span>
      </summary>
      <div class="group-body">
        ${group.summary ? `<p class="group-summary">${esc(group.summary)}</p>` : ""}
        ${services ? `<p class="group-services">${esc(services)}</p>` : ""}
        ${renderPatternBox(group)}
        <div class="group-members">${members}</div>
      </div>
    </details>`;
}

function renderPatternBox(group) {
  const scope = (group.services || []).length === 1 ? group.services[0] : "";
  return `
    <div class="pattern-box" data-pattern-box="${esc(group.id)}" hidden>
      <div class="pattern-hint">The Monitor proposes a containment pattern. Review it — anything it matches, present or future, will be suppressed. Final authorization is yours, Reclaimer.</div>
      <label>Service scope <span class="pattern-sub">(blank = any service)</span>
        <input type="text" data-pattern-service value="${esc(scope)}" spellcheck="false" />
      </label>
      <label>Pattern <span class="pattern-sub">(Python regex, matched against each log line)</span>
        <textarea data-pattern-input rows="2" spellcheck="false" placeholder="Consulting the Monitor…"></textarea>
      </label>
      <div class="pattern-explanation" data-pattern-explanation></div>
      <div class="pattern-preview" data-pattern-preview></div>
      <label>Triage note <span class="pattern-sub">(optional)</span>
        <input type="text" data-pattern-note placeholder="Why this class is noise" />
      </label>
      <div class="pattern-actions">
        <button type="button" class="btn btn-danger" data-pattern-create="${esc(group.id)}">Establish rule</button>
        <button type="button" class="btn" data-pattern-cancel="${esc(group.id)}">Cancel</button>
      </div>
    </div>`;
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
  const silenceAll = $("silence-all");
  silenceAll.hidden = unassigned.length === 0;
  silenceAll.textContent = unassigned.length > 1 ? `Silence all ${unassigned.length}` : "Silence all";
  const groups = data.groups;
  groupsById = {};
  if (groups && groups.length) {
    groups.forEach((g) => { groupsById[g.id] = g; });
    $("unassigned-list").innerHTML = groups.map(renderGroup).join("");
  } else {
    renderIncidentList(
      $("unassigned-list"),
      unassigned,
      "None. Every observed anomaly falls within an existing containment protocol. Most satisfactory.",
      { allowIgnore: true }
    );
  }
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
  const rules = data.rules || [];
  const total = list.length + rules.length;
  $("silenced-count").textContent = total ? `${total} suppressed` : "none suppressed";
  const rulesHtml = rules.length
    ? `<div class="rules-block">${rules.map(renderRule).join("")}</div>`
    : "";
  const listHtml = list.length ? list.map(renderSilenced).join("") : "";
  $("silenced-list").innerHTML =
    rulesHtml + listHtml ||
    `<p class="empty-state">Nothing has been silenced. Every anomaly remains under my full attention, Reclaimer.</p>`;
}

function renderRule(rule) {
  const scope = rule.service
    ? `<span class="inc-service">${esc(rule.service)}</span>`
    : `<span class="inc-service">any service</span>`;
  return `
    <div class="incident rule-row">
      <div class="rule-head">
        <span class="rule-tag">PATTERN</span>
        ${scope}
        <span class="inc-when">since ${fmtTime(rule.created_at)}</span>
        <span class="inc-actions">
          <button type="button" class="btn" data-rule-remove="${esc(rule.id)}">Lift</button>
        </span>
      </div>
      <code class="rule-pattern">${esc(rule.pattern)}</code>
      ${rule.note ? `<div class="rule-note">${esc(rule.note)}</div>` : ""}
    </div>`;
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

/* ---- pattern silence rules ---- */

function groupSamples(group) {
  const lines = [];
  (group.members || []).forEach((m) =>
    (m.samples || []).forEach((line) => lines.push(line))
  );
  return lines;
}

function patternBox(id) {
  return $("unassigned-list").querySelector(`[data-pattern-box="${id}"]`);
}

function updatePatternPreview(box, group) {
  const value = box.querySelector("[data-pattern-input]").value.trim();
  const scope = box.querySelector("[data-pattern-service]").value.trim();
  const out = box.querySelector("[data-pattern-preview]");
  if (!value) { out.innerHTML = ""; return; }
  let regex;
  try {
    regex = new RegExp(value);
  } catch (exc) {
    out.innerHTML = `<span class="pattern-bad">Invalid regex: ${esc(exc.message)}</span>`;
    return;
  }
  const groupFps = new Set(group.fingerprints || []);
  let inClass = 0;
  const outside = [];
  Object.values(unassignedByFp).forEach((inc) => {
    if (scope && inc.service !== scope) return;
    if (!(inc.samples || []).some((line) => regex.test(line))) return;
    if (groupFps.has(inc.fingerprint)) inClass += 1;
    else outside.push(inc);
  });
  const total = inClass + outside.length;
  let html = `Matches ${total} anomal${total === 1 ? "y" : "ies"} in view — `
    + `${inClass} in this class`
    + (outside.length ? `, <span class="pattern-bad">${outside.length} outside</span>` : "")
    + ". <span class=\"pattern-sub\">Preview is approximate; the rule is applied server-side.</span>";
  if (outside.length) {
    html += `<div class="pattern-bad">Would also silence:<br>`
      + outside
          .map((inc) =>
            esc(`${inc.service}: ${((inc.samples && inc.samples[0]) || "").slice(0, 90)}`)
          )
          .join("<br>")
      + "</div>";
  }
  out.innerHTML = html;
}

async function openPatternBox(id) {
  const group = groupsById[id];
  const box = patternBox(id);
  if (!group || !box) return;
  box.hidden = false;
  const patternEl = box.querySelector("[data-pattern-input]");
  const explEl = box.querySelector("[data-pattern-explanation]");
  patternEl.value = "";
  patternEl.placeholder = "Consulting the Monitor…";
  explEl.textContent = "";
  box.querySelector("[data-pattern-preview]").innerHTML = "";
  try {
    const res = await sendJSON("POST", "/api/anomalies/suggest-pattern", {
      service: box.querySelector("[data-pattern-service]").value.trim(),
      samples: groupSamples(group),
    });
    patternEl.value = res.pattern || "";
    patternEl.placeholder = "Enter a regex, Reclaimer";
    explEl.textContent = res.explanation || "";
    if (res.warning) {
      explEl.innerHTML +=
        ` <span class="pattern-bad">The Monitor's proposal needs your correction: ${esc(res.warning)}</span>`;
    }
    updatePatternPreview(box, group);
  } catch (exc) {
    patternEl.placeholder = "Enter a regex, Reclaimer";
    explEl.innerHTML =
      `<span class="pattern-bad">I could not propose a pattern: ${esc(exc.message)}. You may compose one by hand.</span>`;
  }
}

async function createRule(id) {
  const box = patternBox(id);
  if (!box) return;
  const pattern = box.querySelector("[data-pattern-input]").value.trim();
  if (!pattern) return;
  await sendJSON("POST", "/api/anomalies/rules", {
    service: box.querySelector("[data-pattern-service]").value.trim(),
    pattern,
    note: box.querySelector("[data-pattern-note]").value.trim(),
  });
  await Promise.all([loadAnomalies(), loadSilenced()]);
}

function ignorePayload(fingerprint) {
  const incident = unassignedByFp[fingerprint] || {};
  return {
    fingerprint,
    service: incident.service || "",
    level: incident.level || "",
    count: incident.count || 0,
    sample: (incident.samples && incident.samples[0]) || "",
  };
}

$("unassigned-list").addEventListener("input", (event) => {
  const box = event.target.closest("[data-pattern-box]");
  if (!box) return;
  const group = groupsById[box.dataset.patternBox];
  if (group) updatePatternPreview(box, group);
});

$("unassigned-list").addEventListener("click", (event) => {
  const patternBtn = event.target.closest("button[data-pattern]");
  if (patternBtn) {
    event.preventDefault();
    const details = patternBtn.closest("details");
    if (details) details.open = true;
    guard(() => openPatternBox(patternBtn.dataset.pattern));
    return;
  }
  const createBtn = event.target.closest("button[data-pattern-create]");
  if (createBtn) {
    event.preventDefault();
    guard(() => createRule(createBtn.dataset.patternCreate));
    return;
  }
  const cancelBtn = event.target.closest("button[data-pattern-cancel]");
  if (cancelBtn) {
    event.preventDefault();
    const box = patternBox(cancelBtn.dataset.patternCancel);
    if (box) box.hidden = true;
    return;
  }
  const groupBtn = event.target.closest("button[data-ignore-group]");
  if (groupBtn) {
    event.preventDefault();
    const fingerprints = groupBtn.dataset.ignoreGroup.split(",").filter(Boolean);
    if (!fingerprints.length) return;
    const anomalies = fingerprints.map(ignorePayload);
    guard(async () => {
      await sendJSON("POST", "/api/anomalies/ignore-batch", { anomalies });
      await Promise.all([loadAnomalies(), loadSilenced()]);
    });
    return;
  }
  const button = event.target.closest("button[data-ignore]");
  if (!button) return;
  event.preventDefault();
  guard(async () => {
    await sendJSON("POST", "/api/anomalies/ignore", ignorePayload(button.dataset.ignore));
    await Promise.all([loadAnomalies(), loadSilenced()]);
  });
});

$("silence-all").addEventListener("click", (event) => {
  event.preventDefault();
  const incidents = Object.values(unassignedByFp);
  if (!incidents.length) return;
  if (!window.confirm(
    `Silence all ${incidents.length} unassigned anomalies? I shall designate every one as noise, Reclaimer, and suppress it from the stream.`
  )) {
    return;
  }
  const anomalies = incidents.map((incident) => ignorePayload(incident.fingerprint));
  guard(async () => {
    await sendJSON("POST", "/api/anomalies/ignore-batch", { anomalies });
    await Promise.all([loadAnomalies(), loadSilenced()]);
  });
});

$("silenced-list").addEventListener("click", (event) => {
  const ruleBtn = event.target.closest("button[data-rule-remove]");
  if (ruleBtn) {
    event.preventDefault();
    const id = ruleBtn.dataset.ruleRemove;
    guard(async () => {
      await sendJSON("DELETE", `/api/anomalies/rules?id=${encodeURIComponent(id)}`);
      await Promise.all([loadAnomalies(), loadSilenced()]);
    });
    return;
  }
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
