# State in the inbox

Status: Proposed (2026-11-17)

## Decision

Everything that must survive a run lives in one folder, `00_Eingang/.ordnig/`: rules, cached texts, the `sort` history and, since 0.8, the conversion log of `run`. The inbox is found as `--eingang`, `<archive>/00_Eingang` or exactly one `<archive>/*/00_Eingang`. Only an archive without inbox keeps it in `<archive>/.ordnig/`.

## Why

Markus runs the container on his PC, not on the NAS; nothing may depend on the container or `/report`. (stated) With two shares mounted below `/ablage`, `/ablage/.ordnig` would be inside the container and lost. One folder means one lock for `run` and `sort`.
