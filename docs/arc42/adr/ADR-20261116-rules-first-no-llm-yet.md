# Rules first, similarity second, no LLM yet

Status: Proposed (2026-11-16)

## Decision

The first matching rule wins; otherwise similarity decides. An LLM fallback comes only if `--eval` and real use show many files in `_Prüfen`.

## Why

Rules are predictable like mail filters; an LLM adds cost and data flow without proven need. Out of scope for now: [#15](https://github.com/Tiliavir/ordnig/issues/15).
