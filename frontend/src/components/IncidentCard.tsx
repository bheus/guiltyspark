import type { Incident } from "../api/types";
import { fmtNs } from "../lib/time";
import { SevChip } from "./SevChip";

interface IncidentCardProps {
  incident: Incident;
  // When set, renders a Silence action instead of the bucket label.
  onSilence?: (incident: Incident) => void;
}

// Uncontrolled <details>: React reuses the DOM node across polls (stable key by
// fingerprint), so the operator's open/closed toggle survives background
// refetches — the whole point of the migration.
export function IncidentCard({ incident, onSilence }: IncidentCardProps) {
  return (
    <details className="incident">
      <summary>
        <SevChip level={incident.level} />
        <span className="inc-service">{incident.service}</span>
        <span className="inc-count">×{incident.count}</span>
        <span className="inc-when">last {fmtNs(incident.last_seen_ns)}</span>
        {onSilence ? (
          <span className="inc-actions">
            <button
              type="button"
              className="btn btn-danger"
              title="Designate this anomaly as noise"
              onClick={(event) => {
                event.preventDefault();
                onSilence(incident);
              }}
            >
              Silence
            </button>
          </span>
        ) : (
          <span className="inc-bucket">{incident.bucket}</span>
        )}
      </summary>
      <div className="inc-samples">
        {(incident.samples || []).map((line, i) => (
          <div key={i}>{line}</div>
        ))}
      </div>
    </details>
  );
}
