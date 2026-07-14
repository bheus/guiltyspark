import type { Measure, RemediationsResponse } from "../api/types";
import { fmtTime } from "../lib/time";
import { Pager } from "./Pager";

// Live disposition of the opened PR, voiced for the operator. `pr_state` comes
// from GitHub; absent when the measure never reached a PR.
const PR_DISPOSITION: Record<string, { label: string; cls: string }> = {
  merged: { label: "Integrated", cls: "sev-low" },
  open: { label: "Awaiting authorization", cls: "sev-medium" },
  draft: { label: "Draft — pending", cls: "sev-medium" },
  closed: { label: "Dismissed", cls: "sev-high" },
  unknown: { label: "Disposition unverified", cls: "sev-medium" },
};

function MeasureRow({ item }: { item: Measure }) {
  const statusClass =
    item.status === "pr-opened" || item.status === "validated"
      ? "sev-low"
      : "sev-high";
  const disp = item.pr_state ? PR_DISPOSITION[item.pr_state] : undefined;
  const settledAt = item.merged_at || item.closed_at;
  return (
    <div className="record">
      <summary
        style={{
          display: "flex",
          alignItems: "center",
          gap: "10px",
          flexWrap: "wrap",
        }}
      >
        <span className={`sev ${statusClass}`}>{item.status}</span>
        {disp && (
          <span className={`sev ${disp.cls}`} title={item.pr_state}>
            {disp.label}
            {settledAt ? ` · ${fmtTime(settledAt)}` : ""}
          </span>
        )}
        <span className="record-title">{item.target_id}</span>
        <span className="inc-count">{item.fingerprint}</span>
        <span className="record-meta">
          {fmtTime(item.created_at)}{" "}
          {item.pr_url && (
            <a href={item.pr_url} target="_blank" rel="noopener">
              review the proposal
            </a>
          )}
        </span>
      </summary>
    </div>
  );
}

interface MeasuresPanelProps {
  data: RemediationsResponse | null;
  page: number;
  onPage: (page: number) => void;
}

export function MeasuresPanel({ data, page, onPage }: MeasuresPanelProps) {
  const measures = data?.remediations || [];
  const total = data?.total || 0;
  return (
    <section className="panel" aria-labelledby="measures-title">
      <div className="panel-head">
        <h2 id="measures-title">Corrective measures</h2>
      </div>
      <p className="panel-note">
        Protocols executed against catalogued anomalies. Final authorization
        remains yours, Reclaimer.
      </p>
      <div className="record-list">
        {measures.length > 0 ? (
          measures.map((item, i) => (
            <MeasureRow key={`${item.target_id}-${item.fingerprint}-${i}`} item={item} />
          ))
        ) : (
          <p className="empty-state">
            No corrective measures have been required. The installation functions
            within tolerances.
          </p>
        )}
      </div>
      <Pager page={page} total={total} onPage={onPage} />
    </section>
  );
}
