# What `run` converts

Status: Accepted (2026-11-24, proposed 2026-11-14)

## Decision

- Office files and PDFs are converted; images only for the extensions given with
  `--images`.
- Files in the inbox are skipped; the next `run` after `sort` filed them converts
  them where they landed (2026-11-19).

## Why

- Scans should become searchable PDFs, photos should not.
- Copies next to unsorted documents would be filed separately from their original.
