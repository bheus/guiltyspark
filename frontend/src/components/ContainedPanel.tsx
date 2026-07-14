import type { Incident } from "../api/types";
import { IncidentCard } from "./IncidentCard";

export function ContainedPanel({ contained }: { contained: Incident[] }) {
  return (
    <section className="panel" aria-labelledby="contained-title">
      <div className="panel-head">
        <h2 id="contained-title">Contained anomalies</h2>
      </div>
      <p className="panel-note">
        Anomalies matched to a configured containment protocol.
      </p>
      <div className="incident-list">
        {contained.length > 0 ? (
          contained.map((incident) => (
            <IncidentCard key={incident.fingerprint} incident={incident} />
          ))
        ) : (
          <p className="empty-state">
            No contained anomalies in this observation window.
          </p>
        )}
      </div>
    </section>
  );
}
