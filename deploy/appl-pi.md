# Deploying GuiltySpark To apple-pi

GuiltySpark uses the same GitOps shape as the other apple-pi services: GitHub
Actions tests and releases the code, builds a native `linux/arm64` image, pushes
it to GHCR, and calls a Portainer stack webhook. The Pi only pulls the image.

## Release Flow

```text
conventional commit merged to main
  -> GitHub Actions runs tests and builds the package
  -> semantic-release creates a version and GitHub tag
  -> CI pushes ghcr.io/bheus/guiltyspark:<version> and :latest
  -> CI calls the Portainer webhook
  -> Portainer pulls :latest and recreates the service
```

Only `feat:` commits create minor releases; `fix:` and `perf:` create patch
releases. Other conventional commit types are tested but do not publish an image.

## One-Time Host Configuration

Create the configuration directory and target file on apple-pi:

```bash
ssh bheussler@apple-pi.lan 'mkdir -p /home/bheussler/guiltyspark'
scp targets.example.toml bheussler@apple-pi.lan:/home/bheussler/guiltyspark/targets.toml
```

Edit that file for the services GuiltySpark should watch. It contains repository
names, Loki queries, test commands, and patch policy, but no credentials.

## Portainer Stack

Create a Git-backed Portainer stack with:

- Repository: `https://github.com/bheus/guiltyspark`
- Reference: `refs/heads/main`
- Compose path: `docker-compose.yml`
- Automatic updates: webhook

Set these stack environment variables:

```text
GUILTYSPARK_TARGETS_FILE=/home/bheussler/guiltyspark/targets.toml
GITHUB_TOKEN=<token with repository contents and pull-request write access>
```

The Compose stack creates the persistent `guiltyspark-data` volume for SQLite,
findings, remediation state, and Codex authentication. The target file is mounted
read-only from the host.

For GHCR pulls, either make `ghcr.io/bheus/guiltyspark` public or configure a
Portainer registry credential with `read:packages` access.

Enable the stack webhook in Portainer and save its URL as the GitHub repository
secret `PORTAINER_WEBHOOK`. The release job calls it only after the new arm64
image has been pushed successfully.

## Codex Authentication

Authenticate once into the persistent named volume:

```bash
ssh -t bheussler@apple-pi.lan \
  'docker run --rm -it -v guiltyspark-data:/data ghcr.io/bheus/guiltyspark:latest codex login --device-auth'
```

The token remains under `/data/codex` across image updates and container
recreation. GitHub and Loki credentials are stripped from every Codex subprocess.

## Rollout

Start targets in `observe` mode. After confirming incident grouping and diagnosis,
promote individual targets to `fix`, then `draft-pr`. Each mutating mode requires
explicit validation commands and allowed paths.

Deploy by committing with a release-producing conventional commit and pushing
`main`:

```bash
git push origin main
gh run watch
```

## Verification

```bash
ssh bheussler@apple-pi.lan 'docker ps --filter name=guiltyspark'
ssh bheussler@apple-pi.lan 'docker logs --tail 100 guiltyspark'
ssh bheussler@apple-pi.lan \
  'docker exec guiltyspark guiltyspark doctor'
```

Findings and workflow state live in the `guiltyspark-data` volume. Roll back by
pinning the Compose image to an earlier published version tag and redeploying the
Portainer stack.
