# 12 Glossary

| Term | Meaning |
| --- | --- |
| Archive | The owner's files: one or more mounts below `/archive`. |
| Inbox | Folder for new documents of any format; default `Inbox`, configurable (`--inbox`). |
| `_Review` | Folder in the inbox for files `sort` did not file, each with a reason in the report; configurable. |
| `.ordnig` | State folder in the inbox (rules, cache, logs, lock, version). |
| `rules.toml` | The owner's rules for `sort`. |
| `{year}` | Placeholder for a year folder in a target path. |
| `copy` | Additional copy of a filed document made by a rule, e.g. in a tax year folder. |
| PDF/A-2u / 2b | Archival PDF; 2u additionally guarantees Unicode text (searchable, copyable). |
| ODF | OpenDocument (`.odt`, `.ods`, `.odp`). |
| Bit rot | Silent change of stored data; found by comparing SHA-256 checksums. |
