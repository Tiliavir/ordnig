# Copies only through rules

Status: Proposed (2026-11-17)

## Decision

1. Additional copies (e.g. into the tax year) are made only by rules (`kopie`), as real copies.
2. **Recognised by content** (2026-11-24): `sort` records the SHA-256 of every filed
   document; `check` treats a duplicate group as intended when a rule filed that content
   at least as many times (original + copies).

## Why

1. Which documents are needed twice (e.g. for taxes) is the owner's decision, not
   something similarity can learn. No hard links: an archive may span several file
   systems.
2. Copies a rule made are wanted; reporting them as duplicates is noise that hides real
   ones. The hash survives renaming and moving by the owner, which path matching does
   not, and an extra identical file still stays a finding.
