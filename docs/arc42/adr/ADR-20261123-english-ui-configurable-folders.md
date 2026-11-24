# English throughout, folder names are configuration

Status: Accepted (2026-11-24, proposed 2026-11-23)

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
   - Mount `/archive`; options `--inbox`, `--archive-changed`, `--sample`.
   - State files in `.ordnig/`: `rules.toml`, `sort-log.csv`, `run-log.csv`, `texts.json.gz`.
   - Rules file: `ignore`, `[thresholds]` (`k`, `share`, `similarity`), `[year]`,
     `[[rule]]` with `name`, `target`, `any`, `all`, `none`, `year`, `copy`, `copy_year`;
     placeholder `{year}`.
   - Log values `filed`, `review`.

## Why

1. German limits the product to German speakers. A second UI language is not needed
   now; it can come later without touching the configuration format.
2. An archive's folder names belong to its owner, in the owner's language.
3. One list, so code, docs and tests use the same names.
