# Deploying On appl-pi

This is the intended production shape for `guiltyspark`: a small Compose service running next to, or with network access to, Loki.

## 1. Copy The Project

Put the project on `appl-pi`, for example:

```bash
mkdir -p ~/guiltyspark
```

Then copy or clone this repository into that directory.

## 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
LOKI_URL=http://loki:3100
LOKI_QUERY='{job=~".+"}'
CODEX_HOME=/data/codex
GUILTYSPARK_NOTIFY_WEBHOOK_URL=
```

If Loki is in another Compose project, either put `guiltyspark` on the same Docker network or use the host/ LAN address for `LOKI_URL`.

## 3. Sign In To Codex

Use your ChatGPT Pro account. This stores Codex auth in `./data/codex`.

```bash
docker compose build
docker compose run --rm guiltyspark codex login --device-auth
```

## 4. Start It

```bash
docker compose up -d
docker compose logs -f guiltyspark
```

Findings are appended to:

```text
./data/findings.jsonl
```

## 5. Enable Codex Fix Planning Later

Mount a repository or homelab config checkout:

```yaml
volumes:
  - ./data:/data
  - /srv/homelab:/workspace
```

Then set:

```env
GUILTYSPARK_CODEX_WORKDIR=/workspace
GUILTYSPARK_PR_MODE=plan
```

Start in `plan` mode. Move toward branch/PR automation only after branch naming, GitHub credentials, and protected paths are explicit.
