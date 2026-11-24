# Originals stay untouched

Status: Proposed (2026-11-14)

## Decision

ordnig never replaces, changes or renames a user's file; it only adds files.

- Archival copies (ODF, PDF/A) go next to the original. An office file gets its ODF
  counterpart under the same stem (`x.odt`); an existing PDF gets `<name>_pdfa.pdf`.
  Colliding targets get the source extension in the name (`x.rtf.odt`).
- Filed documents keep their original names; only collisions get `_2`. A naming
  scheme would be a separate, optional feature.

## Why

No data loss, and original and copy can be compared side by side; the name shows
which file is the copy. Names carry meaning for the owner (sender's names, own
conventions), and renaming breaks links from elsewhere.
