# Operations Runbook

Use this file to teach the agent how your application fleet is wired and how you
prefer problems to be fixed. Keep it concrete and operational.

## Environment

- Loki is the source of log truth.
- Prefer fixes that reduce noisy retries, improve health checks, or make failure modes clearer.
- Treat each configured target as an independent repository and deployment boundary.

## Fix Preferences

- Do not recommend destructive data operations unless logs clearly show corruption and there is a backup path.
- Prefer configuration changes and small dependency upgrades before rewrites.
- When a service repeatedly restarts, check missing secrets, volume permissions, port conflicts, and failed upstream dependencies.
- When authentication errors appear, distinguish expected internet noise from internal service misconfiguration.

## PR Expectations

- PR-worthy findings should include the affected service, suspected file or component, evidence from logs, and validation results.
- Keep patches small, respect the target's allowed paths, and never merge or deploy automatically.
