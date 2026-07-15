// The Forerunner hard-light lattice shown while work is in flight — Codex
// clustering anomalies off-thread (groups_pending), or a survey of a wide
// window still being gathered. Ported from the vanilla #grouping-status markup.
interface GroupingStatusProps {
  text?: string;
}

export function GroupingStatus({
  text = "Cataloging anomalies into containment classes…",
}: GroupingStatusProps) {
  return (
    <div className="grouping-status" role="status">
      <span className="grouping-grid" aria-hidden="true">
        {Array.from({ length: 12 }, (_, i) => (
          <i key={i} />
        ))}
      </span>
      <span className="grouping-status-text">{text}</span>
    </div>
  );
}
