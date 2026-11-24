# English throughout, folder names are configuration

Status: Proposed (2026-11-23)

## Decision

1. **English:** everything ordnig shows or reads is English: CLI options and messages,
   reports, log columns and values, the rules file and the container mount. No
   translation layer.
2. **Folders are configuration** with English defaults:
   - Inbox: `--inbox PATH`; otherwise the one folder under the archive that holds
     `.ordnig/`; otherwise `<archive>/Inbox`. The name cannot live in the rules file,
     because that file lives in the inbox.
   - Review folder: `[folders] review` in `rules.toml`, default `_Review`.
3. **Names:**

   | Before | Now |
   | --- | --- |
   | `00_Eingang` (inbox) | `Inbox` (default) |
   | `_Prüfen` | `_Review` (default) |
   | mount `/ablage` | `/archive` |
   | `--eingang`, `--ablage-geaendert`, `--stichprobe` | `--inbox`, `--archive-changed`, `--sample` |
   | `.ordnig/regeln.toml` | `.ordnig/rules.toml` |
   | `.ordnig/protokoll.csv`, `migration_log.csv` | `.ordnig/sort-log.csv`, `run-log.csv` |
   | `.ordnig/texte.json.gz` | `.ordnig/texts.json.gz` |
   | `ignorieren`, `[schwellen]` (`anteil`, `aehnlichkeit`) | `ignore`, `[thresholds]` (`share`, `similarity`) |
   | `[jahr]`, placeholder `{jahr}` | `[year]`, `{year}` |
   | `[[regel]]`: `ziel`, `muster`, `alle`, `nicht`, `jahr`, `kopie`, `kopie_jahr` | `[[rule]]`: `target`, `any`, `all`, `none`, `year`, `copy`, `copy_year` |
   | log values `abgelegt`, `prüfen` | `filed`, `review` |

4. **No migration** (pre-production): a state folder with the old names is refused with
   a clear message.

## Why

1. German limits the product to German speakers. A second UI language is not needed
   now; it can come later without touching the configuration format.
2. An archive's folder names belong to its owner, in the owner's language.
3. One table, so code, docs and tests rename the same way.
4. There are no installations to carry along yet.
