import { useState } from "react";
import type { AnomalyGroup, Incident } from "../api/types";
import { fmtNs } from "../lib/time";
import { IncidentCard } from "./IncidentCard";
import { PatternEditor } from "./PatternEditor";
import { SevChip } from "./SevChip";

interface IncidentGroupProps {
  group: AnomalyGroup;
  unassigned: Incident[];
  onSilence: (incident: Incident) => void;
  onSilenceGroup: (fingerprints: string[]) => void;
  onCreateRule: (
    group: AnomalyGroup,
    service: string,
    pattern: string,
    note: string,
  ) => Promise<void>;
}

export function IncidentGroup({
  group,
  unassigned,
  onSilence,
  onSilenceGroup,
  onCreateRule,
}: IncidentGroupProps) {
  // Controlled open + local patternOpen persist across polls because this
  // component stays mounted under a stable key (group.id).
  const [open, setOpen] = useState(false);
  const [patternOpen, setPatternOpen] = useState(false);
  const services = (group.services || []).join(", ");
  const label =
    group.fingerprints && group.fingerprints.length > 1
      ? `Silence all ${group.fingerprints.length}`
      : "Silence";

  return (
    <details
      className="incident incident-group"
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary>
        <SevChip level={group.level} />
        <span className="inc-service">{group.title}</span>
        <span className="inc-count">×{group.count}</span>
        <span className="inc-when">last {fmtNs(group.last_seen_ns)}</span>
        <span className="inc-actions">
          <button
            type="button"
            className="btn"
            title="Silence this class and future variants via a pattern"
            onClick={(event) => {
              event.preventDefault();
              setOpen(true);
              setPatternOpen(true);
            }}
          >
            Silence pattern…
          </button>
          <button
            type="button"
            className="btn btn-danger"
            title="Silence exactly these anomalies now"
            onClick={(event) => {
              event.preventDefault();
              onSilenceGroup(group.fingerprints || []);
            }}
          >
            {label}
          </button>
        </span>
      </summary>
      <div className="group-body">
        {group.summary && <p className="group-summary">{group.summary}</p>}
        {services && <p className="group-services">{services}</p>}
        {patternOpen && (
          <PatternEditor
            group={group}
            unassigned={unassigned}
            onCreate={async (service, pattern, note) => {
              await onCreateRule(group, service, pattern, note);
              setPatternOpen(false);
            }}
            onCancel={() => setPatternOpen(false)}
          />
        )}
        <div className="group-members">
          {(group.members || []).map((member) => (
            <IncidentCard
              key={member.fingerprint}
              incident={member}
              onSilence={onSilence}
            />
          ))}
        </div>
      </div>
    </details>
  );
}
