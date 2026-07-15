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

## SEO

- Full HTML is served on first byte (no JS needed to index) — equivalent to SSR for crawlers.
- The title, meta description, canonical, Open Graph, and Twitter card target the page's core AI-native observability intent.
- JSON-LD in index.html describes the site with a `WebSite`, `SoftwareApplication`, and visible `FAQPage` in one `@graph`. Keep the software features and FAQ answers synchronized with the page copy.
- robots.txt and sitemap.xml reference https://guiltyspark.builtbybrendan.com. Update all three if the domain changes.
- After DNS is live, submit the sitemap in Google Search Console.
