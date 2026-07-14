import { useState } from "react";
import type { TimelineBin } from "../api/types";
import { fmtTime } from "../lib/time";

interface TooltipState {
  text: string;
  x: number;
  y: number;
}

export function Sparkline({ timeline }: { timeline: TimelineBin[] }) {
  const [tip, setTip] = useState<TooltipState | null>(null);
  const max = Math.max(...timeline.map((bin) => bin.count), 1);

  return (
    <>
      <div className="spark" role="img" aria-label="Error events over time">
        {timeline.map((bin, i) => {
          const height = bin.count === 0 ? 0 : Math.max((bin.count / max) * 100, 4);
          const empty = bin.count === 0;
          return (
            <div
              key={i}
              className={empty ? "spark-bin empty" : "spark-bin"}
              style={{ height: `${height}%` }}
              onMouseMove={(event) =>
                setTip({
                  text: `${fmtTime(bin.t)} — ${bin.count} error event${
                    bin.count === 1 ? "" : "s"
                  }`,
                  x: event.clientX + 12,
                  y: event.clientY - 30,
                })
              }
              onMouseLeave={() => setTip(null)}
            />
          );
        })}
      </div>
      <div
        className="spark-tooltip"
        hidden={!tip}
        style={tip ? { left: tip.x, top: tip.y } : undefined}
      >
        {tip?.text}
      </div>
    </>
  );
}
