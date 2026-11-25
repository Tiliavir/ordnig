# Defaults, no searching

Status: Proposed (2026-11-25)

Supersedes the inbox lookup in [English throughout](ADR-20261123-english-ui-configurable-folders.md)
point 2 and in [State folder](ADR-20261117-state-folder.md) point 1, including the state
folder in `<archive>/.ordnig/` for an archive without inbox.

## Decision

1. Every location has a fixed default or is a parameter; ordnig does not search for
   folders or guess them. The inbox is `<archive>/Inbox` or `--inbox PATH`; the state
   folder is always `<inbox>/.ordnig/`.
2. Commands that write state stop (exit 2) when the inbox does not exist; they do not
   create it.

## Why

1. The same call always uses the same folder. A search can find the wrong one (a copied
   or backed-up `.ordnig`) without saying so.
2. A missing inbox usually means a wrong mount; creating it would hide that and split the
   state.
