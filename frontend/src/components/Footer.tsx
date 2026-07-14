import type { OverviewTarget } from "../api/types";
import { fmtTime } from "../lib/time";

interface FooterProps {
  targets: OverviewTarget[];
  lastSurvey: Date | null;
}

export function Footer({ targets, lastSurvey }: FooterProps) {
  const protocols = targets.map((t) => `${t.id} [${t.mode}]`).join(" · ");
  return (
    <footer className="footing">
      <span>
        {protocols
          ? `Containment protocols: ${protocols}`
          : "No containment protocols configured."}
      </span>
      <span>
        {lastSurvey
          ? `Last survey: ${fmtTime(lastSurvey.toISOString())} · resurveying every 60s`
          : ""}
      </span>
    </footer>
  );
}
