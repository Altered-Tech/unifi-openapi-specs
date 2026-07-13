#!/usr/bin/env python3
"""
UniFi API spec fetcher.

Downloads OpenAPI specs and Postman collections directly from developer.ui.com.
Version discovery works by following the redirect from /{service} and parsing
the versions list from the landing page RSC payload.

Output structure:
    {output_dir}/{service_id}/{version}/openapi.yaml
    {output_dir}/{service_id}/{version}/postman-collection.yaml

Usage:
    # Fetch all services, skip versions already on disk
    python api-scraper.py --check-new

    # Fetch everything (overwrite)
    python api-scraper.py

    # Specific services only
    python api-scraper.py --services site-manager,network

    # Show what versions are available without fetching
    python api-scraper.py --discover
"""

import argparse
import json
import os
import re
import subprocess
import sys

import requests
import yaml

BASE_URL = "https://developer.ui.com"
KNOWN_SERVICES = ["site-manager", "network", "protect", "mobility"]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; unifi-api-scraper/3.0)",
        "Accept": "text/html,application/xhtml+xml,application/json",
    })
    return session


def fetch_page(url: str, session: requests.Session) -> str:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# RSC payload parsing (used for version list discovery only)
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

    raw = text[start: pos + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Version discovery
# ---------------------------------------------------------------------------

def discover_versions(service_id: str, session: requests.Session) -> list:
    """
    Follow the redirect from /{service_id} to find the current version page,
    then parse the versions array from the RSC payload.
    Returns a list of version strings.
    """
    resp = session.get(f"{BASE_URL}/{service_id}", allow_redirects=False, timeout=15)
    if resp.status_code not in (301, 302, 307, 308):
        print(f"  [{service_id}] No redirect (status {resp.status_code}), skipping", file=sys.stderr)
        return []

    location = resp.headers.get("location", "").lstrip("/")
    parts = location.split("/")
    if len(parts) < 3:
        print(f"  [{service_id}] Unexpected redirect location: {location}", file=sys.stderr)
        return []

    current_version = parts[1]
    seed_url = f"{BASE_URL}/{location}"

    try:
        html = fetch_page(seed_url, session)
        payload = parse_rsc_payload(html)
        raw_versions = extract_json_value(payload, "versions")
        if raw_versions and isinstance(raw_versions, list):
            return [v["version"] for v in raw_versions if "version" in v]
    except Exception as e:
        print(f"  [{service_id}] Failed to fetch versions list: {e}", file=sys.stderr)

    return [current_version]


def discover_all_versions(service_ids: list, session: requests.Session) -> list:
    """Return list of {"serviceId", "version"} for all services."""
    results = []
    for service_id in service_ids:
        print(f"Discovering versions for {service_id}...", file=sys.stderr)
        for version in discover_versions(service_id, session):
            results.append({"serviceId": service_id, "version": version})
    return results


# ---------------------------------------------------------------------------
# Spec fetching
# ---------------------------------------------------------------------------

def fetch_spec(service_id: str, version: str, session: requests.Session) -> dict:
    """Download openapi.json for a service version directly."""
    url = f"{BASE_URL}/{service_id}/{version}/openapi.json"
    print(f"  Fetching {url}", file=sys.stderr)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_postman(service_id: str, version: str, session: requests.Session) -> "dict | None":
    """Download postman-collection.json for a service version. Returns None if not found."""
    url = f"{BASE_URL}/{service_id}/{version}/postman-collection.json"
    print(f"  Fetching {url}", file=sys.stderr)
    resp = session.get(url, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


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

    spec["components"]["schemas"] = {rename.get(k, k): v for k, v in schemas.items()}

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
# Discriminator fixup
# ---------------------------------------------------------------------------

def fix_discriminators(spec: dict) -> dict:
    """
    Add ``oneOf`` to schemas that are union selectors: they have a
    ``discriminator.mapping`` but their ``properties`` contains only the
    discriminator property plus at most the allowed shared properties
    (currently ``matchOpposite``).

    Schemas with other additional properties alongside the discriminator are
    concrete types — they use discriminator as a hint, not to define a union —
    and are left unchanged.

    Also skips schemas that are direct members of an ``allOf`` array, where
    the discriminator marks inheritance rather than a union.

    Without ``oneOf`` on union selectors, swift-openapi-generator emits only
    the bare discriminator property and discards all variant-specific fields
    from the decoded model.
    """
    # Properties that are shared across all variants of a union and should not
    # prevent oneOf from being added when they appear in the base schema.
    ALLOWED_SHARED_PROPS = {"matchOpposite"}

    def _walk(obj: object, inside_allof: bool = False) -> None:
        if isinstance(obj, dict):
            mapping = obj.get("discriminator", {}).get("mapping")
            if mapping and not inside_allof and "oneOf" not in obj and "anyOf" not in obj:
                props = obj.get("properties", {})
                discriminator_prop = obj["discriminator"]["propertyName"]
                non_discriminator_props = {k for k in props if k != discriminator_prop}
                if non_discriminator_props.issubset(ALLOWED_SHARED_PROPS):
                    unique_refs = list(dict.fromkeys(mapping.values()))
                    obj["oneOf"] = [{"$ref": ref} for ref in unique_refs]
                    obj.pop("properties", None)
                    obj.pop("required", None)
            for key, val in obj.items():
                if key == "allOf" and isinstance(val, list):
                    for item in val:
                        _walk(item, inside_allof=True)
                else:
                    _walk(val, inside_allof=False)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, inside_allof=False)

    _walk(spec)
    return spec


# ---------------------------------------------------------------------------
# Vacuum validation
# ---------------------------------------------------------------------------

def run_vacuum(path: str) -> bool:
    """Run 'vacuum lint' on the YAML file. Returns True if no errors."""
    result = subprocess.run(["which", "vacuum"], capture_output=True, text=True)
    vacuum_bin = result.stdout.strip()
    if not vacuum_bin:
        print("  [vacuum] not found on PATH, skipping validation", file=sys.stderr)
        return True

    result = subprocess.run(
        [vacuum_bin, "lint", "--no-style", path],
        capture_output=True,
        text=True,
    )
    lines = (result.stdout + result.stderr).splitlines()
    summary = [l for l in lines if any(kw in l for kw in ("total", "errors", "warnings", "Score", "✗", "▲", "●"))]
    for line in summary:
        print(f"  [vacuum] {line.strip()}", file=sys.stderr)
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def spec_output_path(output_dir: str, service_id: str, version: str) -> str:
    """Primary path used for --check-new existence checks."""
    return os.path.join(output_dir, service_id, version, "openapi.yaml")


def _write_yaml(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"  Saved: {path}", file=sys.stderr)


def save_spec(service_id: str, version: str, spec: dict, output_dir: str, validate: bool = False) -> None:
    spec = sanitize_spec(spec)
    spec = fix_discriminators(spec)
    path = spec_output_path(output_dir, service_id, version)
    _write_yaml(spec, path)
    if validate:
        run_vacuum(path)


def save_postman(service_id: str, version: str, data: dict, output_dir: str) -> None:
    path = os.path.join(output_dir, service_id, version, "postman-collection.yaml")
    _write_yaml(data, path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch UniFi API specs and save as OpenAPI YAML.",
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
        help="Comma-separated service IDs to fetch. Default: all known services.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Print available service/version pairs as JSON and exit.",
    )
    parser.add_argument(
        "--check-new",
        action="store_true",
        help="Only fetch service/version combos that don't have a spec on disk yet.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run 'vacuum lint' on each saved OpenAPI spec and print the summary.",
    )
    args = parser.parse_args()

    service_ids = (
        [s.strip() for s in args.services.split(",") if s.strip()]
        if args.services
        else KNOWN_SERVICES
    )

    session = make_session()
    combos = discover_all_versions(service_ids, session)

    if args.discover:
        print(json.dumps(combos, indent=2))
        return

    if args.check_new:
        missing = [
            c for c in combos
            if not os.path.exists(spec_output_path(args.output, c["serviceId"], c["version"]))
        ]
        if not missing:
            print("All specs are up to date.", file=sys.stderr)
            return
        print(f"Found {len(missing)} new version(s) to fetch.", file=sys.stderr)
        combos = missing

    for combo in combos:
        service_id = combo["serviceId"]
        version = combo["version"]
        print(f"\n[{service_id} {version}]", file=sys.stderr)

        try:
            spec = fetch_spec(service_id, version, session)
            save_spec(service_id, version, spec, args.output, validate=args.validate)
        except Exception as e:
            print(f"  OpenAPI fetch failed: {e}", file=sys.stderr)
            continue

        try:
            postman = fetch_postman(service_id, version, session)
            if postman:
                save_postman(service_id, version, postman, args.output)
        except Exception as e:
            print(f"  Postman fetch failed: {e}", file=sys.stderr)

    print("\nDone.", file=sys.stderr)


if __name__ == "__main__":
    main()
