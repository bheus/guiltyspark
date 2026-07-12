# Abraham Target Profile

This profile connects the generic GuiltySpark deployment to Abraham's production
and preproduction Loki streams and GitHub repository.

## Portainer Variables

Set `GUILTYSPARK_TARGETS_JSON` to the complete one-line contents of
[`profiles/abraham.targets.json`](profiles/abraham.targets.json).

The profile resolves Loki through `host.docker.internal:3100`. The Compose stack
adds Docker's `host-gateway` mapping, so GuiltySpark does not need to join or
modify the separately managed `loki-stack_loki` network.

Also configure:

```text
GITHUB_APP_ID=<GuiltySpark App ID>
GITHUB_APP_INSTALLATION_ID=<installation ID for bheus>
GITHUB_APP_PRIVATE_KEY=<literal or \n-escaped PEM private key>
GUILTYSPARK_INTERVAL_SECONDS=300
GUILTYSPARK_LOOKBACK_SECONDS=900
GUILTYSPARK_MIN_EVENTS=2
GUILTYSPARK_MAX_INCIDENTS_PER_RUN=8
```

Install the App on `bheus/Abraham` and grant repository Contents read/write and Pull
requests read/write. App credentials and the short-lived installation tokens they
mint are used only by GuiltySpark's Git and GitHub controller and are removed from
the Codex subprocess environment. Keep `GITHUB_TOKEN` only during migration, then
remove it after App authentication is verified.

## Watched Containers

The LogQL selector covers:

- `abraham-trading`
- `abraham-dashboard`
- `abraham-trading-preprod`
- `abraham-dashboard-preprod`

All four map to `bheus/Abraham`. Production and preproduction incidents remain
distinct because the container label participates in each incident fingerprint.

## Validation And Patch Policy

The validation command mirrors Abraham CI:

```bash
ALPACA_API_KEY=ci-test-key ALPACA_API_SECRET=ci-test-secret \
  uv run pytest tests/ -v -n 4
```

Patches may touch application source, tests, the two Compose files, the Dockerfile,
and Python dependency metadata. Credential files remain blocked globally.

The committed profile starts in `observe` mode. After diagnosis and notification
behavior look correct in Portainer, change only this field in the JSON value:

```json
"mode":"draft-pr"
```

`fix` is available as an intermediate stage when a validated patch is desired
without pushing a branch or opening a PR.
