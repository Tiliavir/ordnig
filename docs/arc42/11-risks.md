# 11 Risks and technical debt

<a id="q1"></a>
> [!IMPORTANT]
> **Q1 – Base image end of life:** The image is built on Ubuntu 25.10, an interim release that reached end of life on
> 9 July 2026: no security updates since, so `apt-get upgrade` at build time no longer
> helps, and Dependabot only updates the digest of the same tag. Move to Ubuntu 26.04
> LTS (supported for five years) in its own PR?

| Risk | Effect | Mitigation |
| --- | --- | --- |
| veraPDF ships only a "latest" installer | Build not reproducible | Versions recorded per build (`ordnig version`), smoke test requires 2u. |
| OCR quality of old scans | Wrong or missing text, misfiled mail | `--fix-scans`; `_Prüfen` below thresholds; `--eval` before production. |
| Lock on a mount without POSIX locks | Two runs at once | See [Q3](07-deployment.md#q3). |
| Conversion log tied to mount paths | Repeated conversions | See [Q2](08-concepts.md#q2). |

## Known limits

- Handwriting (Kurrent, Sütterlin) is not recognised; Fraktur via `--lang deu+frk`.
- Spreadsheets as PDF are for reading; the ODS keeps formulas.
- Encrypted PDFs are skipped and listed in the log.
- Fonts without Unicode mapping (Symbol, Wingdings) can block 2u → stays 2b.
- `sort`: a letter for an earlier year needs a `jahr` pattern; HEIC and `.msg` are not read.
- ordnig ≤ 0.7 ignores lock and `version.json`.
