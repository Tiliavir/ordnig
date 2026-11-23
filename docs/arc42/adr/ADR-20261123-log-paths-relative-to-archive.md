# Log paths relative to the archive

Status: Proposed (2026-11-23)

## Decision

The conversion log stores paths relative to the archive root; each run joins them with
the root it has mounted. Several mounts keep their names below the root
(`-v <share>:/ablage/<name>`); the name is part of the relative path. State format 2
lifts existing logs; paths outside the current archive stay absolute and are reported.

## Why

The container cannot know host paths, and container paths change with the mount
point. Relative paths survive both; the checksum manifest already works this way.
