# Safe unattended runs

Status: Proposed (2026-11-18)

## Decision

1. **Exit codes:** 0 done · 1 file errors or crash · 2 wrong call or setup · 3 locked ·
   4 `sort` left files in `_Review`.
2. **One lock:** writers hold a POSIX lock (`lockf`) on `.ordnig/lock` for the whole run.
3. **Mount guard:** `sort` stops when a mount point is empty or most of the documents
   known from the last run are gone; `--archive-changed` overrides.

## Why

1. A scheduler needs no output parsing, and "files wait in `_Review`" is distinct from
   errors.
2. On NFS and SMB only POSIX locks reach the server, and the container may run on
   different machines. The kernel releases the lock on a crash, so there is no stale
   lock. Requirement for the storage: see [deployment](../07-deployment.md).
3. No marker file to set up, and it also catches a share mounted at the wrong path.
   Without it the cache would drop what was learned.
