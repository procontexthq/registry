#!/usr/bin/env python3
from __future__ import annotations

"""
ProContext Registry — validation and checksum management.

Schema reference: registry-schema.md

Usage:
    uv run scripts/validate.py                       # fast schema and cross-entry validation
    uv run scripts/validate.py --urls               # + URL reachability (rule 22)
    uv run scripts/validate.py --pypi               # + PyPI package existence (rule 23)
    uv run scripts/validate.py checksum              # validate, then update both checksums
    uv run scripts/validate.py checksum --urls --pypi  # validation + optional network checks
"""

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).parent.parent
LIBRARIES_FILE = REPO_ROOT / "docs" / "known-libraries.json"
ADDITIONAL_INFO_FILE = REPO_ROOT / "docs" / "registry-additional-info.json"
METADATA_FILE = REPO_ROOT / "docs" / "registry_metadata.json"
FAILED_URLS_FILE = REPO_ROOT / "data" / "failed_url_checks.json"

# --------------------------------------------------------------------------- #
# Constants — see registry-schema.md for field definitions
# --------------------------------------------------------------------------- #

KNOWN_FIELDS = frozenset({"id", "name", "description", "llms_txt_url", "llms_full_txt_url", "aliases", "packages"})
KNOWN_PACKAGE_ENTRY_FIELDS = frozenset({"ecosystem", "languages", "package_names", "readme_url", "repo_url"})
VALID_ECOSYSTEMS = frozenset({"pypi", "npm", "conda", "jsr"})
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# --------------------------------------------------------------------------- #
# Error type
# --------------------------------------------------------------------------- #


@dataclass(order=True)
class ValidationError:
    rule: int
    entry_id: str | None
    message: str

    def __str__(self) -> str:
        prefix = f"[{self.entry_id}]" if self.entry_id else "[file]"
        return f"  Rule {self.rule:>2}  {prefix}  {self.message}"


# --------------------------------------------------------------------------- #
# File-level validation (rules 19–21)
# --------------------------------------------------------------------------- #


def load_json_file(path: Path, rule: int) -> tuple[Any | None, list[ValidationError]]:
    """Parse JSON and return (data, errors)."""
    errors: list[ValidationError] = []
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return json.loads(raw), errors
    except json.JSONDecodeError as exc:
        errors.append(ValidationError(rule, None, f"Invalid JSON in {path.name}: {exc}"))
        return None, errors

 
def validate_libraries_file(path: Path) -> tuple[list[Any] | None, list[ValidationError]]:
    """Validate known-libraries.json shape. Returns (libraries, errors)."""
    libraries, errors = load_json_file(path, 19)
    if libraries is None:
        return None, errors

    # Rule 20: top-level is an array
    if not isinstance(libraries, list):
        errors.append(
            ValidationError(20, None, f"{path.name} top-level must be an array, got {type(libraries).__name__}")
        )
        return None, errors

    # Rule 21: array is non-empty
    if not libraries:
        errors.append(ValidationError(21, None, f"{path.name} array must not be empty"))

    return libraries, errors


def validate_additional_info_file(path: Path) -> list[ValidationError]:
    """Validate registry-additional-info.json structure."""
    data, errors = load_json_file(path, 24)
    if data is None:
        return errors

    if not isinstance(data, dict):
        errors.append(
            ValidationError(25, None, f"{path.name} top-level must be an object, got {type(data).__name__}")
        )
        return errors

    urls = data.get("useful_md_probe_base_urls")
    if not isinstance(urls, list) or not urls:
        errors.append(
            ValidationError(
                26,
                None,
                f"{path.name} must contain a non-empty useful_md_probe_base_urls array",
            )
        )
        return errors

    for i, value in enumerate(urls):
        if not _is_valid_url(value):
            errors.append(
                ValidationError(
                    27,
                    None,
                    f"{path.name} useful_md_probe_base_urls[{i}] is not a valid URL: {value!r}",
                )
            )

    return errors


# --------------------------------------------------------------------------- #
# Per-entry and cross-entry validation (rules 1–18)
# --------------------------------------------------------------------------- #


def _is_valid_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def validate_libraries(libraries: list[Any]) -> list[ValidationError]:
    errors: list[ValidationError] = []

    # Cross-entry tracking
    seen_ids: dict[str, int] = {}                       # id -> entry index
    seen_packages: dict[str, dict[str, str]] = {}       # ecosystem -> {package_name -> entry_id}

    for i, entry in enumerate(libraries):
        if not isinstance(entry, dict):
            errors.append(ValidationError(1, None, f"Entry at index {i} is not an object"))
            continue

        raw_id = entry.get("id")
        display_id = raw_id if isinstance(raw_id, str) and raw_id else f"<index {i}>"

        # ------------------------------------------------------------------- #
        # Rule 1: id present and non-empty  /  Rule 2: id matches pattern
        # ------------------------------------------------------------------- #
        if not isinstance(raw_id, str) or not raw_id.strip():
            errors.append(ValidationError(1, display_id, "id is missing or empty"))
        else:
            display_id = raw_id
            if not ID_PATTERN.match(raw_id):
                errors.append(
                    ValidationError(2, display_id, f"id {raw_id!r} does not match ^[a-z0-9][a-z0-9_-]*$")
                )

        # ------------------------------------------------------------------- #
        # Rule 3: name present and non-empty
        # ------------------------------------------------------------------- #
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(ValidationError(3, display_id, "name is missing or empty"))

        # ------------------------------------------------------------------- #
        # Rule 4: llms_txt_url present and non-empty
        # Rule 5: llms_txt_url is a valid https:// URL
        # ------------------------------------------------------------------- #
        llms_url = entry.get("llms_txt_url")
        if not isinstance(llms_url, str) or not llms_url.strip():
            errors.append(ValidationError(4, display_id, "llms_txt_url is missing or empty"))
        elif not llms_url.startswith("https://"):
            errors.append(
                ValidationError(5, display_id, f"llms_txt_url must start with https://, got {llms_url!r}")
            )

        # ------------------------------------------------------------------- #
        # Rule 6: description, if present, is a non-empty string
        # ------------------------------------------------------------------- #
        if "description" in entry and entry["description"] is not None:
            if not isinstance(entry["description"], str) or not entry["description"].strip():
                errors.append(ValidationError(6, display_id, "description must be a non-empty string"))

        # ------------------------------------------------------------------- #
        # Rule 7: aliases is a list of strings
        # ------------------------------------------------------------------- #
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            errors.append(ValidationError(7, display_id, "aliases must be a list of strings"))
            aliases = []

        # ------------------------------------------------------------------- #
        # Rule 8: packages is an array (not a dict)
        # ------------------------------------------------------------------- #
        packages = entry.get("packages", [])
        if not isinstance(packages, list):
            errors.append(
                ValidationError(
                    8,
                    display_id,
                    f"packages must be an array of PackageEntry objects, got {type(packages).__name__} "
                    f"— see registry-schema.md",
                )
            )
            packages = []

        # ------------------------------------------------------------------- #
        # Rule 9: no unknown library-level fields
        # ------------------------------------------------------------------- #
        unknown = set(entry.keys()) - KNOWN_FIELDS
        if unknown:
            errors.append(
                ValidationError(
                    9,
                    display_id,
                    f"Unknown field(s): {sorted(unknown)} — see registry-schema.md for valid fields",
                )
            )

        # ------------------------------------------------------------------- #
        # PackageEntry validation (rules 10–15)
        # ------------------------------------------------------------------- #
        pkg_names_by_ecosystem: dict[str, list[str]] = {}

        for j, pkg_entry in enumerate(packages):
            if not isinstance(pkg_entry, dict):
                errors.append(ValidationError(10, display_id, f"packages[{j}] is not an object"))
                continue

            pkg_label = f"packages[{j}]"

            # Rule 10: ecosystem is a valid enum value
            ecosystem = pkg_entry.get("ecosystem")
            if ecosystem not in VALID_ECOSYSTEMS:
                errors.append(
                    ValidationError(
                        10,
                        display_id,
                        f"{pkg_label}.ecosystem must be one of {sorted(VALID_ECOSYSTEMS)}, got {ecosystem!r}",
                    )
                )
                ecosystem = None

            # Rule 11: package_names is a list of strings
            pkg_names = pkg_entry.get("package_names", [])
            if not isinstance(pkg_names, list) or not all(isinstance(p, str) for p in pkg_names):
                errors.append(
                    ValidationError(11, display_id, f"{pkg_label}.package_names must be a list of strings")
                )
                pkg_names = []

            # Rule 12: languages, if present, is a list of strings
            langs = pkg_entry.get("languages")
            if langs is not None:
                if not isinstance(langs, list) or not all(isinstance(l, str) for l in langs):
                    errors.append(
                        ValidationError(12, display_id, f"{pkg_label}.languages must be a list of strings")
                    )

            # Rule 13: readme_url, if present, is a valid URL
            if "readme_url" in pkg_entry and pkg_entry["readme_url"] is not None:
                if not _is_valid_url(pkg_entry["readme_url"]):
                    errors.append(
                        ValidationError(
                            13,
                            display_id,
                            f"{pkg_label}.readme_url is not a valid URL: {pkg_entry['readme_url']!r}",
                        )
                    )

            # Rule 14: repo_url, if present, is a valid URL
            if "repo_url" in pkg_entry and pkg_entry["repo_url"] is not None:
                if not _is_valid_url(pkg_entry["repo_url"]):
                    errors.append(
                        ValidationError(
                            14,
                            display_id,
                            f"{pkg_label}.repo_url is not a valid URL: {pkg_entry['repo_url']!r}",
                        )
                    )

            # Rule 15: no unknown PackageEntry fields
            unknown_pkg = set(pkg_entry.keys()) - KNOWN_PACKAGE_ENTRY_FIELDS
            if unknown_pkg:
                errors.append(
                    ValidationError(
                        15,
                        display_id,
                        f"{pkg_label} has unknown field(s): {sorted(unknown_pkg)}",
                    )
                )

            # Collect for cross-entry duplicate checks
            if ecosystem and pkg_names:
                pkg_names_by_ecosystem.setdefault(ecosystem, []).extend(pkg_names)

        # ------------------------------------------------------------------- #
        # Cross-entry: Rule 16 — duplicate IDs
        # ------------------------------------------------------------------- #
        if isinstance(raw_id, str) and raw_id:
            if raw_id in seen_ids:
                errors.append(
                    ValidationError(16, display_id, f"Duplicate id {raw_id!r} (also at index {seen_ids[raw_id]})")
                )
            else:
                seen_ids[raw_id] = i

        # ------------------------------------------------------------------- #
        # Cross-entry: Rule 17 — duplicate package names within an ecosystem
        # ------------------------------------------------------------------- #
        for ecosystem, names in pkg_names_by_ecosystem.items():
            eco_seen = seen_packages.setdefault(ecosystem, {})
            for pkg_name in names:
                if pkg_name in eco_seen:
                    errors.append(
                        ValidationError(
                            17,
                            display_id,
                            f"{ecosystem} package {pkg_name!r} already listed under {eco_seen[pkg_name]!r}",
                        )
                    )
                else:
                    eco_seen[pkg_name] = display_id

    return errors


# --------------------------------------------------------------------------- #
# Optional network checks
# --------------------------------------------------------------------------- #


def check_urls(libraries: list[Any]) -> list[ValidationError]:
    """Rule 22: llms_txt_url is reachable (HTTP 200). Runs in parallel."""
    import httpx

    errors: list[ValidationError] = []

    def _check(entry: dict) -> ValidationError | None:
        url = entry.get("llms_txt_url", "")
        if not isinstance(url, str) or not url.startswith("https://"):
            return None
        eid = entry.get("id", "<unknown>")
        try:
            resp = httpx.head(url, follow_redirects=True, timeout=10)
            if resp.status_code not in (200, 405):
                resp = httpx.get(url, follow_redirects=True, timeout=15)
            if resp.status_code != 200:
                return ValidationError(22, eid, f"llms_txt_url returned HTTP {resp.status_code}: {url}")
        except Exception as exc:
            return ValidationError(22, eid, f"llms_txt_url unreachable ({exc}): {url}")
        return None

    entries = [e for e in libraries if isinstance(e, dict)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for result in pool.map(_check, entries):
            if result:
                errors.append(result)

    return errors


def check_pypi(libraries: list[Any]) -> list[ValidationError]:
    """Rule 23: PyPI packages in package entries exist on pypi.org. Runs in parallel."""
    import httpx

    errors: list[ValidationError] = []
    targets: list[tuple[str, str]] = []

    for entry in libraries:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("id", "<unknown>")
        packages = entry.get("packages", [])
        if isinstance(packages, list):
            for pkg_entry in packages:
                if isinstance(pkg_entry, dict) and pkg_entry.get("ecosystem") == "pypi":
                    for pkg in pkg_entry.get("package_names", []):
                        if isinstance(pkg, str):
                            targets.append((eid, pkg))

    def _check(args: tuple[str, str]) -> ValidationError | None:
        eid, pkg = args
        try:
            resp = httpx.get(f"https://pypi.org/pypi/{pkg}/json", timeout=10)
            if resp.status_code == 404:
                return ValidationError(23, eid, f"PyPI package {pkg!r} not found on pypi.org")
            if resp.status_code != 200:
                return ValidationError(23, eid, f"PyPI package {pkg!r} returned HTTP {resp.status_code}")
        except Exception as exc:
            return ValidationError(23, eid, f"PyPI check failed for {pkg!r}: {exc}")
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for result in pool.map(_check, targets):
            if result:
                errors.append(result)

    return errors


# --------------------------------------------------------------------------- #
# Failed-URL entry persistence
# --------------------------------------------------------------------------- #


def append_failed_url_entries(libraries: list[Any], url_errors: list[ValidationError]) -> None:
    """Append entries that failed Rule 22 to FAILED_URLS_FILE (deduplicates by id)."""
    failed_ids = {err.entry_id for err in url_errors if err.rule == 22 and err.entry_id}
    if not failed_ids:
        return

    existing: list[dict] = []
    if FAILED_URLS_FILE.exists():
        try:
            with open(FAILED_URLS_FILE) as f:
                data = json.load(f)
            if isinstance(data, list):
                existing = data
        except (json.JSONDecodeError, OSError):
            existing = []

    existing_ids = {e.get("id") for e in existing if isinstance(e, dict)}
    new_entries = [
        e for e in libraries
        if isinstance(e, dict) and e.get("id") in failed_ids and e.get("id") not in existing_ids
    ]

    if not new_entries:
        print(f"  (all failing entries already recorded in {FAILED_URLS_FILE.relative_to(REPO_ROOT)})")
        return

    FAILED_URLS_FILE.parent.mkdir(exist_ok=True)
    with open(FAILED_URLS_FILE, "w") as f:
        json.dump(existing + new_entries, f, indent=2)
        f.write("\n")
    print(
        f"  {len(new_entries)} new failing entr{'y' if len(new_entries) == 1 else 'ies'} "
        f"appended to {FAILED_URLS_FILE.relative_to(REPO_ROOT)}"
    )


# --------------------------------------------------------------------------- #
# Checksum helpers
# --------------------------------------------------------------------------- #


def compute_checksum(path: Path) -> str:
    with open(path, "rb") as f:
        data = f.read()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _bump_version(current: str) -> str:
    today = datetime.date.today().isoformat()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:-v(\d+))?$", current)
    if m:
        date_part, n = m.group(1), int(m.group(2) or 1)
        return f"{today}-v{n + 1}" if date_part == today else f"{today}-v1"
    return f"{today}-v1"


def update_metadata(libraries_checksum: str, additional_info_checksum: str, metadata_path: Path) -> None:
    with open(metadata_path) as f:
        meta = json.load(f)
    new_version = _bump_version(meta.get("version", ""))
    meta["checksum"] = libraries_checksum
    meta["additional_info_checksum"] = additional_info_checksum
    meta["version"] = new_version
    with open(metadata_path, "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    print(f"  version:  {new_version}")
    print(f"  checksum: {libraries_checksum}")
    print(f"  additional_info_checksum: {additional_info_checksum}")
    print(f"  written to: {metadata_path.relative_to(REPO_ROOT)}")


# --------------------------------------------------------------------------- #
# CLI commands
# --------------------------------------------------------------------------- #


def run_validation(args: argparse.Namespace) -> int:
    print(f"Validating {LIBRARIES_FILE.relative_to(REPO_ROOT)} ...")

    libraries, errors = validate_libraries_file(LIBRARIES_FILE)
    if libraries is not None:
        errors += validate_libraries(libraries)

    print(f"Validating {ADDITIONAL_INFO_FILE.relative_to(REPO_ROOT)} ...")
    errors += validate_additional_info_file(ADDITIONAL_INFO_FILE)

    if getattr(args, "urls", False) and libraries:
        print("Checking URL reachability (slow) ...")
        url_errors = check_urls(libraries)
        errors += url_errors
        if url_errors:
            append_failed_url_entries(libraries, url_errors)

    if getattr(args, "pypi", False) and libraries:
        print("Checking PyPI packages (slow) ...")
        errors += check_pypi(libraries)

    if errors:
        print(f"\n{len(errors)} error(s) found:\n")
        for err in sorted(errors):
            print(err)
        print()
        return 1

    print("All checks passed.")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    return run_validation(args)


def cmd_checksum(args: argparse.Namespace) -> int:
    rc = run_validation(args)
    if rc != 0:
        print("Checksum NOT updated — fix validation errors first.")
        return rc
    print()
    libraries_checksum = compute_checksum(LIBRARIES_FILE)
    additional_info_checksum = compute_checksum(ADDITIONAL_INFO_FILE)
    print(f"Computing checksum for {LIBRARIES_FILE.relative_to(REPO_ROOT)} ...")
    print(f"Computing checksum for {ADDITIONAL_INFO_FILE.relative_to(REPO_ROOT)} ...")
    update_metadata(libraries_checksum, additional_info_checksum, METADATA_FILE)
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="validate.py",
        description="ProContext Registry — validation and checksum management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  uv run scripts/validate.py                       fast schema and cross-entry validation
  uv run scripts/validate.py --urls               + URL reachability (rule 22)
  uv run scripts/validate.py --pypi               + PyPI package existence (rule 23)
  uv run scripts/validate.py checksum              validate, then update both checksums
  uv run scripts/validate.py checksum --urls       + URL reachability before checksum update
  uv run scripts/validate.py checksum --pypi       + PyPI package existence before checksum update
""",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("validate", "checksum"),
        default="validate",
        help="Optional command. Omit for validation; use 'checksum' to validate and update registry_metadata.json.",
    )
    parser.add_argument(
        "--urls", action="store_true", help="Also check URL reachability (rule 22, slow)"
    )
    parser.add_argument(
        "--pypi", action="store_true", help="Also verify PyPI packages exist (rule 23, slow)"
    )
    args = parser.parse_args()
    if args.command == "checksum":
        sys.exit(cmd_checksum(args))
    sys.exit(cmd_validate(args))


if __name__ == "__main__":
    main()
