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

## Quality gates

Unit tests (stdlib `unittest`), shellcheck, smoke test of the real image on
amd64 and arm64 (veraPDF must confirm 2u), Trivy. `main` only through PRs
with these checks green.

## Known limits

- Handwriting (Kurrent, Sütterlin) is not recognized; Fraktur via `--lang deu+frk`.
- Spreadsheets as PDF are for reading; the ODS keeps formulas.
- Encrypted PDFs are skipped and listed in the log.
- Fonts without Unicode mapping (Symbol, Wingdings) can block 2u → stays 2b.
