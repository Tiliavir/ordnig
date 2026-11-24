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

No migration of existing state folders (pre-production): a state folder with the
old German names is refused with a clear message.

## Why

German limits the product to German speakers, and an archive's folder names belong to
its owner, in the owner's language. A second UI language is not needed now; it can come
later without touching the configuration format.
