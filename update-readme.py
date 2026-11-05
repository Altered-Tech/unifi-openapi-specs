#!/usr/bin/env python3
"""
Rewrite the version table and example URLs in README.md from the specs on disk.

Only the text between the marker comments is replaced:
    <!-- versions:start --> ... <!-- versions:end -->
    <!-- examples:start --> ... <!-- examples:end -->

Usage:
    python update-readme.py            # rewrite README.md in place
    python update-readme.py --check    # exit 1 if README.md is out of date
"""

import argparse
import os
import re
import sys

SERVICES = [
    ("site-manager", "Site Manager"),
    ("network", "Network"),
    ("protect", "Protect"),
    ("mobility", "Mobility"),
]
RAW_BASE = "https://raw.githubusercontent.com/Altered-Tech/unifi-openapi-specs/main"


def version_key(version: str) -> tuple:
    """'v10.6.109' → (10, 6, 109), so v10 sorts after v9."""
    return tuple(int(n) for n in re.findall(r"\d+", version))


def versions_on_disk(root: str, service_id: str) -> list:
    """Version folders that hold an openapi.yaml, oldest first."""
    service_dir = os.path.join(root, service_id)
    if not os.path.isdir(service_dir):
        return []
    versions = [
        v for v in os.listdir(service_dir)
        if os.path.isfile(os.path.join(service_dir, v, "openapi.yaml"))
    ]
    return sorted(versions, key=version_key)


def build_table(root: str) -> str:
    lines = [
        "| Service | Latest | All Versions |",
        "|---|---|---|",
    ]
    for service_id, name in SERVICES:
        versions = versions_on_disk(root, service_id)
        if not versions:
            continue
        latest = versions[-1]
        all_versions = ", ".join(reversed(versions))
        lines.append(f"| {name} | [{latest}]({service_id}/{latest}/openapi.yaml) | {all_versions} |")
    return "\n".join(lines)


def build_examples(root: str) -> str:
    lines = ["```bash"]
    for service_id, name in SERVICES:
        versions = versions_on_disk(root, service_id)
        if not versions:
            continue
        latest = versions[-1]
        lines.append(f"# Latest {name} OpenAPI spec")
        lines.append(f"{RAW_BASE}/{service_id}/{latest}/openapi.yaml")
        if os.path.isfile(os.path.join(root, service_id, latest, "postman-collection.yaml")):
            lines.append("")
            lines.append(f"# Latest {name} Postman collection")
            lines.append(f"{RAW_BASE}/{service_id}/{latest}/postman-collection.yaml")
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    lines.append("```")
    return "\n".join(lines)


def replace_block(text: str, name: str, body: str) -> str:
    pattern = re.compile(
        rf"(<!-- {name}:start -->\n).*?(\n<!-- {name}:end -->)",
        re.DOTALL,
    )
    if not pattern.search(text):
        sys.exit(f"Error: README.md has no <!-- {name}:start/end --> markers.")
    return pattern.sub(lambda m: m.group(1) + body + m.group(2), text)


def main():
    parser = argparse.ArgumentParser(description="Update README.md version info from specs on disk.")
    parser.add_argument("--root", default=".", help="Repo root that holds the service folders (default: .)")
    parser.add_argument("--check", action="store_true", help="Exit 1 if README.md is out of date; write nothing.")
    args = parser.parse_args()

    readme_path = os.path.join(args.root, "README.md")
    with open(readme_path, encoding="utf-8") as f:
        old = f.read()

    new = replace_block(old, "versions", build_table(args.root))
    new = replace_block(new, "examples", build_examples(args.root))

    if new == old:
        print("README.md is up to date.", file=sys.stderr)
        return
    if args.check:
        sys.exit("README.md is out of date. Run: python update-readme.py")

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(new)
    print("Updated README.md.", file=sys.stderr)


if __name__ == "__main__":
    main()
