# guiltyspark marketing site

Static, pre-rendered marketing page for https://guiltyspark.builtbybrendan.com.

No build step. Serve this directory with any static file server:

```bash
# quick local check
python3 -m http.server -d site 8080
```

## Docker Compose

The repository's main-branch workflow packages this directory as the
`ghcr.io/bheus/guiltyspark-site:latest` nginx image. The Compose stack runs that
image as the `guiltyspark-site` service and publishes port `8080` by default:

```bash
docker compose up -d guiltyspark-site
```

Open `http://localhost:8080`, or set `GUILTYSPARK_SITE_PORT` to choose another host
port. For a local image build, run:

```bash
docker build -t guiltyspark-site:local site
GUILTYSPARK_SITE_IMAGE=guiltyspark-site:local docker compose up -d guiltyspark-site
```

## Other deploy options

- **GitHub Pages**: publish the site/ directory, add a CNAME file containing guiltyspark.builtbybrendan.com.
- **Caddy**: `caddy file-server --root site --domain guiltyspark.builtbybrendan.com` (automatic HTTPS).

## SEO and agent parsing

- Full HTML is served on first byte (no JS needed to index) — equivalent to SSR for crawlers.
- The title, meta description, canonical, Open Graph, and Twitter card target the page's core AI-native observability intent.
- JSON-LD in index.html describes the site with a `WebSite`, `SoftwareApplication`, and visible `FAQPage` in one `@graph`.
- robots.txt, sitemap.xml, and llms.txt reference https://guiltyspark.builtbybrendan.com. Update all of them if the domain changes.
- After DNS is live, submit the sitemap in Google Search Console.

### Things that drift — check these when editing the page

- **FAQ**: every question in the `FAQPage` JSON-LD must exist on the page with matching
  answer text. Google discards FAQ markup whose Q&A is not visible, so adding or rewording
  a visible FAQ card means editing the `@graph` in the same commit.
- **Version**: `softwareVersion` in the JSON-LD and the `v0.NN` badge in the nav both restate
  `project.version` from the repository's root `pyproject.toml`. Bump them together.
- **Feature list**: `featureList` in the `SoftwareApplication` node should stay recognizable
  in the page copy.

### Structure the parsers rely on

- One `<h1>`, then `<h2>` per section and `<h3>` per card — the outline agents extract.
- `<main id="content">` wraps the page body; a skip link targets it.
- Quick-start commands live in `<pre><code>`, with the `$` prompt supplied by a CSS
  `::before` rule so copied and scraped text is the bare command.
- Enumerations are real lists: `<ol>` for the timeline, `<ul>` for autonomy modes and trust
  guarantees, `<dl>` for the CLI subcommand reference.
- `llms.txt` is the machine-readable project summary. Keep its autonomy modes, commands, and
  requirements in step with the page and the root README.
- `_orig_preview.html` is a retired draft kept for reference only; `.dockerignore` keeps it
  out of the served image so it cannot be crawled as duplicate content.
