# Checksums in the state folder

Status: Proposed (2026-11-23)

## Decision

The checksum baseline for `verify` is state like the logs: `.ordnig/manifest.sha256`,
written by `run` and by the first `check`. Each check still leaves its own copy in its
report folder. A baseline in `/report` (up to 0.8) is taken over.

## Why

The baseline must survive changes of host and container, like all other state
([ADR](ADR-20261117-state-in-the-inbox.md)), and with it the archive's backup keeps
versions of it. Keeping it on the same storage does not hide damage: a corrupted line
shows up as a changed file (a false alarm), and the copies in `/report` stay off the
storage. Previously it lived only in `/report`, which is on whichever host ran ordnig.
