# 8 Crosscutting concepts

## No data loss

- Originals are never changed; copies go next to them (`x.odt`, `x_pdfa.pdf`).
- Nothing is overwritten: name collisions get a free name (`_2`, `x.rtf.odt`).
- Moves across file systems: copy → fsync → compare SHA-256 → delete original.
- `preview` and the read-only commands work with the archive mounted `:ro`.
- Unclear cases are parked (review folder `_Prüfen/`, log status `fehler`), never guessed.

## State: one `.ordnig` folder

Location: `--eingang`, else `<archive>/00_Eingang`, else exactly one
`<archive>/*/00_Eingang`; only an archive without inbox keeps it in `<archive>/.ordnig/`.

| File | Written by | Content |
| --- | --- | --- |
| `regeln.toml` | owner | rules, thresholds, ignored folders, year patterns |
| `texte.json.gz` | `sort` (also in preview) | cached texts of the archive by size and mtime |
| `protokoll.csv` | `sort --apply` | every filed file (with SHA-256), copy and review decision |
| `migration_log.csv` | `run` | every conversion; newest row per file wins |
| `version.json` | all writers | format of the folder and the ordnig version that set it |
| `lock` | all writers | POSIX lock for the whole run; names the holder |

A log at the old place (`<archive>/.ordnig/`, up to 0.7) is read and taken over at
the next `run`; the original stays as `….übernommen`. A newer ordnig lifts an older
format step by step; an older ordnig refuses a newer one (exit 2).

<a id="q2"></a>
> [!IMPORTANT]
> **Q2 – Container paths in the conversion log:** `migration_log.csv` stores paths as the container sees them (`/ablage/share2/…`).
> Mounted differently, `run` no longer recognises generated files and may convert them
> again. Store paths relative to the archive root instead (format 2, with a migration)?

<a id="exit-codes"></a>
## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | done |
| 1 | errors in single files (`sort`, `run`), changed or missing files (`verify`), crash |
| 2 | wrong call or setup: not mounted, read-only, tools missing, bad `regeln.toml`, newer state format |
| 3 | another run holds the lock |
| 4 | `sort` done, but files wait in `_Prüfen` |

<a id="mount-guard"></a>
## Mount guard

`sort` stops (exit 2) when the archive or a top-level mount point is empty, or
when the archive or a mount point holds less than half of the documents the last
run knew (from at least 20). Otherwise the cache would drop everything learned from
an unmounted share. `--ablage-geaendert` accepts a real reorganisation.

## Classification (`sort`)

- Classes = folders of the archive; year folders become `{jahr}`. Ignored:
  `ignorieren` folders, `#recycle`, `@eaDir`, `OneNote_RecycleBin`, the inbox.
- One example per document: ordnig's PDF/A copy first (it has OCR text), else
  the original. Scans count only after `run` added their text.
- Rules: first match wins; `muster` (one of), `alle` (all), `nicht` (none);
  regular expressions on file name and text.
- Similarity: TF-IDF, cosine, k = 7 nearest neighbours vote. Thresholds in
  `[schwellen]`.
- Year: newest date in the text up to today, or a `jahr` pattern per folder or rule.
- Reading: PDF text via Ghostscript (first 3 pages), OCR if there is none; images
  via OCR; ODF/OOXML from XML; legacy office via LibreOffice; `.eml` subject and
  body; text and HTML.

<a id="q8"></a>
> [!IMPORTANT]
> **Q8 – Thresholds:** The defaults `anteil` 0.7 and `aehnlichkeit` 0.2 are a first guess. Each owner tunes
> them with `sort --eval`. Should the default aim at a fixed error rate (e.g. ≤ 2 % wrongly
> filed, more in the review folder), and should `--eval` suggest the values?

## System folders

All commands skip the same folders that never belong to the archive: ordnig's
own `.ordnig`, `.git`, recycle bins, snapshots and indexes of NAS systems (Synology,
QNAP, NetApp/NFS, Btrfs) and operating systems (Windows, macOS). Snapshots would
otherwise count every file twice. `sort` also skips hidden folders and the owner's
`ignorieren` list.

## Format assessment (`check`)

Every extension is `ok` (open, widespread), `watch` (common but proprietary or
complex) or `risk` (outdated or proprietary, convert). Structure checks find
truncated PDF/JPEG/PNG, broken ZIP-based files, encryption, wrong extensions
(libmagic), bad file names (encoding, NFD, Windows-invalid, reserved names),
empty files and folders, duplicates (SHA-256).

### Intended copies

`sort` records the SHA-256 of every filed document in `protokoll.csv`. `check`
reads it: a duplicate group whose hash a rule copied, with no more members than the
rule made, is an intended copy (ℹ in the tree, not counted as redundant). One more
identical file is a duplicate again. Matching by hash survives moving or renaming;
rows from before 0.9 are matched by path ([ADR](adr/ADR-20261122-intended-copies-by-hash.md)).

## Reports

Console: one summary table per step. Files in `/report` (German Markdown): the
archive as a tree with every finding per file and folder totals, summary at the
end; plus CSVs and the conversion log of the run.
