# 8 Crosscutting concepts

## No data loss

- Originals are never changed; copies go next to them (`x.odt`, `x_pdfa.pdf`).
- Nothing is overwritten: name collisions get a free name (`_2`, `x.rtf.odt`).
- Moves across file systems: copy → fsync → compare SHA-256 → delete original.
- `preview` and the read-only commands work with the archive mounted `:ro`.
- Unclear cases are parked (review folder `_Review/`, log status `error`), never guessed.

## State: one `.ordnig` folder

Location: always `<inbox>/.ordnig/`; the inbox is `<archive>/Inbox` or `--inbox`,
never searched for. Writers stop (exit 2) when the inbox is missing
([ADR](adr/ADR-20261125-defaults-no-searching.md)).

| File | Written by | Content |
| --- | --- | --- |
| `rules.toml` | owner | rules, thresholds, ignored folders, year patterns, review folder |
| `texts.json.gz` | `sort` (also in preview) | cached texts of the archive by size and mtime |
| `sort-log.csv` | `sort --apply` | every filed file (with SHA-256), copy and review decision |
| `run-log.csv` | `run` | every conversion; newest row per file wins |
| `manifest.sha256` | `run`, first `check` | checksum baseline for `verify` (`sha256sum` format) |
| `version.json` | all writers | format of the folder and the ordnig version that set it |
| `lock` | all writers | POSIX lock for the whole run; names the holder |

The checksum baseline lives in `.ordnig` ([ADR](adr/ADR-20261117-state-folder.md)).
An older ordnig refuses a newer format (exit 2). Before production nothing is
migrated: a state folder of an older format or with the German file names of
versions up to 0.9 is refused (exit 2) and has to be moved away
([ADR](adr/ADR-20261125-no-migrations-before-production.md)).

Paths in `run-log.csv` are relative to the archive root, so the log survives any
mount point ([ADR](adr/ADR-20261117-state-folder.md)).

<a id="exit-codes"></a>
## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | done |
| 1 | errors in single files (`sort`, `run`), changed or missing files (`verify`), crash |
| 2 | wrong call or setup: not mounted, inbox missing, read-only, tools missing, bad `rules.toml`, state of another format |
| 3 | another run holds the lock |
| 4 | `sort` done, but files wait in the review folder |

<a id="mount-guard"></a>
## Mount guard

`sort` stops (exit 2) when the archive or a top-level mount point is empty, or
when the archive or a mount point holds less than half of the documents the last
run knew (from at least 20). Otherwise the cache would drop everything learned from
an unmounted share. `--archive-changed` accepts a real reorganisation.

## Classification (`sort`)

- Classes = folders of the archive; year folders become `{year}`. Ignored:
  `ignore` folders, system folders (recycle bins, snapshots, NAS indexes), the inbox.
- One example per document: ordnig's PDF/A copy first (it has OCR text), else
  the original. Scans count only after `run` added their text.
- Rules: first match wins; `any` (one of), `all` (all), `none` (none);
  regular expressions on file name and text.
- Similarity: TF-IDF, cosine, k = 7 nearest neighbours vote. Thresholds in
  `[thresholds]`.
- Year: newest date in the text up to today, or a `year` pattern per folder or rule.
- Reading: PDF text via Ghostscript (first 3 pages), OCR if there is none; images
  via OCR; ODF/OOXML from XML; legacy office via LibreOffice; `.eml` subject and
  body; text and HTML.

Defaults `share` 0.7 and `similarity` 0.2; each owner tunes them with `sort --eval`.

## Format assessment (`check`)

Every extension is `ok` (open, widespread), `watch` (common but proprietary or
complex) or `risk` (outdated or proprietary, convert). Structure checks find
truncated PDF/JPEG/PNG, broken ZIP-based files, encryption, wrong extensions
(libmagic), bad file names (encoding, NFD, Windows-invalid, reserved names),
empty files and folders, duplicates (SHA-256).

Copies made by a rule (`copy`) are intended duplicates: `sort` records the SHA-256 of
every filed document in `sort-log.csv`; `check` lists a duplicate group in its own
report section, not among the findings, when a rule filed that content at least as
many times. Renaming or moving a copy keeps it intended; one more identical file is a
finding again ([ADR](adr/ADR-20261117-copies-only-through-rules.md)).

## Reports

Console: one summary table per step. Files in `/report` (Markdown): the
archive as a tree with every finding per file and folder totals, summary at the
end; plus CSVs and the conversion log of the run.
