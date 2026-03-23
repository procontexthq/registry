# ProContext Registry

This repository hosts the library registry for [ProContext](https://github.com/procontexthq/procontext) — an MCP server that gives AI coding agents accurate, up-to-date library documentation.

The live registry is published at [procontexthq.github.io](https://procontexthq.github.io/).

## What's in here

```
docs/
├── known-libraries.json          # The registry — one entry per supported library
├── registry-additional-info.json # Extra registry metadata used by probes and enrichment
└── registry_metadata.json        # Version pointer + checksums, fetched by ProContext on startup
registry-schema.md                # Canonical schema reference for registry JSON files
```

ProContext polls `registry_metadata.json` every 24 hours. When the `version` changes, it downloads `known-libraries.json` and `registry-additional-info.json`, verifies their SHA-256 checksums, and updates its in-memory index.

`registry-additional-info.json` stores supplemental data used outside the core library index. In particular, `useful_md_probe_base_urls` lists documentation URLs that should be probed by appending `.md` to determine whether they expose a valid Markdown document directly.

## Schema

See **[registry-schema.md](registry-schema.md)** for the full field reference — library-level fields, `PackageEntry` fields, `registry-additional-info.json`, and the `resolve_library` response format.

## Registry metadata format

```json
{
  "version": "YYYY-MM-DD",
  "download_url": "https://procontexthq.github.io/known-libraries.json",
  "checksum": "sha256:<hex>",
  "additional_info_download_url": "https://procontexthq.github.io/registry-additional-info.json",
  "additional_info_checksum": "sha256:<hex>"
}
```

## Validation & tooling

The `scripts/validate.py` script validates `docs/known-libraries.json` and `docs/registry-additional-info.json`, then keeps `registry_metadata.json` in sync. It requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/).

### Setup

```bash
uv sync
```

### Commands

| Command | What it does |
|---------|--------------|
| `uv run scripts/validate.py validate` | Fast schema check (rules 1–27) |
| `uv run scripts/validate.py validate --urls` | Schema check + URL reachability (rule 22) |
| `uv run scripts/validate.py validate --pypi` | Schema check + PyPI existence (rule 23) |
| `uv run scripts/validate.py checksum` | Compute SHA-256 for both registry JSON files and update `registry_metadata.json` |
| `uv run scripts/validate.py all` | Validate then update checksum (aborts on errors) |
| `uv run scripts/validate.py all --urls --pypi` | Run all checks |

### Validation rules

#### Per-entry rules (library level)

| # | Rule |
|---|------|
| 1 | `id` is present and a non-empty string |
| 2 | `id` matches `^[a-z0-9][a-z0-9_-]*$` |
| 3 | `name` is present and a non-empty string |
| 4 | `llms_txt_url` is present and a non-empty string |
| 5 | `llms_txt_url` is a valid URL starting with `https://` |
| 6 | `description`, if present, is a non-empty string |
| 7 | `aliases` is a list of strings |
| 8 | `packages`, if present, is an array (not an object) |
| 9 | No fields outside the known set — catches typos like `alias` instead of `aliases` |

Known library-level fields also include optional `llms_full_txt_url` values where a provider publishes a full-documentation entry point.

#### Per-entry rules (PackageEntry level)

| # | Rule |
|---|------|
| 10 | `ecosystem` is one of `"pypi"`, `"npm"`, `"conda"`, `"jsr"` |
| 11 | `package_names` is a list of strings |
| 12 | `languages`, if present, is a list of strings |
| 13 | `readme_url`, if present, is a valid URL |
| 14 | `repo_url`, if present, is a valid URL |
| 15 | No unknown fields in the PackageEntry object |

#### Cross-entry rules

| # | Rule |
|---|------|
| 16 | No two entries share the same `id` |
| 17 | No two entries share the same package name within the same ecosystem |
| 18 | No two entries share the same alias |

#### File-level rules

| # | Rule |
|---|------|
| 19 | `known-libraries.json` is valid JSON |
| 20 | `known-libraries.json` top-level structure is an array |
| 21 | `known-libraries.json` array is non-empty |
| 24 | `registry-additional-info.json` is valid JSON |
| 25 | `registry-additional-info.json` top-level structure is an object |
| 26 | `registry-additional-info.json.useful_md_probe_base_urls` is a non-empty array |
| 27 | Every `useful_md_probe_base_urls` entry is a valid URL |

#### Optional network checks (slow)

| # | Flag | Rule |
|---|------|------|
| 22 | `--urls` | `llms_txt_url` is reachable (HTTP 200) |
| 23 | `--pypi` | PyPI `package_names` exist on pypi.org |

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add or update entries, field reference, and grouping rules.

## License

This project is licensed under the [MIT License](LICENSE).
