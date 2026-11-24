# ordnig

*Ordnig* (Alemannic): order, tidiness.

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

The console shows one summary table per step. The reports in `/report`
(Markdown) show the whole archive as a tree with every finding per
file and folder totals, followed by a summary. They list format risks, broken files (truncated PDF/JPEG,
corrupt ZIP/Office), wrong extensions, duplicates, encoding problems in file
names, names that collide on Windows/macOS, empty files and folders. It also
writes SHA-256 checksums to detect bit rot later.

## Usage

```sh
# 1. Look first (archive mounted read-only)
docker run --rm --user "$(id -u):$(id -g)" \
  -v /volume1/archive:/archive:ro -v "$PWD/report:/report" \
  ghcr.io/tiliavir/ordnig

# 2. Convert (work on a snapshot or copy first)
docker run --rm --user "$(id -u):$(id -g)" \
  -v /volume1/archive:/archive -v "$PWD/report:/report" \
  ghcr.io/tiliavir/ordnig run --images tif,tiff   # preview suggests the options

# 3. Later: check for bit rot
docker run --rm -v /volume1/archive:/archive:ro -v "$PWD/report:/report" \
  ghcr.io/tiliavir/ordnig verify
```

### Sort the inbox

`sort` files new documents from the inbox (`Inbox`, or any folder with `--inbox`)
into the archive. It learns from the archive itself (every folder is a class, year
folders become `{year}`) and follows your rules in `<inbox>/.ordnig/rules.toml`
first ([example](docs/rules.example.toml)). Anything it is unsure about goes to
`<inbox>/_Review/` (configurable) with a suggestion in the report.
Rules can also file a copy elsewhere, e.g. into the tax year.

```sh
docker run … ghcr.io/tiliavir/ordnig sort --eval    # how well would it sort? (uses the archive only)
docker run … ghcr.io/tiliavir/ordnig sort           # preview
docker run … ghcr.io/tiliavir/ordnig sort --apply   # move the files
```

For timers and cron jobs: exit code 0 done, 1 file errors, 2 setup (e.g. NAS
share not mounted), 3 another run is active, 4 files wait in the review folder.
Details: [exit codes](docs/arc42/08-concepts.md#exit-codes).

### Check for bit rot regularly

Run `verify` monthly, e.g. as a Synology *Task Scheduler* task (user-defined
script) or a cron job, and read the table it prints (the task can mail it):

```sh
# crontab: 03:00 on the 1st of every month
0 3 1 * * docker run --rm -v /volume1/archive:/archive:ro -v /volume1/ordnig-report:/report ghcr.io/tiliavir/ordnig verify
```

`run` can be interrupted and restarted; it continues where it stopped
(state in `<inbox>/.ordnig/`, without inbox in `<archive>/.ordnig/`). More options: `… ordnig help`; tool versions: `… ordnig version`.
The checksums live in `<inbox>/.ordnig/manifest.sha256`, so they travel with the archive and its backup; each check also leaves a copy in its report folder.

Details: [architecture (arc42)](docs/arc42/README.md) · [changelog](CHANGELOG.md) ·
[contributing and releasing](CONTRIBUTING.md) · License: [MIT](LICENSE).
