import { useState } from "react";
import type { IgnoredResponse, SilenceRule, SilencedItem } from "../api/types";
import { fmtTime } from "../lib/time";
import { SevChip } from "./SevChip";

interface SaveButtonProps {
  onSave: () => Promise<void>;
  idle: string;
  className?: string;
}

// Shows a transient "Recorded" confirmation after a successful save, mirroring
// the setTimeout label swap in the vanilla app.js.
function SaveButton({ onSave, idle, className = "btn btn-primary" }: SaveButtonProps) {
  const [label, setLabel] = useState(idle);
  return (
    <button
      type="button"
      className={className}
      onClick={async () => {
        await onSave();
        setLabel("Recorded");
        window.setTimeout(() => setLabel(idle), 1500);
      }}
    >
      {label}
    </button>
  );
}

interface SilencedCardProps {
  item: SilencedItem;
  onRestore: (fingerprint: string) => void;
  onSaveNote: (fingerprint: string, note: string) => Promise<void>;
}

// `note` is seeded once into local state; subsequent polls do not overwrite an
// in-progress edit (the card stays mounted under a stable fingerprint key).
function SilencedCard({ item, onRestore, onSaveNote }: SilencedCardProps) {
  const [note, setNote] = useState(item.note);
  const service = item.service || item.fingerprint;
  return (
    <details className="incident">
      <summary>
        {item.level && <SevChip level={item.level} />}
        <span className="inc-service">{service}</span>
        {item.count ? <span className="inc-count">×{item.count}</span> : null}
        <span className="inc-when">silenced {fmtTime(item.created_at)}</span>
        <span className="inc-actions">
          <button
            type="button"
            className="btn"
            onClick={(event) => {
              event.preventDefault();
              onRestore(item.fingerprint);
            }}
          >
            Restore
          </button>
        </span>
      </summary>
      <div className="silenced-body">
        <div className="silenced-fp">fingerprint {item.fingerprint}</div>
        {item.sample && (
          <div className="inc-samples">
            <div>{item.sample}</div>
          </div>
        )}
        <div className="note-editor">
          <label htmlFor={`note-${item.fingerprint}`}>Triage note</label>
          <textarea
            id={`note-${item.fingerprint}`}
            rows={2}
            value={note}
            placeholder="Record why this anomaly is noise, for future reference."
            onChange={(e) => setNote(e.target.value)}
          />
          <SaveButton
            idle="Save note"
            onSave={() => onSaveNote(item.fingerprint, note)}
          />
        </div>
      </div>
    </details>
  );
}

interface RuleCardProps {
  rule: SilenceRule;
  onLift: (id: number) => void;
  onSave: (id: number, title: string, note: string) => Promise<void>;
}

function RuleCard({ rule, onLift, onSave }: RuleCardProps) {
  const defaultTitle =
    rule.title ||
    (rule.service ? `${rule.service} pattern containment` : "Pattern containment");
  const [title, setTitle] = useState(defaultTitle);
  const [note, setNote] = useState(rule.note);
  const scope = rule.service ? `service: ${rule.service}` : "any service";

  return (
    <div className="incident rule-row">
      <div className="rule-head">
        <span className="rule-tag">PATTERN</span>
        <span className="inc-service">{defaultTitle}</span>
        <span className="rule-scope">{scope}</span>
        <span className="inc-when">since {fmtTime(rule.created_at)}</span>
        <span className="inc-actions">
          <button type="button" className="btn" onClick={() => onLift(rule.id)}>
            Lift
          </button>
        </span>
      </div>
      <code className="rule-pattern">{rule.pattern}</code>
      {rule.note && <div className="rule-note">{rule.note}</div>}
      <details className="rule-editor">
        <summary>Amend record</summary>
        <label>
          Containment label
          <input
            type="text"
            value={title}
            maxLength={200}
            placeholder="Describe this silenced anomaly"
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label>
          Triage note
          <textarea
            rows={2}
            value={note}
            placeholder="Record why this pattern is noise, for future reference."
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <SaveButton idle="Save record" onSave={() => onSave(rule.id, title, note)} />
      </details>
    </div>
  );
}

interface SilencedPanelProps {
  data: IgnoredResponse | null;
  onRestore: (fingerprint: string) => void;
  onSaveNote: (fingerprint: string, note: string) => Promise<void>;
  onLiftRule: (id: number) => void;
  onSaveRule: (id: number, title: string, note: string) => Promise<void>;
}

export function SilencedPanel({
  data,
  onRestore,
  onSaveNote,
  onLiftRule,
  onSaveRule,
}: SilencedPanelProps) {
  const list = data?.ignored || [];
  const rules = data?.rules || [];
  const total = list.length + rules.length;

  return (
    <section className="panel" aria-labelledby="silenced-title">
      <div className="panel-head">
        <h2 id="silenced-title">Silenced anomalies</h2>
        <span className="panel-tag">
          {total ? `${total} suppressed` : "none suppressed"}
        </span>
      </div>
      <p className="panel-note">
        Anomalies you have designated as noise. I have suppressed them from the
        stream, as instructed. Restore any one and I shall resume cataloguing it.
      </p>
      <div className="incident-list">
        {total === 0 ? (
          <p className="empty-state">
            Nothing has been silenced. Every anomaly remains under my full
            attention, Reclaimer.
          </p>
        ) : (
          <>
            {rules.length > 0 && (
              <div className="rules-block">
                {rules.map((rule) => (
                  <RuleCard
                    key={rule.id}
                    rule={rule}
                    onLift={onLiftRule}
                    onSave={onSaveRule}
                  />
                ))}
              </div>
            )}
            {list.map((item) => (
              <SilencedCard
                key={item.fingerprint}
                item={item}
                onRestore={onRestore}
                onSaveNote={onSaveNote}
              />
            ))}
          </>
        )}
      </div>
    </section>
  );
}
