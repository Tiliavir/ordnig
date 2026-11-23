# English throughout, folder names are configuration

Status: Proposed (2026-11-23)

## Decision

Everything ordnig shows or reads is English: CLI options and messages, reports, log
columns and values, the rules file (`rules.toml`, keys such as `ignore`, `rule`,
`target`, `copy`, placeholder `{year}`) and the container mount (`/archive`).
No translation layer.

Folders ordnig looks for or creates are configuration with English defaults:

- Inbox: `--inbox PATH`; otherwise the one folder under the archive that holds
  `.ordnig/`; otherwise `<archive>/Inbox`. The name cannot live in the rules file,
  because that file lives in the inbox.
- Review folder: `[folders] review` in `rules.toml`, default `_Review`.

The state-format lift ([ADR](ADR-20261118-state-format-version.md)) migrates existing
state folders: renames the files and rewrites the known rule keys, and reports what
it changed.

## Why

German limits the product to German speakers, and an archive's folder names belong to
its owner, in the owner's language. A second UI language is not needed now; it can come
later without touching the configuration format.
