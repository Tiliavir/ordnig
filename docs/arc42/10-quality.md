# 10 Quality requirements

| Scenario | Expected |
| --- | --- |
| `run` is interrupted (power, Ctrl+C) | Next `run` continues; no original changed, no half copy taken as done. |
| Storage not mounted when the scheduler fires | `sort` exits 2, nothing moved, cache unchanged. |
| `sort` started twice, also from two machines | Second exits 3. |
| Older image started after a newer one wrote the state | Exits 2 instead of misreading it. |
| Another archive: different folders, one share, other languages | Works with configuration only (mounts, inbox, `rules.toml`, `--lang`); no code change. |
| Document similar to two folders | Review folder with reason and suggestion, not filed. |
| PDF that does not pass 2u | Declared and kept as 2b, reason in the log. |

## Quality gates

Unit tests (stdlib `unittest`), shellcheck, smoke test of the real image on amd64
and arm64 (veraPDF must confirm 2u; exit codes 3 and 4), Trivy (fails on fixable
critical findings), image size compared with the latest release.
