# guiltyspark marketing site

Static, pre-rendered marketing page for https://guiltyspark.builtbybrendan.com.

No build step. Serve this directory with any static file server:

```bash
# quick local check
python3 -m http.server -d site 8080
```

## Docker Compose

The repository's Compose stack runs this directory in the `guiltyspark-site` nginx
service. It publishes port `8080` by default:

```bash
docker compose up -d guiltyspark-site
```

Open `http://localhost:8080`, or set `GUILTYSPARK_SITE_PORT` to choose another host
port. Files are mounted read-only into nginx, so site edits are visible without
rebuilding an image.

## Other deploy options

- **GitHub Pages**: publish the site/ directory, add a CNAME file containing guiltyspark.builtbybrendan.com.
- **Caddy**: `caddy file-server --root site --domain guiltyspark.builtbybrendan.com` (automatic HTTPS).

## SEO

- Full HTML is served on first byte (no JS needed to index) — equivalent to SSR for crawlers.
- <title>, meta description, canonical, Open Graph, Twitter card, and JSON-LD SoftwareApplication schema are in index.html.
- robots.txt and sitemap.xml reference https://guiltyspark.builtbybrendan.com. Update all three if the domain changes.
- After DNS is live, submit the sitemap in Google Search Console.
