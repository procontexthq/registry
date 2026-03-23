# Contributing to the ProContext Registry

Thanks for helping improve the registry! Before contributing, please read the [README.md](README.md) for an overview of the project.

## Ways to Contribute

- **Add a new library** — a library you use that has a public `llms.txt` file
- **Fix an existing entry** — correct a URL, name, alias, or package name
- **Remove a stale entry** — a library whose `llms_txt_url` is no longer reachable

---

## Before You Add a New Entry

**Check whether the library already exists.** Search `docs/known-libraries.json` for the library name, package name, or a known alias. If an entry already exists, update it instead of creating a new one.

Also check whether another library in the registry **shares the same `llms_txt_url`**. If it does, add your library's packages and aliases to that existing entry rather than creating a separate one (see [Grouping rules](#grouping-libraries-under-one-entry) below).

---

## Adding a Library

### 1. Check eligibility

The library must have a publicly accessible `llms.txt` file. Verify it is reachable before submitting:

```bash
curl -I https://docs.example.com/llms.txt
```

Expect a `200 OK` response. Do not add entries whose `llms_txt_url` returns an error or redirects to a generic page.

### 2. Add an entry to `docs/known-libraries.json`

Append your entry to the JSON array. See [registry-schema.md](registry-schema.md) for the full field reference.

```json
{
  "id": "my-library",
  "name": "My Library",
  "description": "A brief description of what the library does.",
  "llms_txt_url": "https://docs.example.com/llms.txt",
  "aliases": ["mylib"],
  "packages": [
    {
      "ecosystem": "pypi",
      "languages": ["python"],
      "package_names": ["my-library"],
      "readme_url": "https://raw.githubusercontent.com/org/my-library/main/README.md",
      "repo_url": "https://github.com/org/my-library"
    }
  ]
}
```

**Required fields:** `id`, `name`, `llms_txt_url`

**`id` rules:** lowercase, alphanumeric, hyphens and underscores only, must start with a letter or digit — e.g. `langchain`, `openai`, `react`

### 3. Validate and update the checksums

```bash
uv run scripts/validate.py all
```

This validates the file structure and updates `registry_metadata.json` with fresh checksums for both registry JSON files. Run `--urls` to also verify the URL is reachable:

```bash
uv run scripts/validate.py all --urls
```

---

## Field Reference

See **[registry-schema.md](registry-schema.md)** for the complete field reference, including:

- Library-level fields (`id`, `name`, `description`, `llms_txt_url`, `aliases`, `packages`)
- Optional library-level fields such as `llms_full_txt_url`
- `PackageEntry` fields (`ecosystem`, `languages`, `package_names`, `readme_url`, `repo_url`)
- Valid `ecosystem` values (`"pypi"`, `"npm"`, `"conda"`, `"jsr"`)
- Supplemental `registry-additional-info.json` fields
- The `resolve_library` response format

---

## Grouping Libraries Under One Entry

**Libraries that share the same `llms.txt` should be grouped into a single entry.**

A single `llms.txt` often covers an entire ecosystem of related packages (e.g. a core library and its official plugins). Creating separate entries for each package would point ProContext to the same documentation file, which is redundant.

**Group them when:**
- Multiple packages share the exact same `llms_txt_url`
- The packages are maintained together under the same docs site

**Keep them separate when:**
- Each library has its own distinct `llms.txt`
- The libraries are from different maintainers and have genuinely different documentation

### Examples

**Correct — grouped (same `llms.txt`, multiple packages):**
```json
{
  "id": "langchain-python",
  "name": "LangChain (Python)",
  "llms_txt_url": "https://docs.langchain.com/llms.txt",
  "packages": [
    {
      "ecosystem": "pypi",
      "languages": ["python"],
      "package_names": ["langchain", "langchain-core", "langchain-community", "langchain-openai"],
      "repo_url": "https://github.com/langchain-ai/langchain"
    }
  ]
}
```

`langchain-core` and `langchain-openai` are not separate entries — they share the same `llms.txt` as `langchain`.

---

**Correct — separate entries (different `llms.txt`):**
```json
{
  "id": "react",
  "name": "React",
  "llms_txt_url": "https://react.dev/llms.txt",
  "packages": [
    {
      "ecosystem": "npm",
      "languages": ["javascript", "typescript"],
      "package_names": ["react", "react-dom"]
    }
  ]
}
```
```json
{
  "id": "next-js",
  "name": "Next.js",
  "llms_txt_url": "https://nextjs.org/docs/llms.txt",
  "packages": [
    {
      "ecosystem": "npm",
      "languages": ["javascript", "typescript"],
      "package_names": ["next"]
    }
  ]
}
```

React and Next.js each have their own `llms.txt`, so they get their own entries.

---

**Incorrect — do not do this:**
```json
{ "id": "langchain-core", "llms_txt_url": "https://docs.langchain.com/llms.txt" }
{ "id": "langchain-openai", "llms_txt_url": "https://docs.langchain.com/llms.txt" }
```

These duplicate the same `llms.txt` and should instead be listed as `package_names` under the `langchain-python` entry.

---

### Same library, separate per-language documentation

Some libraries publish independent `llms.txt` files for each language SDK. In that case, create one entry per language and append a language shorthand to the `id`:

| Language | Shorthand |
|----------|-----------|
| Python | `-python` |
| JavaScript | `-js` |
| TypeScript | `-ts` |
| Go | `-go` |
| Rust | `-rust` |
| Java | `-java` |
| Ruby | `-rb` |
| Kotlin | `-kt` |
| Swift | `-swift` |
| .NET / C# | `-dotnet` |
| PHP | `-php` |

**Example — a library with distinct Python and JS docs:**
```json
{
  "id": "openai-python",
  "name": "OpenAI SDK (Python)",
  "llms_txt_url": "https://platform.openai.com/llms.txt",
  "aliases": ["openai-sdk"],
  "packages": [
    {
      "ecosystem": "pypi",
      "languages": ["python"],
      "package_names": ["openai"],
      "readme_url": "https://raw.githubusercontent.com/openai/openai-python/main/README.md",
      "repo_url": "https://github.com/openai/openai-python"
    }
  ]
}
```
```json
{
  "id": "openai-js",
  "name": "OpenAI SDK (JavaScript)",
  "llms_txt_url": "https://platform.openai.com/llms.txt",
  "packages": [
    {
      "ecosystem": "npm",
      "languages": ["javascript", "typescript"],
      "package_names": ["openai"],
      "readme_url": "https://raw.githubusercontent.com/openai/openai-node/main/README.md",
      "repo_url": "https://github.com/openai/openai-node"
    }
  ]
}
```

Do **not** use this pattern when both languages are covered by the same `llms.txt` — use a single entry with two `packages` array items instead.

---

## Making Changes to Existing Entries

1. Edit the relevant entry in `docs/known-libraries.json`.
2. If you are fixing a broken `llms_txt_url`, verify the new URL is reachable before submitting.
3. Run validation and update the checksums:

```bash
uv run scripts/validate.py all
```

---

## Submitting Your Changes

1. Fork this repository and create a branch with a descriptive name (e.g. `add-langchain`, `fix-openai-url`).
2. Make your changes following the steps above.
3. Run `uv run scripts/validate.py all` and confirm it exits cleanly.
4. Open a pull request with a short description of what you added or changed and why.
