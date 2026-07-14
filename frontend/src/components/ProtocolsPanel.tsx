import { useState } from "react";
import type { Target } from "../api/types";
import { ProtocolForm } from "./ProtocolForm";

function ProtocolRow({
  target,
  onEdit,
  onRemove,
}: {
  target: Target;
  onEdit: (target: Target) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="protocol-row">
      <span className="protocol-id">{target.id}</span>
      <span className="mode-chip" data-mode={target.mode}>
        {target.mode}
      </span>
      <span className="protocol-repo">{target.github_repo}</span>
      <span className="protocol-actions">
        <button type="button" className="btn" onClick={() => onEdit(target)}>
          Amend
        </button>
        <button
          type="button"
          className="btn btn-danger"
          onClick={() => onRemove(target.id)}
        >
          Decommission
        </button>
      </span>
      <span className="protocol-query">{target.loki_query}</span>
    </div>
  );
}

interface ProtocolsPanelProps {
  targets: Target[];
  onSave: (payload: Target) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

export function ProtocolsPanel({ targets, onSave, onDelete }: ProtocolsPanelProps) {
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Target | null>(null);
  const [error, setError] = useState("");

  const open = (target: Target | null) => {
    setEditing(target);
    setError("");
    setFormOpen(true);
  };

  const remove = (id: string) => {
    if (
      !window.confirm(
        `Decommission containment protocol "${id}"? I shall cease monitoring its target.`,
      )
    ) {
      return;
    }
    void onDelete(id);
  };

  const submit = async (payload: Target) => {
    try {
      await onSave(payload);
    } catch (exc) {
      setError(
        `I regret I cannot commit this protocol, Reclaimer: ${(exc as Error).message}`,
      );
      return;
    }
    setFormOpen(false);
  };

  return (
    <section className="panel" aria-labelledby="protocols-title">
      <div className="panel-head">
        <h2 id="protocols-title">Containment protocols</h2>
        <button type="button" className="btn btn-primary" onClick={() => open(null)}>
          Establish protocol
        </button>
      </div>
      <p className="panel-note">
        The installation's configured targets. You may amend, establish, or
        decommission a protocol here, Reclaimer; I shall adopt your directives
        within the cycle. Final authorization remains yours.
      </p>
      <div className="record-list">
        {targets.length > 0 ? (
          targets.map((target) => (
            <ProtocolRow
              key={target.id}
              target={target}
              onEdit={open}
              onRemove={remove}
            />
          ))
        ) : (
          <p className="empty-state">
            No containment protocols are established. The installation is
            unmonitored until you establish one, Reclaimer.
          </p>
        )}
      </div>
      {formOpen && (
        <ProtocolForm
          key={editing?.id ?? "new"}
          editing={editing}
          error={error}
          onSubmit={submit}
          onCancel={() => setFormOpen(false)}
        />
      )}
    </section>
  );
}
