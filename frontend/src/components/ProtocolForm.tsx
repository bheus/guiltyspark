import { useState } from "react";
import type { Target } from "../api/types";

const MODES = ["observe", "fix", "draft-pr", "pr"];

// Form draft as a flat string map; textareas stay newline-joined and are split
// back to arrays on submit (ported from protocolFormPayload).
interface FormState {
  id: string;
  mode: string;
  loki_url: string;
  github_repo: string;
  loki_query: string;
  base_branch: string;
  max_changed_files: string;
  test_commands: string;
  allowed_paths: string;
  expected_logs_path: string;
}

function initialState(target: Target | null): FormState {
  return {
    id: target?.id ?? "",
    mode: target?.mode ?? "observe",
    loki_url: target?.loki_url ?? "",
    github_repo: target?.github_repo ?? "",
    loki_query: target?.loki_query ?? "",
    base_branch: target?.base_branch ?? "main",
    max_changed_files: String(target?.max_changed_files ?? 12),
    test_commands: (target?.test_commands ?? []).join("\n"),
    allowed_paths: (target?.allowed_paths ?? []).join("\n"),
    expected_logs_path: target?.expected_logs_path ?? "",
  };
}

function lines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

interface ProtocolFormProps {
  editing: Target | null;
  error: string;
  onSubmit: (payload: Target) => void;
  onCancel: () => void;
}

export function ProtocolForm({ editing, error, onSubmit, onCancel }: ProtocolFormProps) {
  const [form, setForm] = useState<FormState>(() => initialState(editing));
  const [releaseObserved, setReleaseObserved] = useState(false);
  const set = (key: keyof FormState) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>,
  ) => setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    onSubmit({
      id: form.id.trim(),
      mode: form.mode,
      loki_url: form.loki_url.trim(),
      github_repo: form.github_repo.trim(),
      loki_query: form.loki_query.trim(),
      base_branch: form.base_branch.trim() || "main",
      max_changed_files: Number(form.max_changed_files) || 12,
      test_commands: lines(form.test_commands),
      allowed_paths: lines(form.allowed_paths),
      expected_logs_path: form.expected_logs_path.trim(),
      release_observed: releaseObserved,
    });
  };

  const held = editing?.held_remediations ?? 0;
  const canRelease = Boolean(editing) && form.mode !== "observe" && held > 0;

  return (
    <form className="protocol-form" onSubmit={submit}>
      <h3>{editing ? `Amend protocol · ${editing.id}` : "Establish protocol"}</h3>
      <div className="field-grid">
        <label>
          Designation (id)
          <input
            name="id"
            autoComplete="off"
            spellCheck={false}
            required
            readOnly={Boolean(editing)}
            value={form.id}
            onChange={set("id")}
          />
        </label>
        <label>
          Operational mode
          <select name="mode" value={form.mode} onChange={set("mode")}>
            {MODES.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </label>
        <label>
          Archive endpoint (loki_url)
          <input
            autoComplete="off"
            spellCheck={false}
            required
            value={form.loki_url}
            onChange={set("loki_url")}
          />
        </label>
        <label>
          GitHub repository (owner/name)
          <input
            autoComplete="off"
            spellCheck={false}
            required
            value={form.github_repo}
            onChange={set("github_repo")}
          />
        </label>
        <label className="field-wide">
          Log stream selector (loki_query)
          <input
            autoComplete="off"
            spellCheck={false}
            required
            value={form.loki_query}
            onChange={set("loki_query")}
          />
        </label>
        <label>
          Base branch
          <input
            autoComplete="off"
            spellCheck={false}
            placeholder="main"
            value={form.base_branch}
            onChange={set("base_branch")}
          />
        </label>
        <label>
          Max changed files
          <input
            type="number"
            min={1}
            value={form.max_changed_files}
            onChange={set("max_changed_files")}
          />
        </label>
        <label className="field-wide">
          Verification sequence (test_commands, one per line)
          <textarea
            rows={2}
            spellCheck={false}
            value={form.test_commands}
            onChange={set("test_commands")}
          />
        </label>
        <label className="field-wide">
          Permitted paths (allowed_paths, one per line)
          <textarea
            rows={2}
            spellCheck={false}
            value={form.allowed_paths}
            onChange={set("allowed_paths")}
          />
        </label>
        <label className="field-wide">
          Expected-logs dossier (expected_logs_path, repo-relative)
          <input
            autoComplete="off"
            spellCheck={false}
            placeholder="docs/EXPECTED_LOGS.md"
            value={form.expected_logs_path}
            onChange={set("expected_logs_path")}
          />
        </label>
      </div>
      <p className="form-note">
        A protocol beyond <em>observe</em> requires both a verification sequence
        and permitted paths before I may act.
      </p>
      {canRelease && (
        <label className="release-observed">
          <input
            type="checkbox"
            checked={releaseObserved}
            onChange={(event) => setReleaseObserved(event.target.checked)}
          />
          <span>
            Release {held} catalogued anomal{held === 1 ? "y" : "ies"} for
            corrective action. The Monitor will process at most a small batch each
            cycle. Final authorization remains yours, Reclaimer.
          </span>
        </label>
      )}
      {error && <div className="error-state">{error}</div>}
      <div className="form-actions">
        <button type="submit" className="btn btn-primary">
          Commit protocol
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
