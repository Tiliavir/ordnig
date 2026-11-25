# No migrations before production

Status: Proposed (2026-11-25)

Supersedes [State folder](ADR-20261117-state-folder.md) point 2 ("newer ordnig versions
lift older folders step by step") and the sentence on lifting logs in point 3.

## Decision

1. Until ordnig is ready for production, no state is migrated: a `.ordnig` folder of
   another format, or with the file names of an earlier version, is refused (exit 2)
   and has to be moved away.
2. The same holds for single files: a log with other columns stops the command before
   it changes anything.
3. `version.json` and the step-by-step mechanism stay; from production on, every
   incompatible change brings its migration.

## Why

1. There are no installations to carry along yet. Migration code for formats that
   never reached users only adds code paths to keep and test.
2. Rows appended under a different header would corrupt the log without a warning.
3. Once others rely on ordnig, an update must not cost them their state.
