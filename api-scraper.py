#!/usr/bin/env python3
"""
UniFi API documentation scraper.

Extracts OpenAPI specs from developer.ui.com by parsing the Next.js RSC payload
embedded in each page's HTML. Each page embeds the full OpenAPI spec, sidebar
navigation, and per-endpoint details.

Output structure:
    {output_dir}/{service_id}/{version}/openapi.yaml         (site-manager, mobility)
    {output_dir}/{service_id}/{version}/openapi-local.yaml   (network, protect — local access)
    {output_dir}/{service_id}/{version}/openapi-cloud.yaml   (network, protect — cloud connector)

Usage:
    # Scrape all services, skip versions already on disk
    python api-scraper.py --check-new

    # Scrape everything (overwrite)
    python api-scraper.py

    # Specific services only
    python api-scraper.py --services site-manager,network

    # Show what versions are available without scraping
    python api-scraper.py --discover

    # Scrape one URL, print JSON to stdout
    python api-scraper.py --url https://developer.ui.com/network/v10.3.58/getnetworkdetails
"""

import argparse
import copy
import json
import os
import re
import subprocess
import time
from urllib.parse import urljoin

import requests
import yaml

BASE_URL = "https://developer.ui.com"
KNOWN_SERVICES = ["site-manager", "network", "protect", "mobility"]
REQUEST_DELAY = 0.5  # seconds between requests

# Services that run on the console and are accessible both locally and via
# the cloud connector. Value is the proxy path segment used in both URLs.
SERVICE_PROXY_PATHS = {
    "network": "proxy/network/integration",
    "protect": "proxy/protect/integration",
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; unifi-api-scraper/2.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def fetch_page(url: str, session: requests.Session) -> str:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# RSC payload parsing
# ---------------------------------------------------------------------------

def parse_rsc_payload(html: str) -> str:
    """Concatenate all Next.js __next_f push payloads into one string."""
    pushes = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)
    combined = ""
    for chunk in pushes:
        try:
            combined += bytes(chunk, "utf-8").decode("unicode_escape")
        except Exception:
            combined += chunk
    return combined


def extract_json_value(text: str, key: str) -> "dict | list | None":
    """
    Find the first occurrence of `"key":` in text and extract the JSON value.
    Handles nested objects/arrays by tracking depth.
    """
    pattern = f'"{key}":'
    idx = text.find(pattern)
    if idx == -1:
        return None

    start = idx + len(pattern)
    while start < len(text) and text[start] == " ":
        start += 1
    if start >= len(text):
        return None

    opener = text[start]
    if opener not in ("{", "[", '"'):
        end = start
        while end < len(text) and text[end] not in (",", "}"):
            end += 1
        try:
            return json.loads(text[start:end])
        except Exception:
            return None

    depth = 0
    in_string = False
    escape_next = False
    pos = start

    while pos < len(text):
        ch = text[pos]
        if escape_next:
            escape_next = False
        elif ch == "\\":
            escape_next = True
        elif ch == '"':
            if not in_string:
                in_string = True
            else:
                in_string = False
        elif not in_string:
            if ch in ("{", "["):
                depth += 1
            elif ch in ("}", "]"):
                depth -= 1
                if depth == 0:
                    break
        pos += 1

    raw = text[start : pos + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def extract_best_full_spec(text: str) -> "dict | None":
    """
    Find the fullSpec with the most API paths in the payload.
    Multiple fullSpec values may exist (e.g. connector vs. main API spec).
    """
    best = None
    best_path_count = 0
    search_from = 0

    while True:
        idx = text.find('"fullSpec":', search_from)
        if idx == -1:
            break

        start = idx + len('"fullSpec":')
        while start < len(text) and text[start] == " ":
            start += 1

        if start >= len(text) or text[start] != "{":
            search_from = idx + 1
            continue

        # Walk the object to find its end
        depth = 0
        in_string = False
        escape_next = False
        pos = start

        while pos < len(text):
            ch = text[pos]
            if escape_next:
                escape_next = False
            elif ch == "\\":
                escape_next = True
            elif ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
            pos += 1

        raw = text[start : pos + 1]
        try:
            spec = json.loads(raw)
            path_count = len(spec.get("paths", {}))
            if path_count > best_path_count:
                best = spec
                best_path_count = path_count
        except json.JSONDecodeError:
            pass

        search_from = pos + 1

    return best


# ---------------------------------------------------------------------------
# Page scraping
# ---------------------------------------------------------------------------

def scrape_page(url: str, session: requests.Session) -> dict:
    """Scrape one docs page and return structured data."""
    html = fetch_page(url, session)
    payload = parse_rsc_payload(html)

    result: dict = {"url": url}

    sidebar = extract_json_value(payload, "sidebarData")
    if sidebar:
        result["sidebarData"] = sidebar

    versions = extract_json_value(payload, "versions")
    if versions:
        result["versions"] = versions

    endpoint = extract_json_value(payload, "endpoint")
    if endpoint:
        result["endpoint"] = endpoint

    full_spec = extract_best_full_spec(payload)
    if full_spec:
        result["fullSpec"] = full_spec

    return result


def collect_sidebar_urls(sidebar: list, base: str = BASE_URL) -> list:
    """Flatten nested sidebarData into [{label, url, method}]."""
    pages = []
    for item in sidebar:
        if item.get("type") == "doc":
            pages.append(
                {
                    "label": item.get("label", ""),
                    "url": urljoin(base, item["path"]),
                    "method": item.get("method"),
                }
            )
        elif item.get("type") == "category":
            pages.extend(collect_sidebar_urls(item.get("items", []), base))
    return pages


# ---------------------------------------------------------------------------
# Version discovery
# ---------------------------------------------------------------------------

def discover_versions(service_id: str, session: requests.Session) -> list:
    """
    Follow the redirect from /{service_id} to find the current version,
    then parse the versions array from that page.
    Returns list of {"version": str, "seed": str} dicts.
    """
    resp = session.get(f"{BASE_URL}/{service_id}", allow_redirects=False, timeout=15)
    if resp.status_code not in (301, 302, 307, 308):
        print(f"  [{service_id}] No redirect (status {resp.status_code}), skipping")
        return []

    location = resp.headers.get("location", "").lstrip("/")
    parts = location.split("/")
    # parts: [service_id, version, seed_slug]
    if len(parts) < 3:
        print(f"  [{service_id}] Unexpected redirect location: {location}")
        return []

    current_version = parts[1]
    seed_slug = parts[2]
    seed_url = f"{BASE_URL}/{location}"

    try:
        page_data = scrape_page(seed_url, session)
    except Exception as e:
        print(f"  [{service_id}] Failed to fetch seed page: {e}")
        return [{"version": current_version, "seed": seed_slug}]

    raw_versions = page_data.get("versions", [])
    if raw_versions and isinstance(raw_versions, list):
        return [{"version": v["version"], "seed": seed_slug} for v in raw_versions if "version" in v]

    return [{"version": current_version, "seed": seed_slug}]


def discover_all_versions(service_ids: list, session: requests.Session) -> list:
    """Return list of {"serviceId", "version", "seed"} for all services."""
    results = []
    for service_id in service_ids:
        print(f"Discovering versions for {service_id}...")
        versions = discover_versions(service_id, session)
        for v in versions:
            results.append({"serviceId": service_id, **v})
        time.sleep(REQUEST_DELAY)
    return results


# ---------------------------------------------------------------------------
# Scraping a full service/version
# ---------------------------------------------------------------------------

def scrape_service(service_id: str, version: str, seed_slug: str, session: requests.Session) -> dict:
    """
    Scrape all pages for one service version.
    Discovers the full sidebar from the seed page, then visits each page.
    """
    seed_url = f"{BASE_URL}/{service_id}/{version}/{seed_slug}"
    print(f"\n[{service_id} {version}] Seed: {seed_url}")

    seed_data = scrape_page(seed_url, session)
    sidebar = seed_data.get("sidebarData", [])
    pages_to_visit = collect_sidebar_urls(sidebar)
    print(f"  {len(pages_to_visit)} pages in sidebar")

    full_spec = seed_data.get("fullSpec")
    visited = {seed_url}

    for page_info in pages_to_visit:
        url = page_info["url"]
        if url in visited:
            continue
        visited.add(url)

        label = page_info["label"]
        method = page_info.get("method") or "DOC"
        print(f"  [{method}] {label}")
        time.sleep(REQUEST_DELAY)

        try:
            page_data = scrape_page(url, session)
        except requests.HTTPError as e:
            print(f"    HTTP {e.response.status_code}, skipping")
            continue
        except Exception as e:
            print(f"    Error: {e}, skipping")
            continue

        new_spec = page_data.get("fullSpec")
        if new_spec:
            current_count = len(full_spec.get("paths", {})) if full_spec else 0
            if len(new_spec.get("paths", {})) > current_count:
                full_spec = new_spec

    return {
        "serviceId": service_id,
        "version": version,
        "fullSpec": full_spec,
        "sidebar": sidebar,
    }


# ---------------------------------------------------------------------------
# Schema name sanitization
# ---------------------------------------------------------------------------

# OpenAPI component names must match ^[a-zA-Z0-9\.\-_]+$
_INVALID_COMPONENT_CHAR = re.compile(r"[^a-zA-Z0-9.\-_]")


def _to_pascal(name: str) -> str:
    """'Error Message' → 'ErrorMessage', 'mDNS proxy' → 'MDNSProxy'."""
    words = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(w[0].upper() + w[1:] for w in words if w)


def sanitize_spec(spec: dict) -> dict:
    """
    Rename any component schema keys that contain spaces or other characters
    not allowed by the OpenAPI spec, then update every $ref and discriminator
    mapping value that pointed at the old name.
    """
    schemas = spec.get("components", {}).get("schemas", {})

    rename: dict[str, str] = {}
    for key in list(schemas):
        if _INVALID_COMPONENT_CHAR.search(key):
            new_key = _to_pascal(key)
            if new_key != key:
                rename[key] = new_key

    if not rename:
        return spec

    # Rebuild schemas dict with new keys (preserve insertion order)
    spec["components"]["schemas"] = {
        rename.get(k, k): v for k, v in schemas.items()
    }

    # Sort longest old-name first so that "Foo Bar Baz" is replaced before
    # "Foo Bar" — preventing partial substitutions within longer names.
    ordered = sorted(rename.items(), key=lambda kv: len(kv[0]), reverse=True)
    prefix = "#/components/schemas/"

    def _rewrite(value: str) -> str:
        for old, new in ordered:
            value = value.replace(f"{prefix}{old}", f"{prefix}{new}")
        return value

    def _fix(obj: object) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and prefix in v:
                    obj[k] = _rewrite(v)
                else:
                    _fix(v)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str) and prefix in item:
                    obj[i] = _rewrite(item)
                else:
                    _fix(item)

    _fix(spec)
    return spec


# ---------------------------------------------------------------------------
# Vacuum validation
# ---------------------------------------------------------------------------

def run_vacuum(path: str) -> bool:
    """Run 'vacuum lint' on the YAML file. Returns True if no errors."""
    vacuum_bin = _find_vacuum()
    if not vacuum_bin:
        print("  [vacuum] not found on PATH, skipping validation")
        return True

    result = subprocess.run(
        [vacuum_bin, "lint", "--no-style", path],
        capture_output=True,
        text=True,
    )
    # vacuum exits 0 on success, non-zero on lint errors
    lines = (result.stdout + result.stderr).splitlines()
    # Print only the summary lines (category table + totals)
    summary = [l for l in lines if any(kw in l for kw in ("total", "errors", "warnings", "Score", "✗", "▲", "●"))]
    for line in summary:
        print(f"  [vacuum] {line.strip()}")
    return result.returncode == 0


def _find_vacuum() -> str:
    """Return full path to the vacuum binary, or empty string if not found."""
    result = subprocess.run(["which", "vacuum"], capture_output=True, text=True)
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def spec_output_path(output_dir: str, service_id: str, version: str) -> str:
    """Primary path used for --check-new existence checks."""
    if service_id in SERVICE_PROXY_PATHS:
        return os.path.join(output_dir, service_id, version, "openapi-local.yaml")
    return os.path.join(output_dir, service_id, version, "openapi.yaml")


def _build_local_servers(proxy_path: str) -> list:
    return [
        {
            "url": f"https://{{host}}/{proxy_path}",
            "description": "Local (direct access)",
            "variables": {
                "host": {
                    "default": "192.168.1.1",
                    "description": "IP address or hostname of the UniFi console",
                }
            },
        }
    ]


def _build_cloud_servers(proxy_path: str) -> list:
    return [
        {
            "url": f"https://api.ui.com/v1/connector/consoles/{{consoleId}}/{proxy_path}",
            "description": "Cloud connector",
            "variables": {
                "consoleId": {
                    "default": "",
                    "description": "Console Host ID",
                }
            },
        }
    ]


def _write_spec(spec: dict, path: str, validate: bool) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(spec, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"  Saved: {path}")
    if validate:
        run_vacuum(path)


def save_spec(data: dict, output_dir: str, validate: bool = False):
    service_id = data["serviceId"]
    version = data["version"]
    full_spec = data.get("fullSpec")

    if not full_spec:
        print(f"  No OpenAPI spec found for {service_id} {version}, skipping")
        return

    full_spec = sanitize_spec(full_spec)
    base_dir = os.path.join(output_dir, service_id, version)

    proxy_path = SERVICE_PROXY_PATHS.get(service_id)
    if proxy_path:
        local_spec = copy.deepcopy(full_spec)
        local_spec["servers"] = _build_local_servers(proxy_path)
        _write_spec(local_spec, os.path.join(base_dir, "openapi-local.yaml"), validate)

        cloud_spec = copy.deepcopy(full_spec)
        cloud_spec["servers"] = _build_cloud_servers(proxy_path)
        _write_spec(cloud_spec, os.path.join(base_dir, "openapi-cloud.yaml"), validate)
    else:
        _write_spec(full_spec, os.path.join(base_dir, "openapi.yaml"), validate)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape UniFi API docs and save OpenAPI YAML specs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output",
        default=".",
        help="Root output directory. Specs are written to {output}/{service}/{version}/openapi.yaml (default: .)",
    )
    parser.add_argument(
        "--services",
        default="",
        help="Comma-separated service IDs to scrape. Default: all known services.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Print available service/version pairs as JSON and exit.",
    )
    parser.add_argument(
        "--check-new",
        action="store_true",
        help="Only scrape service/version combos that don't have openapi.yaml on disk yet.",
    )
    parser.add_argument(
        "--url",
        default="",
        help="Scrape a single page URL and print structured JSON to stdout.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run 'vacuum lint' on each saved spec and print the summary.",
    )
    args = parser.parse_args()

    session = make_session()

    # Single-URL mode
    if args.url:
        data = scrape_page(args.url, session)
        print(json.dumps(data, indent=2, default=str))
        return

    service_ids = (
        [s.strip() for s in args.services.split(",") if s.strip()]
        if args.services
        else KNOWN_SERVICES
    )

    # Discover mode
    combos = discover_all_versions(service_ids, session)

    if args.discover:
        print(json.dumps(combos, indent=2))
        return

    # Filter to missing specs when --check-new
    if args.check_new:
        missing = [
            c for c in combos
            if not os.path.exists(spec_output_path(args.output, c["serviceId"], c["version"]))
        ]
        if not missing:
            print("All specs are up to date.")
            return
        print(f"Found {len(missing)} new service/version combos to scrape.")
        combos = missing

    for combo in combos:
        data = scrape_service(combo["serviceId"], combo["version"], combo["seed"], session)
        save_spec(data, args.output, validate=args.validate)

    print("\nDone.")


if __name__ == "__main__":
    main()
