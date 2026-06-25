# Homelab Runbook

Use this file to teach the agent how your homelab is wired and how you prefer problems to be fixed. Keep it concrete and operational.

## Environment

- `appl-pi` is the intended always-on host for this monitor.
- Loki is the source of log truth.
- Prefer fixes that reduce noisy retries, improve health checks, or make failure modes clearer.

## Fix Preferences

- Do not recommend destructive data operations unless logs clearly show corruption and there is a backup path.
- Prefer config changes and small dependency upgrades before rewrites.
- When a service is repeatedly restarting, check for missing secrets, bad volume permissions, port conflicts, and failed upstream dependencies.
- When auth errors appear, distinguish expected internet background noise from internal service misconfiguration.

## PR Expectations

- PR-worthy findings should include the affected service, suspected file or component, evidence from logs, and a small test or validation command when possible.
- Start in plan mode until repository credentials and branch naming rules are configured.
