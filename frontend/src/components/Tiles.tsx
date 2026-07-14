interface TilesProps {
  anomalies: number | string;
  unassigned: number | string;
  findings: number | string;
  measures: number | string;
}

export function Tiles({ anomalies, unassigned, findings, measures }: TilesProps) {
  return (
    <section className="tiles" aria-label="Summary">
      <div className="tile">
        <span className="tile-value">{anomalies}</span>
        <span className="tile-label">Anomalies · window</span>
      </div>
      <div className="tile tile-alert">
        <span className="tile-value" data-zero={String(unassigned === 0)}>
          {unassigned}
        </span>
        <span className="tile-label">Outside containment</span>
      </div>
      <div className="tile">
        <span className="tile-value">{findings}</span>
        <span className="tile-label">Findings catalogued</span>
      </div>
      <div className="tile">
        <span className="tile-value">{measures}</span>
        <span className="tile-label">Corrective measures</span>
      </div>
    </section>
  );
}
