// The Forerunner hard-light lattice shown while Codex clusters anomalies
// off-thread (groups_pending). Ported from the vanilla #grouping-status markup.
export function GroupingStatus() {
  return (
    <div className="grouping-status">
      <span className="grouping-grid" aria-hidden="true">
        {Array.from({ length: 12 }, (_, i) => (
          <i key={i} />
        ))}
      </span>
      <span className="grouping-status-text">
        Cataloging anomalies into containment classes…
      </span>
    </div>
  );
}
