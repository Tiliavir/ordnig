# State folder

Status: Accepted (2026-11-24, proposed 2026-11-17)

Point 1 partly superseded by [Defaults, no searching](ADR-20261125-defaults-no-searching.md);
points 2 and 3 partly by [No migrations before production](ADR-20261125-no-migrations-before-production.md).

## Decision

1. **One folder in the inbox:** everything that must survive a run lives in
   `<inbox>/.ordnig/`: rules, cached texts, the `sort` history and, since 0.8, the
   conversion log of `run`. The inbox is found as described in
   [English throughout](ADR-20261123-english-ui-configurable-folders.md) (`--inbox`, the
   folder holding `.ordnig/`, `<archive>/Inbox`). Only an archive without inbox keeps it in
   `<archive>/.ordnig/`.
2. **Format version** (2026-11-18): `.ordnig/version.json` records the format; newer
   ordnig versions lift older folders step by step, older ones refuse newer folders.
3. **Paths relative to the archive** (2026-11-23): the conversion log stores paths
   relative to the archive root; each run joins them with the root it has mounted.
   Several mounts keep their names below the root (`-v <share>:/archive/<name>`); the
   name is part of the relative path. State format 2 lifts existing logs; paths
   outside the current archive stay absolute and are reported.
4. **Checksum baseline** (2026-11-23): the baseline for `verify` is state like the logs:
   `.ordnig/manifest.sha256`, written by `run` and by the first `check`. Each check
   still leaves its own copy in its report folder.

## Why

1. Containers are short-lived and may run on another host or in another version next
   time, so nothing may depend on the container or `/report`. With several mounts
   below `/archive`, `/archive/.ordnig` would be inside the container and lost. One
   folder means one lock for `run` and `sort`.
2. The state outlives containers and versions, so a change must be detected, not
   guessed.
3. The container cannot know host paths, and container paths change with the mount
   point. Relative paths survive both; the checksum manifest already works this way.
4. The baseline must survive changes of host and container like all other state, and
   the archive's backup then keeps versions of it. Keeping it on the same storage does
   not hide damage: a corrupted line shows up as a changed file (a false alarm), and
   the copies in the report folders stay off the storage.
