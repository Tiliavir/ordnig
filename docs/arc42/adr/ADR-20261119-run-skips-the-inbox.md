# `run` skips the inbox

Status: Proposed (2026-11-19)

## Decision

`run` does not convert files in `00_Eingang`; they are converted by the next `run` after `sort` filed them.

## Why

Copies next to unsorted documents would be filed separately from their original.
