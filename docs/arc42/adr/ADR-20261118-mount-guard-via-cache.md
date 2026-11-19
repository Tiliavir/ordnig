# Mount guard via the cache

Status: Proposed (2026-11-18)

## Decision

`sort` stops when a mount point is empty or most of the documents known from the last run are gone; `--ablage-geaendert` overrides.

## Why

No marker file to set up, and it also catches a share mounted at the wrong path. Without it the cache would drop what was learned.
