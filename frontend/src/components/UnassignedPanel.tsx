import type { AnomalyGroup, Incident } from "../api/types";
import { GroupingStatus } from "./GroupingStatus";
import { IncidentCard } from "./IncidentCard";
import { IncidentGroup } from "./IncidentGroup";

interface UnassignedPanelProps {
  unassigned: Incident[];
  groups?: AnomalyGroup[];
  groupsPending?: boolean;
  error?: string;
  onSilence: (incident: Incident) => void;
  onSilenceAll: () => void;
  onSilenceGroup: (fingerprints: string[]) => void;
  onCreateRule: (
    group: AnomalyGroup,
    service: string,
    pattern: string,
    note: string,
  ) => Promise<void>;
}

export function UnassignedPanel({
  unassigned,
  groups,
  groupsPending,
  error,
  onSilence,
  onSilenceAll,
  onSilenceGroup,
  onCreateRule,
}: UnassignedPanelProps) {
  const hasGroups = groups && groups.length > 0;
  const silenceAllLabel =
    unassigned.length > 1 ? `Silence all ${unassigned.length}` : "Silence all";

  return (
    <section className="panel panel-breach" aria-labelledby="unassigned-title">
      <div className="panel-head">
        <h2 id="unassigned-title">Unassigned anomalies</h2>
        <span className="panel-tag">outside all containment protocols</span>
        {unassigned.length > 0 && (
          <button type="button" className="btn btn-danger" onClick={onSilenceAll}>
            {silenceAllLabel}
          </button>
        )}
      </div>
      <p className="panel-note">
        These malfunctions match no configured target. I cannot prepare
        corrective measures for them until you assign a protocol, Reclaimer — or,
        should you judge them mere noise, you may silence them.
      </p>
      {groupsPending && <GroupingStatus />}
      <div className="incident-list">
        {error ? (
          <p className="error-state">archive query failed: {error}</p>
        ) : hasGroups ? (
          groups!.map((group) => (
            <IncidentGroup
              key={group.id}
              group={group}
              unassigned={unassigned}
              onSilence={onSilence}
              onSilenceGroup={onSilenceGroup}
              onCreateRule={onCreateRule}
            />
          ))
        ) : unassigned.length > 0 ? (
          unassigned.map((incident) => (
            <IncidentCard
              key={incident.fingerprint}
              incident={incident}
              onSilence={onSilence}
            />
          ))
        ) : (
          <p className="empty-state">
            None. Every observed anomaly falls within an existing containment
            protocol. Most satisfactory.
          </p>
        )}
      </div>
    </section>
  );
}
