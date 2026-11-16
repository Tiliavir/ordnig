# Architecture

## Goal and constraints

Make a decades-old personal archive last: open formats, searchable text,
detectable bit rot. **No data loss**: originals stay untouched, every output is
an additional file. One container, no setup on the host.

## Building blocks

| Part | Job |
| --- | --- |
| `src/ordnig` | entry point: `preview`, `run`, `check`, `verify` |
| `src/check.py` | read-only scan → `report.md`, `files.csv`, `duplicates.csv`, `manifest.sha256`; stdlib only |
| `src/migrate.py` | plans and runs conversions; preview unless `--apply` |
| `src/sort.py` | files the inbox into the archive; preview unless `--apply`; stdlib only |
| LibreOffice (headless) | legacy office → ODF; ODF/office → tagged PDF/A-2 |
| OCRmyPDF + Tesseract | OCR text layer for pages without text, PDF/A-2 output |
| veraPDF | validates 2u; decides what gets declared |

## Conversion flow

1. Office file → ODF next to it (LibreOffice).
2. Office/ODF → PDF/A-2 (LibreOffice, tagged, fonts embedded). Used as is
   when every page has text; otherwise OCRmyPDF `--skip-text` for the pages
   without text (tagging is lost then).
3. PDF → OCRmyPDF `--skip-text`; image → img2pdf → OCRmyPDF. PDFs that are
   already PDF/A with text on every page are skipped. `--fix-scans` adds
   rotation and deskew (≈ 40 % more OCR time).
4. Workers place the PDF declared as 2b. Afterwards veraPDF validates in
   batches of 50: a copy declared `U` is checked for 2u; on success exactly
   that copy replaces the file, otherwise the 2b declaration is checked.
   Result in the log (newest row wins).

Parallelism: one file per CPU, one OCR thread per file (no oversubscription).
Per-file seconds are logged; the report lists the slowest files.
5. Output gets the original's modification time.

Targets are reserved during planning so parallel jobs never collide
(`x.doc` + `x.rtf` → `x.odt`, `x.rtf.odt`).

## Inbox sorting

1. Learn: walk the archive (without the inbox, `ignorieren` folders, `#recycle`,
   `@eaDir`, `OneNote_RecycleBin`). One example per document: ordnig's PDF/A copy
   first (it has the OCR text), else the original. No OCR here; scans count only
   after `ordnig run`. Texts are cached in `/report/cache/` by size and mtime.
   Label = folder; year folders (`2019`) become `{jahr}`.
2. Read each inbox file: PDF text via Ghostscript (first 3 pages), OCR with
   Tesseract if there is none; images OCR; ODF/OOXML from their XML; legacy
   office through LibreOffice; `.eml` subject + body; plain text and HTML.
3. Decide: first matching rule, else k nearest neighbours (TF-IDF, cosine,
   k = 7) vote. Filed only if the winner has ≥ 70 % of the vote weight and its
   best neighbour a similarity ≥ 0.2. `{jahr}` = newest date in the text up to
   today, or the rule's/folder's `jahr` pattern.
4. Move: same file system → rename; else copy, fsync, compare SHA-256, then
   delete the original. Never overwrite (`name_2.pdf`). Same content already in
   the target folder → `_Prüfen` ("schon abgelegt").

`--eval` sorts a sample of the archive as if it were new (leave-one-out) and
shows coverage and accuracy per threshold, year accuracy and the most frequent
confusions — the basis for choosing thresholds and writing rules.

## State

`<archive>/.ordnig/migration_log.csv` travels with the archive: generated
files are not converted again, finished sources are skipped, failed ones are
retried. Reports and the checksum baseline live in `/report`, outside the archive.

## Decisions (Proposed)

1. Copies next to the original, never replace it (no data loss, easy to compare).
2. Declare 2u only after veraPDF passes; never claim unverified conformance.
3. `preview` is the default; changes need the explicit `run`.
4. Images are opt-in (`--images`): photos should not become PDFs, scans should.
5. Existing PDFs get `_pdfa` as suffix instead of replacing them.
6. Ubuntu packages instead of pinned pip versions (fewer moving parts).
   Base image pinned by digest (Dependabot), `apt-get upgrade` at build for
   security fixes. Only what ordnig calls is installed: no `unpaper`/`pngquant`
   (OCRmyPDF needs them only for `--clean`/`--optimize 2`), and veraPDF runs on
   a `jlink` runtime with the modules of veraPDF's own image instead of the
   full JRE; installer, JDK, curl and unzip stay in a build stage. veraPDF only offers a "latest" installer: each build records
   all tool versions (`ordnig version`), and the smoke test on amd64 and arm64
   guards every build.
7. Release process as in Allmend: newest `CHANGELOG.md` version without a tag
   is released by CI (image with SBOM + provenance, tag, GitHub release).
8. Trivy fails the check only on *fixable critical* vulnerabilities; the
   image is large (LibreOffice, JRE) and unfixable findings would only be noise.

9. Inbox sorting learns from the archive's folders instead of a hand-made
   taxonomy: the archive already is the labelled data.
10. Rules first (first match wins, like mail filters), similarity second, no
    LLM yet. An LLM fallback is only worth it if `--eval`/real use shows many
    files in `_Prüfen`.
11. k nearest neighbours in the standard library instead of scikit-learn: no new
    dependency, and every decision names the most similar archived document.
12. Unsure, unreadable, no year for `{jahr}`, or duplicate → `_Prüfen/` with the
    reason; nothing is guessed. Wrong config (unknown keys, bad patterns,
    missing folders) stops the run with all errors listed.
13. Files keep their names; only collisions get `_2`. A naming scheme can come later.
14. Rules live with the inbox (`00_Eingang/.ordnig/regeln.toml`), the text cache in
    `/report/cache/` (preview writes nothing to the archive).

## Quality gates

Unit tests (stdlib `unittest`), shellcheck, smoke test of the real image on
amd64 and arm64 (veraPDF must confirm 2u), Trivy. `main` only through PRs
with these checks green.

## Known limits

- `sort`: `{jahr}` uses the newest date up to today; a tax assessment for 2025
  sent in 2026 needs a `jahr` pattern. HEIC photos and Outlook `.msg` are not read.
- Handwriting (Kurrent, Sütterlin) is not recognized; Fraktur via `--lang deu+frk`.
- Spreadsheets as PDF are for reading; the ODS keeps formulas.
- Encrypted PDFs are skipped and listed in the log.
- Fonts without Unicode mapping (Symbol, Wingdings) can block 2u → stays 2b.
