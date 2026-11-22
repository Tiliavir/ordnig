# State in the inbox

Status: Proposed (2026-11-17)

## Decision

Everything that must survive a run lives in one folder, `00_Eingang/.ordnig/`: rules, cached texts, the `sort` history and, since 0.8, the conversion log of `run`. The inbox is found as `--eingang`, `<archive>/00_Eingang` or exactly one `<archive>/*/00_Eingang`. Only an archive without inbox keeps it in `<archive>/.ordnig/`.

## Why

Containers are short-lived and may run on another host or in another version next time, so nothing may depend on the container or `/report`. With several mounts below `/ablage`, `/ablage/.ordnig` would be inside the container and lost. One folder means one lock for `run` and `sort`.
