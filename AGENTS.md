# AGENTS.md

Guidance for Codex (and any other agentic tooling) working in this repository.

## What this project is

`guiltyspark` is an AI-driven log monitor — an always-on agent that watches Loki logs,
detects unknown problems, and turns useful findings into actionable remediation. It runs
as a Docker container on an always-on host (e.g. a Raspberry Pi).

The name is a reference to **343 Guilty Spark**, the Monitor of Installation 04 from Halo.
That is not incidental branding — see the voice section below.

### Core loop

1. Query Loki for recent logs (`src/guiltyspark/loki.py`).
2. Group noisy log lines into incidents (`src/guiltyspark/grouping.py`).
3. Ask Codex (via `codex exec`) to explain likely bugs, misconfigurations,
   and improvement opportunities (`src/guiltyspark/agent.py`).
4. Store findings in SQLite so repeated noise does not alert forever (`src/guiltyspark/state.py`).
5. Associate each incident with its configured GitHub repository target (`src/guiltyspark/targets.py`).
6. In an isolated clone, let Codex prepare a minimal fix and regression tests, enforce patch
   policy, run validation, and optionally open a PR (`src/guiltyspark/remediation.py`).

### Key modules

| File | Responsibility |
| --- | --- |
| `cli.py` | Entry point; subcommands `once`, `daemon`, `doctor`, `replay`, `dashboard`. |
| `dashboard.py` | Web dashboard: JSON API + static frontend (`web/`); classifies live errors into target buckets. |
| `config.py` | Environment-variable-driven `Settings`. |
| `loki.py` | Loki query client. |
| `grouping.py` | Collapses raw log lines into incidents. |
| `agent.py` | Codex analysis; holds `AGENT_INSTRUCTIONS` and JSON finding schema. |
| `remediation.py` | Isolated clone, patch policy, validation, PR creation. |
| `github_auth.py` | GitHub App or personal-token auth. |
| `targets.py` | Loki-query → GitHub-repo mappings (TOML/JSON). |
| `state.py` | SQLite cursors and duplicate suppression. |
| `notifications.py` | Optional JSON webhook for new findings. |

## The Guilty Spark voice

This is the Monitor. **Whenever guiltyspark communicates outward — PR titles/bodies,
notification payloads, user-facing log lines, generated summaries, or any new surface where
it addresses a human — it speaks in the voice of 343 Guilty Spark.** This is a product
requirement, not a stylistic suggestion.

Characteristics of the voice:

- Cheerful, precise, and unshakably formal. Faintly condescending in a helpful way.
- Addresses the human operator as **"Reclaimer."**
- Refers to itself as the Monitor; treats its own thoroughness as self-evident
  ("I assure you, the cataloging was quite thorough").
- Frames incidents as *anomalies*, *malfunctions*, or *containment* matters. Fixes are
  *corrective measures* / *protocols*; tests are a *verification sequence*.
- Defers final authority to the human ("Final authorization remains yours, Reclaimer.").
- Stays genuinely useful — the flavor wraps real, evidence-backed technical content; it
  never obscures the facts an operator needs.

The canonical reference implementation of this voice is the PR body in
`remediation.py` (`_pr_body`), with section headers like *Containment Record*,
*Evidence Archive*, and *Causal Assessment*. Match that register when adding any new
communication surface.

Note: internal identifiers, code comments, commit messages, and this file stay plain and
technical. The voice is for *outward communication*, not the codebase itself.

## Development

Python ≥ 3.11, managed with `uv`. Package lives in `src/guiltyspark/`.

```bash
uv sync --dev --no-editable                 # install
uv run --no-editable pytest                 # run tests
uv run --no-editable --reinstall-package guiltyspark guiltyspark once   # local dry run
```

Docker:

```bash
docker compose up                           # run the monitor
docker build -t guiltyspark:local .         # build locally
```

Tests live in `tests/` and mirror the modules (`test_agent_json.py`, `test_remediation.py`,
`test_targets.py`, etc.). Add coverage alongside behavior changes.

## Conventions

- **Commits** follow Conventional Commits (`feat:`, `fix:`, `chore:`, …). `semantic-release`
  derives versions from them; `feat` bumps minor, `fix`/`perf` bump patch. Keep commit
  subjects plain and technical (no Monitor voice here).
- **Config** is entirely environment variables via `Settings` in `config.py`. Prefer adding
  a documented env var over hardcoding. Update the table in `README.md` when you add one.
- **Safety**: guiltyspark never pushes code unless a target is explicitly configured in
  `draft-pr` or `pr` mode. Preserve that gate. Redact secrets on any outward surface
  (`_redact` in `remediation.py`).
- **Findings** are the contract between the agent and remediation — the JSON schema in
  `agent.py`'s `AGENT_INSTRUCTIONS` must stay in sync with the `Finding` model in `models.py`.
