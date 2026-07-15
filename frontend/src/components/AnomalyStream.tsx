import type { AnomaliesResponse } from "../api/types";
import { GroupingStatus } from "./GroupingStatus";
import { Sparkline } from "./Sparkline";

const WINDOWS = [
  { minutes: 15, label: "15m" },
  { minutes: 60, label: "1h" },
  { minutes: 360, label: "6h" },
  { minutes: 1440, label: "24h" },
];

interface AnomalyStreamProps {
  data: AnomaliesResponse | null;
  loading: boolean;
  windowMinutes: number;
  onWindowChange: (minutes: number) => void;
  availableContainers: string[];
  selectedContainers: string[];
  onContainersChange: (containers: string[]) => void;
}

export function AnomalyStream({
  data,
  loading,
  windowMinutes,
  onWindowChange,
  availableContainers,
  selectedContainers,
  onContainersChange,
}: AnomalyStreamProps) {
  const windowLabel =
    windowMinutes >= 60 ? `${windowMinutes / 60}h` : `${windowMinutes}m`;
  const filtered = selectedContainers.length > 0;
  const scope = filtered
    ? `within the designated containment field of ${selectedContainers.length} ` +
      `container${selectedContainers.length === 1 ? "" : "s"}.`
    : "across the entire installation.";
  // While a survey is in flight, `data` still describes the *previous* window
  // or containment field. Reporting its counts under the new heading would
  // attribute one window's anomalies to another, so the tallies wait.
  const note = loading
    ? `Surveying the last ${windowLabel}, ${scope} The archive is extensive; ` +
      `a moment, Reclaimer.`
    : data
      ? `${data.error_events} error-severity events among ${data.total_events} observed ` +
        `in the last ${windowLabel}, ${scope}` +
        (data.truncated
          ? " Regrettably, this window exceeds my per-survey cataloging capacity — the oldest portion is catalogued, the remainder awaits." +
            (filtered
              ? ""
              : " May I suggest narrowing the containment field, Reclaimer?")
          : "")
      : "Error-severity events observed across the entire installation, regardless of containment protocol.";

  // A selected container may stop logging and drop out of the label values;
  // keep it listed so the operator can still uncheck it.
  const options = [
    ...availableContainers,
    ...selectedContainers.filter((c) => !availableContainers.includes(c)),
  ].sort();

  const toggle = (container: string) =>
    onContainersChange(
      selectedContainers.includes(container)
        ? selectedContainers.filter((c) => c !== container)
        : [...selectedContainers, container],
    );

  return (
    <section className="panel" aria-labelledby="stream-title">
      <div className="panel-head">
        <h2 id="stream-title">Anomaly stream</h2>
        <details className="container-picker">
          <summary aria-label="Containment field">
            {filtered
              ? `${selectedContainers.length} of ${options.length} containers`
              : "All containers"}
          </summary>
          <div className="container-menu" role="group" aria-label="Containers">
            <button
              type="button"
              className="container-reset"
              disabled={!filtered}
              onClick={() => onContainersChange([])}
            >
              Survey all containers
            </button>
            {options.length === 0 && (
              <p className="container-empty">
                No containers registered in this window, Reclaimer.
              </p>
            )}
            {options.map((container) => (
              <label key={container} className="container-option">
                <input
                  type="checkbox"
                  checked={selectedContainers.includes(container)}
                  onChange={() => toggle(container)}
                />
                <span>{container}</span>
              </label>
            ))}
          </div>
        </details>
        <div
          className="window-picker"
          role="group"
          aria-label="Observation window"
        >
          {WINDOWS.map((w) => (
            <button
              key={w.minutes}
              className={w.minutes === windowMinutes ? "active" : undefined}
              onClick={() => onWindowChange(w.minutes)}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>
      <p className="panel-note">{note}</p>
      {loading ? (
        <GroupingStatus text={`Surveying the last ${windowLabel}…`} />
      ) : (
        data && <Sparkline timeline={data.timeline} />
      )}
    </section>
  );
}
