# unifi-openapi-specs

Community-maintained OpenAPI specs for UniFi APIs (Site Manager, Network, Protect, Mobility), auto-updated via GitHub Actions.

[![Scrape UniFi API Docs](https://github.com/Altered-Tech/unifi-openapi-specs/actions/workflows/scrape.yml/badge.svg)](https://github.com/Altered-Tech/unifi-openapi-specs/actions/workflows/scrape.yml)

---

## Available Specs

Specs are organized as `{service}/{version}/openapi.yaml`. The latest version of each service is linked below.

| Service | Latest | All Versions |
|---|---|---|
| Site Manager | [v1.0.0](site-manager/v1.0.0/openapi.yaml) | v1.0.0 |
| Network | [v10.3.58](network/v10.3.58/openapi.yaml) | v9.1.120, v9.2.86, v9.2.87, v9.3.43, v9.3.45, v9.4.17, v9.4.19, v9.5.21, v10.0.162, v10.1.84, v10.3.58 |
| Protect | [v7.1.87](protect/v7.1.87/openapi.yaml) | v5.3.48, v6.0.47, v6.0.53, v6.1.65, v6.1.68, v6.1.75, v6.1.78, v6.1.79, v6.2.72, v6.2.83, v6.2.87, v6.2.88, v7.0.94, v7.0.104, v7.0.107, v7.1.69, v7.1.73, v7.1.74, v7.1.75, v7.1.76, v7.1.77, v7.1.83, v7.1.87 |
| Mobility | [v1.0.0](mobility/v1.0.0/openapi.yaml) | v1.0.0 |

---

## Usage

Reference a raw spec URL directly in your tooling:

```
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/{service}/{version}/openapi.yaml
```

**Examples:**

```bash
# Latest Network spec
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/network/v10.3.58/openapi.yaml

# Latest Protect spec
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/protect/v7.1.87/openapi.yaml
```

These URLs work directly with tools like [Swagger UI](https://swagger.io/tools/swagger-ui/), [Postman](https://www.postman.com/), [Insomnia](https://insomnia.rest/), and code generators.

---

## How It Works

Specs are scraped from [developer.ui.com](https://developer.ui.com) by parsing the embedded Next.js RSC payload in each page — no headless browser required. The scraper:

1. Follows redirects from `/{service}` to auto-discover all available versions
2. Reads the full OpenAPI spec embedded in the page HTML
3. Sanitizes component schema names (converts spaces to PascalCase to comply with the OpenAPI spec)
4. Writes `{service}/{version}/openapi.yaml`

A GitHub Action runs daily, detects new versions, and commits them automatically.

---

## Running the Scraper Locally

**Prerequisites:**
- Python 3.11+
- [vacuum](https://github.com/daveshanley/vacuum) (optional, for `--validate`)

```bash
pip install -r requirements.txt

# Scrape only versions not already on disk
python api-scraper.py --check-new

# Scrape everything
python api-scraper.py

# Specific services only
python api-scraper.py --services network,protect

# List all available versions without scraping
python api-scraper.py --discover

# Validate generated specs with vacuum
python api-scraper.py --check-new --validate

# Inspect a single page
python api-scraper.py --url https://developer.ui.com/network/v10.3.58/getnetworkdetails
```

---

## License

[MIT](LICENSE)

> This project is not affiliated with or endorsed by Ubiquiti Inc. All API specs are sourced from the public UniFi developer documentation at [developer.ui.com](https://developer.ui.com).
