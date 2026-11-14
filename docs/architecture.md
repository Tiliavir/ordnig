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
2. Office/ODF → PDF/A-2 (LibreOffice, tagged, fonts embedded) →
   OCRmyPDF `--skip-text` (only pages without text get OCR).
3. PDF → OCRmyPDF `--skip-text`; image → img2pdf → OCRmyPDF.
4. Set XMP `pdfaid:conformance=U`, validate with veraPDF `--flavour 2u`.
   Fail → set `B`, validate 2b. Result in the log.
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
6. Ubuntu 24.04 packages instead of pinned pip versions (fewer moving parts);
   veraPDF installer is only available as "latest" — CI smoke test guards it.

## Known limits

- Handwriting (Kurrent, Sütterlin) is not recognized; Fraktur via `--lang deu+frk`.
- Spreadsheets as PDF are for reading; the ODS keeps formulas.
- Encrypted PDFs are skipped and listed in the log.
- Fonts without Unicode mapping (Symbol, Wingdings) can block 2u → stays 2b.
