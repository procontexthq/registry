# Guidelines

This document contains critical information about working with this repository. Follow these guidelines precisely.

## About the Project

This is the official registry for [ProContext](https://github.com/procontexthq/procontext) — an open-source MCP server that provides AI coding agents with accurate, up-to-date library documentation. This repo hosts the registry data that ProContext servers download and use to resolve library names and fetch documentation.

## ⚠️ CRITICAL: Git Operations Policy

**NEVER commit and push changes without explicit user approval.**

You must:

1. Wait for the user to explicitly ask you to commit and push any changes.
2. If you believe a commit is necessary, you can say "I think we should commit these changes. Should I commit and push them?" and wait for the user's response.
3. NEVER ever mention a `co-authored-by` or similar aspects. In particular, never mention the tool used to create the commit message or PR.
4. **Commit by intent**. If something is a coherent unit (adding a library, fixing a URL, bumping the version), it deserves its own commit. Avoid these two extremes:
   - ❌ One giant commit covering unrelated changes: hard to review, hard to revert.
   - ❌ A commit for every tiny edit: noise, harder to understand history.
5. Commit only the changes relevant to the current session. If there are other pending changes, ask the user whether you should commit them as well.
6. **Verify before pushing**: ensure `registry_metadata.json` checksums match the actual bytes of `known-libraries.json` and `registry-additional-info.json`, and that the `version` field has been bumped.

## Registry Update Checklist

Every time `known-libraries.json` or `registry-additional-info.json` is modified:

1. Recompute the SHA-256 checksums:
   ```bash
   python3 -c "
   import hashlib
   for path in ('docs/known-libraries.json', 'docs/registry-additional-info.json'):
       with open(path, 'rb') as f:
           data = f.read()
       print(path, 'sha256:' + hashlib.sha256(data).hexdigest())
   "
   ```
2. Update `checksum` and `additional_info_checksum` in `docs/registry_metadata.json` and bump `version` (use today's date: `YYYY-MM-DD`, or append `-N` for multiple releases in a day). You can also use `uv run scripts/validate.py checksum` to auto update. Refer to [README](README.md) for detailed instructions.
