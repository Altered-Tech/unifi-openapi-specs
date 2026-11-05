# unifi-openapi-specs

Community-maintained OpenAPI specs and Postman collections for UniFi APIs (Site Manager, Network, Protect, Mobility), auto-updated via GitHub Actions.

[![Fetch UniFi API Specs](https://github.com/Altered-Tech/unifi-openapi-specs/actions/workflows/scrape.yml/badge.svg)](https://github.com/Altered-Tech/unifi-openapi-specs/actions/workflows/scrape.yml)

**[Browse interactive API docs →](https://altered-tech.github.io/unifi-openapi-specs/)**

---

## Available Specs

Each version includes an OpenAPI spec (YAML) and a Postman collection (YAML).

<!-- versions:start -->
| Service | Latest | All Versions |
|---|---|---|
| Site Manager | [v1.0.0](site-manager/v1.0.0/openapi.yaml) | v1.0.0 |
| Network | [v11.0.97](network/v11.0.97/openapi.yaml) | v11.0.97, v10.6.109, v10.6.106, v10.6.102, v10.5.67, v10.4.57, v10.3.58, v10.1.84, v10.0.162, v9.5.21, v9.4.19, v9.4.17, v9.3.45, v9.3.43, v9.2.87, v9.2.86, v9.1.120 |
| Protect | [v7.3.74](protect/v7.3.74/openapi.yaml) | v7.3.74, v7.3.70, v7.2.105, v7.1.87, v7.1.83, v7.1.77, v7.1.76, v7.1.75, v7.1.74, v7.1.73, v7.1.69, v7.0.107, v7.0.104, v7.0.94, v6.2.88, v6.2.87, v6.2.83, v6.2.72, v6.1.79, v6.1.78, v6.1.75, v6.1.68, v6.1.65, v6.0.53, v6.0.47, v5.3.48 |
| Mobility | [v1.0.0](mobility/v1.0.0/openapi.yaml) | v1.0.0 |
<!-- versions:end -->

Each spec's `servers` block includes both access methods:

- **Local** — `https://{consoleIP}/proxy/{service}/integration` for direct console access on your network
- **Cloud** — `https://api.ui.com/v1/connector/consoles/{consoleId}/proxy/{service}/integration` for access via the UniFi cloud connector (requires console firmware ≥ 5.0.3)

---

## Usage

Reference a raw spec URL directly in your tooling:

```
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/{service}/{version}/openapi.yaml
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/{service}/{version}/postman-collection.yaml
```

**Examples:**

<!-- examples:start -->
```bash
# Latest Site Manager OpenAPI spec
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/site-manager/v1.0.0/openapi.yaml

# Latest Site Manager Postman collection
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/site-manager/v1.0.0/postman-collection.yaml

# Latest Network OpenAPI spec
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/network/v11.0.97/openapi.yaml

# Latest Network Postman collection
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/network/v11.0.97/postman-collection.yaml

# Latest Protect OpenAPI spec
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/protect/v7.3.74/openapi.yaml

# Latest Protect Postman collection
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/protect/v7.3.74/postman-collection.yaml

# Latest Mobility OpenAPI spec
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/mobility/v1.0.0/openapi.yaml

# Latest Mobility Postman collection
https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main/mobility/v1.0.0/postman-collection.yaml
```
<!-- examples:end -->

These URLs work directly with tools like [Swagger UI](https://swagger.io/tools/swagger-ui/), [Postman](https://www.postman.com/), [Insomnia](https://insomnia.rest/), and code generators.

---

## How It Works

Specs are fetched directly from [developer.ui.com](https://developer.ui.com) using the official OpenAPI and Postman download endpoints. The fetcher:

1. Queries `/api/services/{service}/versions` to discover all available versions
2. Downloads `/{service}/{version}/openapi.json` and `/{service}/{version}/postman-collection.json` for each version
3. Sanitizes OpenAPI component schema names (converts spaces to PascalCase to comply with the OpenAPI spec)
4. Adds `oneOf` to discriminator union schemas for compatibility with strict code generators (e.g. swift-openapi-generator)
5. Saves both files as YAML

A GitHub Action runs daily, detects new versions, and commits them automatically. The same run rewrites the version table and example URLs in this README with `update-readme.py`.

---

## Running Locally

**Prerequisites:**
- Python 3.11+
- [vacuum](https://github.com/daveshanley/vacuum) (optional, for `--validate`)

```bash
pip install -r requirements.txt

# Fetch only versions not already on disk
python api-scraper.py --check-new

# Fetch everything (overwrite)
python api-scraper.py

# Specific services only
python api-scraper.py --services network,protect

# List all available versions without fetching
python api-scraper.py --discover

# Validate generated OpenAPI specs with vacuum
python api-scraper.py --check-new --validate
```

---

## License

[MIT](LICENSE)

> This project is not affiliated with or endorsed by Ubiquiti Inc. All API specs are sourced from the public UniFi developer documentation at [developer.ui.com](https://developer.ui.com).
