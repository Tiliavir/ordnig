# State folder

Status: Proposed (2026-11-17)

## Decision

1. **One folder in the inbox:** everything that must survive a run lives in
   `00_Eingang/.ordnig/`: rules, cached texts, the `sort` history and, since 0.8, the
   conversion log of `run`. The inbox is found as `--eingang`, `<archive>/00_Eingang`
   or exactly one `<archive>/*/00_Eingang`. Only an archive without inbox keeps it in
   `<archive>/.ordnig/`.
2. **Format version** (2026-11-18): `.ordnig/version.json` records the format; newer
   ordnig versions lift older folders step by step, older ones refuse newer folders.
3. **Paths relative to the archive** (2026-11-23): the conversion log stores paths
   relative to the archive root; each run joins them with the root it has mounted.
   Several mounts keep their names below the root (`-v <share>:/ablage/<name>`); the
   name is part of the relative path. State format 2 lifts existing logs; paths
   outside the current archive stay absolute and are reported.
4. **Checksum baseline** (2026-11-23): the baseline for `verify` is state like the logs:
   `.ordnig/manifest.sha256`, written by `run` and by the first `check`. Each check
   still leaves its own copy in its report folder. A baseline in `/report` (up to 0.8)
   is taken over.

## Why

1. Containers are short-lived and may run on another host or in another version next
   time, so nothing may depend on the container or `/report`. With several mounts
   below `/ablage`, `/ablage/.ordnig` would be inside the container and lost. One
   folder means one lock for `run` and `sort`.
2. The state outlives containers and versions, so a change must be detected, not
   guessed.
3. The container cannot know host paths, and container paths change with the mount
   point. Relative paths survive both; the checksum manifest already works this way.
4. The baseline must survive changes of host and container like all other state, and
   the archive's backup then keeps versions of it. Keeping it on the same storage does
   not hide damage: a corrupted line shows up as a changed file (a false alarm), and
   the copies in the report folders stay off the storage.
