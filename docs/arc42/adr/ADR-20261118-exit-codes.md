# Exit codes as the automation interface

Status: Proposed (2026-11-18)

## Decision

0 done · 1 file errors or crash · 2 wrong call or setup · 3 locked · 4 `sort` left files in `_Prüfen`.

## Why

A scheduler needs no output parsing, and "files wait in `_Prüfen`" is distinct from errors.
