# 1 Introduction and goals

ordnig keeps a long-lived personal or family file archive readable for decades
and files incoming documents into it. The archive is a folder tree on a NAS or a
disk, possibly spread over several shares, grown over many years without a plan.

| Task | Command |
| --- | --- |
| Find formats that won't last, broken files, bad names, duplicates | `preview`, `check` |
| Add archival copies next to the originals: ODF for office files, PDF/A-2u with OCR text | `run` |
| Detect bit rot later | `verify` |
| File new documents from an inbox folder into the archive | `sort` |

ordnig is built as a product that others can install for their own archive. One
installation's folders, rules and schedule are configuration, not part of the design.

## Quality goals

| Priority | Goal | Meaning |
| ---: | --- | --- |
| 1 | No data loss | Originals are never changed or overwritten; every output is an additional file. |
| 2 | Honest results | PDF/A-2u only when veraPDF confirms it; unsure documents go to the review folder, nothing is guessed. |
| 3 | Adaptable | Folder structure, rules, languages and host are configuration; nothing assumes one archive. |
| 4 | Runs unattended | Restartable, one run at a time, exit codes a scheduler can act on. |
| 5 | Low maintenance | One container, Python stdlib, distribution packages, no host setup. |

## Stakeholders

| Role | Expects |
| --- | --- |
| Archive owner | Documents that still open in decades; incoming documents filed without manual work; reports to skim. |
| Operator (often the owner) | One `docker run`, clear exit codes, no state lost between versions or hosts. |
| Maintainer | Small code base, few dependencies, CI that proves every build. |
