# 11 Risks and technical debt

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Base image reaches end of life | No security updates | Ubuntu LTS only; Dependabot proposes the next one ([ADR](adr/ADR-20261114-distribution-packages-lean-image.md)). |
| veraPDF ships only a "latest" installer | Build not reproducible | Versions recorded per build (`ordnig version`), smoke test requires 2u. |
| OCR quality of old scans | Wrong or missing text, misfiled documents | `--fix-scans`; `_Prüfen` below thresholds; `--eval` before production. |
| Lock on a mount without POSIX locks | Two runs at once | Documented requirement ([7](07-deployment.md)). |
| Conversion log tied to mount paths | Repeated conversions | See [Q2](08-concepts.md#q2). |

## Known limits

- Handwriting (Kurrent, Sütterlin) is not recognised; Fraktur via `--lang deu+frk`.
- Spreadsheets as PDF are for reading; the ODS keeps formulas.
- Encrypted PDFs are skipped and listed in the log.
- Fonts without Unicode mapping (Symbol, Wingdings) can block 2u → stays 2b.
- `sort`: a letter for an earlier year needs a `jahr` pattern; HEIC and `.msg` are not read.
- ordnig ≤ 0.7 ignores lock and `version.json`.
