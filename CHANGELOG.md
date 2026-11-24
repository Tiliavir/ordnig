# Changelog

All notable changes. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions: [Semantic Versioning](https://semver.org/). Releasing is described in
[CONTRIBUTING.md](CONTRIBUTING.md#releasing).

## [Unreleased]

### Changed

- **Breaking: English throughout.** CLI options, messages, reports, log columns and
  values, the rules file and the container mount are English; folder names are
  configuration ([ADR](docs/arc42/adr/ADR-20261123-english-ui-configurable-folders.md)):
  - Mount `/archive` (was `/ablage`); options `--inbox`, `--archive-changed`, `--sample`.
  - Inbox: `--inbox`, else the folder one or two levels below the archive that holds
    `.ordnig/`, else `<archive>/Inbox`. An existing `00_Eingang` keeps working once it
    holds `.ordnig/`, or with `--inbox`.
  - Review folder `_Review`, configurable as `[folders] review` in `rules.toml`.
  - State files `rules.toml`, `sort-log.csv`, `run-log.csv`, `texts.json.gz`; rule keys
    `ignore`, `[thresholds]` (`share`, `similarity`), `[year]`, `[[rule]]` with
    `target`, `any`, `all`, `none`, `year`, `copy`, `copy_year`; placeholder `{year}`
    ([example](docs/rules.example.toml)).
  - No migration (pre-production): a `.ordnig` folder with the German file names is
    refused (exit 2). Move it away and write `rules.toml` with the new keys.
- `check` and `verify` skip the inbox like `run` does: unsorted mail no longer shows
  up as format risks, and filed mail no longer as "moved". Inbox entries in an older
  `manifest.sha256` are ignored, so the first `verify` after the update reports no
  missing files.
- `run-log.csv` stores paths relative to the archive instead of container paths, so a
  different mount point no longer makes `run` convert generated files again.
- The checksum baseline for `verify` lives in `<inbox>/.ordnig/manifest.sha256`
  instead of `/report`, so it travels with the archive and its backup. Each `check`
  still leaves a copy in its report folder. A baseline in `/report` is not taken over:
  run `check` once to create a new one.
- All commands skip the same system folders, now also snapshots and recycle bins of
  QNAP, NetApp/NFS, Btrfs, Windows and macOS (Synology already before).
- Architecture documented as arc42 in `docs/arc42/` (product view); replaces
  `docs/architecture.md` and `OPEN.md`.

### Added

- `check` lists copies made by a `sort` rule (`copy`) as intended duplicates in their
  own report section (⎘ in the tree, `intended = yes` in `duplicates.csv`, `copy`
  column in `files.csv`); the duplicate findings and the redundant size leave them out.
  They are recognised by content: `sort` records the SHA-256 of every filed document
  in `sort-log.csv`, so renaming or moving a copy keeps it intended; one more
  identical file is a duplicate again.
- Warning when the state folder lies on an SMB/cifs mount with `nobrl`: the lock then
  only works on this machine, a run on another machine is not prevented.

### Removed

- Taking over state from `<archive>/.ordnig/` (up to 0.7) and lifting older logs; before
  production a state folder of another format is refused instead.

## [0.8.1] - 2026-11-22

### Changed

- Base image Ubuntu 26.04 LTS (supported until 2031) instead of 25.10, which
  has had no security updates since 9 July 2026. The image shrinks by about
  60 MB.

## [0.8.0] - 2026-11-18

### Added

- Exit codes for unattended runs: 0 done, 1 file errors, 2 wrong call or setup,
  3 locked by another run, 4 `sort` left files in `_Prüfen`. `verify` returns 1
  when files changed or are missing.
- Only one run at a time (`run` or `sort`; lock, works across machines on NFS/SMB).
- `.ordnig/version.json` records the state format; a newer format stops an
  older ordnig instead of being misread.
- `sort` stops when a NAS share looks unmounted (empty mount point, or most of
  the known documents gone) instead of throwing away what it learned.
  `--ablage-geaendert` accepts a real reorganisation.

### Changed

- One state folder: `run` keeps its conversion log in `00_Eingang/.ordnig/` too
  (found automatically, also as `/ablage/*/00_Eingang` with two shares; `--eingang`
  for `run`). A log in `<archive>/.ordnig/` is taken over at the next `run`; an
  archive without inbox keeps it there.
- `run` leaves the inbox alone: mail is converted by the next `run` after
  `sort` filed it, so original and copies are never filed separately.
- `sort` finds the inbox the same way, so `--eingang` is only needed when there
  are several.
- `run` finishes the check afterwards even when single files failed, then exits 1.
- Setup errors (missing archive, tools, bad `regeln.toml`) exit with 2 instead of 1.

## [0.7.0] - 2026-11-18

### Added

- `ordnig sort` files new documents from `00_Eingang` into the archive: rules
  from `00_Eingang/.ordnig/regeln.toml` first, then the most similar documents
  already in the archive. Year folders are filled from the date in the letter.
  Unsure, unreadable or duplicate files go to `00_Eingang/_Prüfen/` with the
  reason and a suggestion. Preview unless `--apply`; `--eval` measures accuracy
  on the archive itself.
- Rules can also copy a file, e.g. dividends or annual insurance bills into the
  tax folder: `kopie = "…/0_Steuer/{jahr}"`, optional `kopie_jahr` pattern.
- All sort state lives in `00_Eingang/.ordnig/` (rules, learned texts, history
  `protokoll.csv`), so the container can run on any machine.

### Changed

- Smaller image: veraPDF runs on a minimal Java runtime (`jlink`) instead of
  the full JRE; `unpaper`, `pngquant` (with ffmpeg libraries), the veraPDF GUI,
  `curl` and `unzip` are gone. `ordnig version` also shows the Java version.

## [0.6.0] - 2026-11-14

### Changed

- Faster conversion (test archive: 70 s → 44 s on 2 cores):
  - Office files: the PDF/A from LibreOffice is used directly when every page
    has text; OCR runs only for pages without text. Keeps the PDF tagging.
  - Rotating and deskewing scans is opt-in: `--fix-scans` (≈ 40 % more OCR
    time). `preview` suggests it when scans are found.
  - PDFs that already are PDF/A with text on every page are left alone.
  - One file per CPU with one OCR thread each (`-j` sets the number of files)
    instead of half as many files with all CPUs each.
  - veraPDF validates in batches of 50 (one JVM start per batch); the file
    that passed is exactly the one put in place.
- The migration log has a column `sekunden`; the report lists the slowest
  files, the console shows run time and average per file. Old logs are
  upgraded on the next run.

## [0.5.0] - 2026-11-14

### Added

- `preview` ends with a suggested `run` call based on the archive: TIFF files
  are included as scans (`--images tif,tiff`), other images (JPG, PNG, …) are
  listed with a hint, given options (`--lang`, `-j`) are kept.

## [0.4.0] - 2026-11-14

### Added

- `ordnig version` lists the versions of all tools in the image (veraPDF,
  OCRmyPDF, Tesseract, LibreOffice, Ghostscript, base image).
- Images for amd64 and arm64 are both smoke-tested on native runners.
- Unit tests for name, integrity and PDF/A checks, the report tree and the
  target naming of the converter.
- Vulnerability scan (Trivy) of the image; fails on fixable critical findings.
- Release images carry an SBOM and provenance attestation; GitHub release
  with the changelog section.
- Dependabot for GitHub Actions and the base image.
- MIT license.

### Changed

- Base image pinned by digest; security updates are installed at build time.
- Releases are driven by this changelog (as in Allmend); `VERSION` is gone,
  `:latest` moves only on a release.

## [0.3.0] - 2026-11-14

### Changed

- Console shows one summary table per step; reports show the archive as a
  tree with every finding, folder totals and a summary at the end.
- Migrated originals count as migrated; PDF/A part and level are detected.

## [0.2.0] - 2026-11-14

### Changed

- Renamed to ordnig (command, image, state folder `.ordnig/`).

## [0.1.0] - 2026-11-14

### Added

- Archive check (formats, integrity, names, duplicates, checksums) and
  conversion to ODF and PDF/A-2u with OCR, in one container.
