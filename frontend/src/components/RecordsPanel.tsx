import type { Finding, FindingsResponse } from "../api/types";
import { fmtTime } from "../lib/time";
import { Pager } from "./Pager";

function FindingCard({ finding }: { finding: Finding }) {
  return (
    <details className="record">
      <summary>
        <span className={`sev sev-${finding.severity}`}>{finding.severity}</span>
        <span className="record-title">{finding.title}</span>
        <span className="record-meta">{fmtTime(finding.created_at)}</span>
      </summary>
      <div className="record-body">
        <p>{finding.summary}</p>
        <p>
          <strong>Causal assessment:</strong> {finding.suspected_cause}
        </p>
        <p>
          <strong>Corrective protocol:</strong> {finding.recommended_fix}
        </p>
        {(finding.evidence || []).length > 0 && (
          <div className="inc-samples">
            {finding.evidence.map((item, i) => (
              <div key={i}>· {item}</div>
            ))}
          </div>
        )}
      </div>
    </details>
  );
}

interface RecordsPanelProps {
  data: FindingsResponse | null;
  page: number;
  onPage: (page: number) => void;
}

export function RecordsPanel({ data, page, onPage }: RecordsPanelProps) {
  const findings = data?.findings || [];
  const total = data?.total || 0;
  return (
    <section className="panel" aria-labelledby="findings-title">
      <div className="panel-head">
        <h2 id="findings-title">Containment records</h2>
      </div>
      <p className="panel-note">
        Findings catalogued by my analysis. The cataloging was quite thorough, I
        assure you.
      </p>
      <div className="record-list">
        {findings.length > 0 ? (
          findings.map((finding, i) => (
            <FindingCard key={`${finding.title}-${i}`} finding={finding} />
          ))
        ) : (
          <p className="empty-state">
            The archive holds no catalogued findings yet, Reclaimer.
          </p>
        )}
      </div>
      <Pager page={page} total={total} onPage={onPage} />
    </section>
  );
}
