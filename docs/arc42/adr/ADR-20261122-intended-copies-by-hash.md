# Intended copies recognised by hash

Status: Proposed (2026-11-22)

## Decision

`sort` writes the SHA-256 of each filed document to `protokoll.csv`; `check` treats
a duplicate group as intended when a rule made exactly these copies. Older rows
without a hash are matched by path.

## Why

Copies made by a rule are wanted, so reporting them as duplicates is noise that
hides real ones. The hash is computed anyway while moving, still matches after the
owner moves or renames a file, and an extra identical file stays a finding.
