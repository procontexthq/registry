#!/usr/bin/env python3
"""
ProContext Registry — validation and checksum management.

Usage:
    uv run scripts/validate.py validate              # fast schema check (rules 1–19)
    uv run scripts/validate.py validate --urls       # + URL reachability (rule 20)
    uv run scripts/validate.py validate --pypi       # + PyPI package existence (rule 21)
    uv run scripts/validate.py checksum              # compute & update checksum only
    uv run scripts/validate.py all                   # validate then update checksum
    uv run scripts/validate.py all --urls --pypi     # everything
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
METADATA_FILE = REPO_ROOT / "docs" / "registry_metadata.json"
FAILED_URLS_FILE = REPO_ROOT / "data" / "failed_url_checks.json"

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

KNOWN_FIELDS = frozenset(
    {"id", "name", "docs_url", "repo_url", "languages", "packages", "aliases", "llms_txt_url"}
)
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
# File-level validation (rules 17–19)
# --------------------------------------------------------------------------- #


def validate_file(path: Path) -> tuple[list[Any] | None, list[ValidationError]]:
    """Parse JSON and validate top-level structure. Returns (libraries, errors)."""
    errors: list[ValidationError] = []

    # Rule 17: valid JSON
    try:
        with open(path, "rb") as f:
            raw = f.read()
        libraries = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(ValidationError(17, None, f"Invalid JSON: {exc}"))
        return None, errors

    # Rule 18: top-level is an array
    if not isinstance(libraries, list):
        errors.append(
            ValidationError(18, None, f"Top-level must be an array, got {type(libraries).__name__}")
        )
        return None, errors

    # Rule 19: array is non-empty
    if not libraries:
        errors.append(ValidationError(19, None, "Array must not be empty"))

    return libraries, errors


# --------------------------------------------------------------------------- #
# Per-entry and cross-entry validation (rules 1–16)
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
    seen_ids: dict[str, int] = {}        # id -> entry index
    seen_pypi: dict[str, str] = {}       # package -> entry id
    seen_npm: dict[str, str] = {}        # package -> entry id
    seen_aliases: dict[str, str] = {}    # alias -> entry id

    for i, entry in enumerate(libraries):
        if not isinstance(entry, dict):
            errors.append(ValidationError(1, None, f"Entry at index {i} is not an object"))
            continue

        # Resolve a display id for error messages before we validate id itself
        raw_id = entry.get("id")
        display_id = raw_id if isinstance(raw_id, str) and raw_id else f"<index {i}>"

        # ------------------------------------------------------------------- #
        # Rule 1: id is present and non-empty string
        # ------------------------------------------------------------------- #
        if not isinstance(raw_id, str) or not raw_id.strip():
            errors.append(ValidationError(1, display_id, "id is missing or empty"))
        else:
            display_id = raw_id  # confirmed valid string
            # Rule 2: id matches pattern
            if not ID_PATTERN.match(raw_id):
                errors.append(
                    ValidationError(
                        2, display_id, f"id {raw_id!r} does not match ^[a-z0-9][a-z0-9_-]*$"
                    )
                )

        # ------------------------------------------------------------------- #
        # Rule 3: name is present and non-empty string
        # ------------------------------------------------------------------- #
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(ValidationError(3, display_id, "name is missing or empty"))

        # ------------------------------------------------------------------- #
        # Rule 4: llms_txt_url is present and non-empty string
        # Rule 5: llms_txt_url starts with https://
        # ------------------------------------------------------------------- #
        llms_url = entry.get("llms_txt_url")
        if not isinstance(llms_url, str) or not llms_url.strip():
            errors.append(ValidationError(4, display_id, "llms_txt_url is missing or empty"))
        elif not llms_url.startswith("https://"):
            errors.append(
                ValidationError(5, display_id, f"llms_txt_url must start with https://, got {llms_url!r}")
            )

        # ------------------------------------------------------------------- #
        # Rule 6: docs_url, if present, is a valid URL
        # ------------------------------------------------------------------- #
        if "docs_url" in entry and entry["docs_url"] is not None:
            if not _is_valid_url(entry["docs_url"]):
                errors.append(
                    ValidationError(6, display_id, f"docs_url is not a valid URL: {entry['docs_url']!r}")
                )

        # ------------------------------------------------------------------- #
        # Rule 7: repo_url, if present, is a valid URL
        # ------------------------------------------------------------------- #
        if "repo_url" in entry and entry["repo_url"] is not None:
            if not _is_valid_url(entry["repo_url"]):
                errors.append(
                    ValidationError(7, display_id, f"repo_url is not a valid URL: {entry['repo_url']!r}")
                )

        # ------------------------------------------------------------------- #
        # Rule 8: languages is a list of strings (not null, not a bare string)
        # ------------------------------------------------------------------- #
        languages = entry.get("languages")
        if not isinstance(languages, list) or not all(isinstance(l, str) for l in languages):
            errors.append(
                ValidationError(
                    8,
                    display_id,
                    f"languages must be a list of strings, got {type(languages).__name__}",
                )
            )

        # ------------------------------------------------------------------- #
        # Rule 9: packages.pypi is a list of strings
        # Rule 10: packages.npm is a list of strings
        # ------------------------------------------------------------------- #
        packages = entry.get("packages", {})
        if not isinstance(packages, dict):
            errors.append(ValidationError(9, display_id, "packages must be an object"))
            pypi_list: list = []
            npm_list: list = []
        else:
            pypi_list = packages.get("pypi", [])
            npm_list = packages.get("npm", [])
            if not isinstance(pypi_list, list) or not all(isinstance(p, str) for p in pypi_list):
                errors.append(
                    ValidationError(9, display_id, "packages.pypi must be a list of strings")
                )
                pypi_list = []
            if not isinstance(npm_list, list) or not all(isinstance(p, str) for p in npm_list):
                errors.append(
                    ValidationError(10, display_id, "packages.npm must be a list of strings")
                )
                npm_list = []

        # ------------------------------------------------------------------- #
        # Rule 11: aliases is a list of strings
        # ------------------------------------------------------------------- #
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            errors.append(
                ValidationError(11, display_id, "aliases must be a list of strings")
            )
            aliases = []

        # ------------------------------------------------------------------- #
        # Rule 12: no unknown fields
        # ------------------------------------------------------------------- #
        unknown = set(entry.keys()) - KNOWN_FIELDS
        if unknown:
            errors.append(
                ValidationError(12, display_id, f"Unknown field(s): {sorted(unknown)} — did you mean aliases/docs_url/repo_url?")
            )

        # ------------------------------------------------------------------- #
        # Cross-entry: Rule 13 — duplicate IDs
        # ------------------------------------------------------------------- #
        if isinstance(raw_id, str) and raw_id:
            if raw_id in seen_ids:
                errors.append(
                    ValidationError(
                        13,
                        display_id,
                        f"Duplicate id {raw_id!r} (also at index {seen_ids[raw_id]})",
                    )
                )
            else:
                seen_ids[raw_id] = i

        # ------------------------------------------------------------------- #
        # Cross-entry: Rule 14 — duplicate PyPI package names
        # ------------------------------------------------------------------- #
        for pkg in pypi_list:
            if pkg in seen_pypi:
                errors.append(
                    ValidationError(
                        14,
                        display_id,
                        f"PyPI package {pkg!r} is already listed under {seen_pypi[pkg]!r}",
                    )
                )
            else:
                seen_pypi[pkg] = display_id

        # ------------------------------------------------------------------- #
        # Cross-entry: Rule 15 — duplicate npm package names
        # ------------------------------------------------------------------- #
        for pkg in npm_list:
            if pkg in seen_npm:
                errors.append(
                    ValidationError(
                        15,
                        display_id,
                        f"npm package {pkg!r} is already listed under {seen_npm[pkg]!r}",
                    )
                )
            else:
                seen_npm[pkg] = display_id

        # ------------------------------------------------------------------- #
        # Cross-entry: Rule 16 — duplicate aliases
        # ------------------------------------------------------------------- #
        for alias in aliases:
            if alias in seen_aliases:
                errors.append(
                    ValidationError(
                        16,
                        display_id,
                        f"Alias {alias!r} is already used by {seen_aliases[alias]!r}",
                    )
                )
            else:
                seen_aliases[alias] = display_id

    return errors


# --------------------------------------------------------------------------- #
# Optional network checks
# --------------------------------------------------------------------------- #


def check_urls(libraries: list[Any]) -> list[ValidationError]:
    """Rule 20: llms_txt_url is reachable (HTTP 200). Runs in parallel."""
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
                # Some servers reject HEAD; fall back to GET
                resp = httpx.get(url, follow_redirects=True, timeout=15)
            if resp.status_code != 200:
                return ValidationError(
                    20, eid, f"llms_txt_url returned HTTP {resp.status_code}: {url}"
                )
        except Exception as exc:
            return ValidationError(20, eid, f"llms_txt_url unreachable ({exc}): {url}")
        return None

    entries = [e for e in libraries if isinstance(e, dict)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for result in pool.map(_check, entries):
            if result:
                errors.append(result)

    return errors


def check_pypi(libraries: list[Any]) -> list[ValidationError]:
    """Rule 21: PyPI packages exist on pypi.org. Runs in parallel."""
    import httpx

    errors: list[ValidationError] = []

    targets: list[tuple[str, str]] = []
    for entry in libraries:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("id", "<unknown>")
        packages = entry.get("packages", {})
        if isinstance(packages, dict):
            for pkg in packages.get("pypi", []):
                if isinstance(pkg, str):
                    targets.append((eid, pkg))

    def _check(args: tuple[str, str]) -> ValidationError | None:
        eid, pkg = args
        try:
            resp = httpx.get(f"https://pypi.org/pypi/{pkg}/json", timeout=10)
            if resp.status_code == 404:
                return ValidationError(21, eid, f"PyPI package {pkg!r} not found on pypi.org")
            if resp.status_code != 200:
                return ValidationError(
                    21, eid, f"PyPI package {pkg!r} returned HTTP {resp.status_code}"
                )
        except Exception as exc:
            return ValidationError(21, eid, f"PyPI check failed for {pkg!r}: {exc}")
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
    """Append entries that failed Rule 20 to FAILED_URLS_FILE (deduplicates by id)."""
    failed_ids = {err.entry_id for err in url_errors if err.rule == 20 and err.entry_id}
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


def update_metadata(checksum: str, metadata_path: Path) -> None:
    with open(metadata_path) as f:
        meta = json.load(f)
    new_version = _bump_version(meta.get("version", ""))
    meta["checksum"] = checksum
    meta["version"] = new_version
    with open(metadata_path, "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    print(f"  version:  {new_version}")
    print(f"  checksum: {checksum}")
    print(f"  written to: {metadata_path.relative_to(REPO_ROOT)}")


# --------------------------------------------------------------------------- #
# CLI commands
# --------------------------------------------------------------------------- #


def cmd_validate(args: argparse.Namespace) -> int:
    print(f"Validating {LIBRARIES_FILE.relative_to(REPO_ROOT)} ...")

    libraries, errors = validate_file(LIBRARIES_FILE)
    if libraries is not None:
        errors += validate_libraries(libraries)

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


def cmd_checksum(_args: argparse.Namespace) -> int:
    checksum = compute_checksum(LIBRARIES_FILE)
    print(f"Computing checksum for {LIBRARIES_FILE.relative_to(REPO_ROOT)} ...")
    update_metadata(checksum, METADATA_FILE)
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    rc = cmd_validate(args)
    if rc != 0:
        print("Checksum NOT updated — fix validation errors first.")
        return rc
    print()
    return cmd_checksum(args)


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
  uv run scripts/validate.py validate              fast schema check (rules 1-19)
  uv run scripts/validate.py validate --urls       + URL reachability (rule 20)
  uv run scripts/validate.py validate --pypi       + PyPI package existence (rule 21)
  uv run scripts/validate.py checksum              compute & update checksum only
  uv run scripts/validate.py all                   validate then update checksum
  uv run scripts/validate.py all --urls --pypi     run everything
""",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate
    p_validate = subparsers.add_parser(
        "validate", help="Validate registry structure (rules 1–19 always; 20–21 optional)"
    )
    p_validate.add_argument(
        "--urls", action="store_true", help="Also check URL reachability (slow, network)"
    )
    p_validate.add_argument(
        "--pypi", action="store_true", help="Also verify PyPI packages exist (slow, network)"
    )
    p_validate.set_defaults(func=cmd_validate)

    # checksum
    p_checksum = subparsers.add_parser(
        "checksum", help="Compute SHA-256 and update registry_metadata.json (no validation)"
    )
    p_checksum.set_defaults(func=cmd_checksum)

    # all
    p_all = subparsers.add_parser(
        "all", help="Validate structure then update checksum (aborts on errors)"
    )
    p_all.add_argument(
        "--urls", action="store_true", help="Also check URL reachability (slow, network)"
    )
    p_all.add_argument(
        "--pypi", action="store_true", help="Also verify PyPI packages exist (slow, network)"
    )
    p_all.set_defaults(func=cmd_all)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
