import { useEffect, useMemo, useState } from "react";
import type { AnomalyGroup, Incident } from "../api/types";
import { api } from "../api/client";
import { groupSamples } from "../lib/anomalies";

interface PatternEditorProps {
  group: AnomalyGroup;
  unassigned: Incident[];
  onCreate: (service: string, pattern: string, note: string) => Promise<void>;
  onCancel: () => void;
}

type Preview =
  | null
  | { kind: "invalid"; message: string }
  | { kind: "ok"; inClass: number; outside: Incident[]; total: number };

// Approximate client-side preview of what a regex would silence, mirroring
// updatePatternPreview from the vanilla app.js. The authoritative match happens
// server-side; this only warns the operator about over-broad patterns.
function usePreview(
  pattern: string,
  service: string,
  group: AnomalyGroup,
  unassigned: Incident[],
): Preview {
  return useMemo<Preview>(() => {
    const value = pattern.trim();
    if (!value) return null;
    let regex: RegExp;
    try {
      regex = new RegExp(value);
    } catch (exc) {
      return { kind: "invalid", message: (exc as Error).message };
    }
    const scope = service.trim();
    const groupFps = new Set(group.fingerprints || []);
    let inClass = 0;
    const outside: Incident[] = [];
    for (const inc of unassigned) {
      if (scope && inc.service !== scope) continue;
      if (!(inc.samples || []).some((line) => regex.test(line))) continue;
      if (groupFps.has(inc.fingerprint)) inClass += 1;
      else outside.push(inc);
    }
    return { kind: "ok", inClass, outside, total: inClass + outside.length };
  }, [pattern, service, group, unassigned]);
}

export function PatternEditor({ group, unassigned, onCreate, onCancel }: PatternEditorProps) {
  const initialScope = (group.services || []).length === 1 ? group.services[0] : "";
  const [service, setService] = useState(initialScope);
  const [pattern, setPattern] = useState("");
  const [note, setNote] = useState("");
  const [explanation, setExplanation] = useState("");
  const [warning, setWarning] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  // Ask the Monitor to propose a pattern when the editor opens.
  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .suggestPattern(initialScope, groupSamples(group))
      .then((res) => {
        if (!live) return;
        setPattern(res.pattern || "");
        setExplanation(res.explanation || "");
        setWarning(res.warning || "");
      })
      .catch((exc: Error) => {
        if (!live) return;
        setError(
          `I could not propose a pattern: ${exc.message}. You may compose one by hand.`,
        );
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const preview = usePreview(pattern, service, group, unassigned);

  return (
    <div className="pattern-box">
      <div className="pattern-hint">
        The Monitor proposes a containment pattern. Review it — anything it
        matches, present or future, will be suppressed. Final authorization is
        yours, Reclaimer.
      </div>
      <label>
        Service scope <span className="pattern-sub">(blank = any service)</span>
        <input
          type="text"
          value={service}
          spellCheck={false}
          onChange={(e) => setService(e.target.value)}
        />
      </label>
      <label>
        Pattern{" "}
        <span className="pattern-sub">
          (Python regex, matched against each log line)
        </span>
        <textarea
          rows={2}
          spellCheck={false}
          value={pattern}
          placeholder={loading ? "Consulting the Monitor…" : "Enter a regex, Reclaimer"}
          onChange={(e) => setPattern(e.target.value)}
        />
      </label>
      <div className="pattern-explanation">
        {explanation}
        {warning && (
          <span className="pattern-bad">
            {" "}
            The Monitor's proposal needs your correction: {warning}
          </span>
        )}
        {error && <span className="pattern-bad">{error}</span>}
      </div>
      <div className="pattern-preview">
        {preview?.kind === "invalid" && (
          <span className="pattern-bad">Invalid regex: {preview.message}</span>
        )}
        {preview?.kind === "ok" && (
          <>
            Matches {preview.total} anomal{preview.total === 1 ? "y" : "ies"} in
            view — {preview.inClass} in this class
            {preview.outside.length > 0 && (
              <span className="pattern-bad">, {preview.outside.length} outside</span>
            )}
            .{" "}
            <span className="pattern-sub">
              Preview is approximate; the rule is applied server-side.
            </span>
            {preview.outside.length > 0 && (
              <div className="pattern-bad">
                Would also silence:
                {preview.outside.map((inc, i) => (
                  <div key={i}>
                    {inc.service}: {((inc.samples && inc.samples[0]) || "").slice(0, 90)}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
      <label>
        Triage note <span className="pattern-sub">(optional)</span>
        <input
          type="text"
          value={note}
          placeholder="Why this class is noise"
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <div className="pattern-actions">
        <button
          type="button"
          className="btn btn-danger"
          disabled={!pattern.trim()}
          onClick={() => onCreate(service.trim(), pattern.trim(), note.trim())}
        >
          Establish rule
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
