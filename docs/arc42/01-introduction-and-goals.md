# 1 Introduction and goals

ordnig keeps a decades-old personal file archive (two NAS shares, `home` and
`documents`) readable for the long term and files incoming mail into it.

| Task | Command |
| --- | --- |
| Find formats that won't last, broken files, bad names, duplicates | `preview`, `check` |
| Add archival copies next to the originals: ODF for office files, PDF/A-2u with OCR text | `run` |
| Detect bit rot later | `verify` |
| File new documents from `00_Eingang` into the archive | `sort` |

## Quality goals

| Priority | Goal | Meaning |
| ---: | --- | --- |
| 1 | No data loss | Originals are never changed or overwritten; every output is an additional file. |
| 2 | Honest results | PDF/A-2u only when veraPDF confirms it; unsure mail goes to `_Prüfen`, nothing is guessed. |
| 3 | Runs unattended | Restartable, one run at a time, exit codes a scheduler can act on. |
| 4 | Low maintenance | One container, Python stdlib, distribution packages, no host setup. |

## Stakeholders

| Who | Expects |
| --- | --- |
| Markus (owner, only user) | An archive that still opens in decades, mail filed without manual work, reports he can skim. |
