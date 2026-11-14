# archive-validator

Checks an old file archive for formats that won't last and for inconsistencies,
then puts archival copies **next to** the originals:

| Original | Added next to it |
| --- | --- |
| `.doc` `.docx` `.rtf` `.wpd` `.wps` `.sxw` … | `.odt` + PDF/A-2u |
| `.xls` `.xlsx` `.wk1` … | `.ods` + PDF/A-2u |
| `.ppt` `.pptx` … | `.odp` + PDF/A-2u |
| `.odt` `.ods` `.odp` | PDF/A-2u |
| `.pdf` | `<name>_pdfa.pdf`, PDF/A-2u, OCR on pages without text |
| scans (`--images tif,tiff,png`) | PDF/A-2u with OCR |

Originals are never changed, nothing is overwritten. PDF/A-2u is declared only
when veraPDF confirms it; otherwise 2b, with the reason in the log.

The report (German) lists format risks, broken files (truncated PDF/JPEG,
corrupt ZIP/Office), wrong extensions, duplicates, encoding problems in file
names, names that collide on Windows/macOS, empty files and folders. It also
writes SHA-256 checksums to detect bit rot later.

## Usage

```sh
# 1. Look first (archive mounted read-only)
docker run --rm --user "$(id -u):$(id -g)" \
  -v /volume1/ablage:/ablage:ro -v "$PWD/report:/report" \
  ghcr.io/tiliavir/archive-validator

# 2. Convert (work on a snapshot or copy first)
docker run --rm --user "$(id -u):$(id -g)" \
  -v /volume1/ablage:/ablage -v "$PWD/report:/report" \
  ghcr.io/tiliavir/archive-validator run --images tif,tiff

# 3. Later: check for bit rot
docker run --rm -v /volume1/ablage:/ablage:ro -v "$PWD/report:/report" \
  ghcr.io/tiliavir/archive-validator verify
```

`run` can be interrupted and restarted; it continues where it stopped
(state in `<archive>/.archive-validator/`). More options: `… archive-validator help`.
Keep `report/manifest.sha256` somewhere other than the archive.

Details: [docs/architecture.md](docs/architecture.md).
