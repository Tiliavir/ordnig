# Format version of the state folder

Status: Proposed (2026-11-18)

## Decision

`.ordnig/version.json` records the format; newer ordnig versions lift older folders step by step, older ones refuse newer folders.

## Why

The state outlives containers and versions, so a change must be detected, not guessed.
