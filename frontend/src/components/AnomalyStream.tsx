import type { AnomaliesResponse } from "../api/types";
import { Sparkline } from "./Sparkline";

const WINDOWS = [
  { minutes: 15, label: "15m" },
  { minutes: 60, label: "1h" },
  { minutes: 360, label: "6h" },
  { minutes: 1440, label: "24h" },
];

interface AnomalyStreamProps {
  data: AnomaliesResponse | null;
  windowMinutes: number;
  onWindowChange: (minutes: number) => void;
}

export function AnomalyStream({
  data,
  windowMinutes,
  onWindowChange,
}: AnomalyStreamProps) {
  const windowLabel =
    windowMinutes >= 60 ? `${windowMinutes / 60}h` : `${windowMinutes}m`;
  const note = data
    ? `${data.error_events} error-severity events among ${data.total_events} observed ` +
      `in the last ${windowLabel}, across the entire installation.` +
      (data.truncated
        ? " Regrettably, the survey reached the archive's event limit — the newest portion of this window is not yet represented."
        : "")
    : "Error-severity events observed across the entire installation, regardless of containment protocol.";

  return (
    <section className="panel" aria-labelledby="stream-title">
      <div className="panel-head">
        <h2 id="stream-title">Anomaly stream</h2>
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
      {data && <Sparkline timeline={data.timeline} />}
    </section>
  );
}
