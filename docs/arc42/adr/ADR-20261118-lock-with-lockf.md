# One lock with `lockf`

Status: Proposed (2026-11-18)

## Decision

Writers hold a POSIX lock (`lockf`) on `.ordnig/lock` for the whole run.

## Why

On NFS and SMB only POSIX locks reach the server, and the container may run on different machines. The kernel releases the lock on a crash, so there is no stale lock. Requirement for the storage: see [deployment](../07-deployment.md).
