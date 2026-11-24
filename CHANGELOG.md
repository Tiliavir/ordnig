# Changelog

All notable changes. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions: [Semantic Versioning](https://semver.org/). Releasing is described in
[CONTRIBUTING.md](CONTRIBUTING.md#releasing).

## [Unreleased]

### Changed

- `check` and `verify` skip the inbox like `run` does: unsorted mail no longer shows
  up as format risks, and filed mail no longer as "moved". Inbox entries in an older
  `manifest.sha256` are ignored, so the first `verify` after the update reports no
  missing files.
- State format 2: `migration_log.csv` stores paths relative to the archive instead of
  container paths (`/ablage/…`), so a different mount point no longer makes `run`
  convert generated files again. The first `run` or `sort --apply` lifts an existing
  log; paths outside the current archive stay as they are and are reported. Older
  ordnig versions refuse a lifted folder (exit 2).

### Added

- `check` lists copies made by a `sort` rule (`kopie`) as intended duplicates in their
  own report section (⎘ in the tree, `gewollt = ja` in `duplicates.csv`, `copy` column
  in `files.csv`); the duplicate findings and the redundant size leave them out.
- Warning when the state folder lies on an SMB/cifs mount with `nobrl`: the lock then
  only works on this machine, a run on another machine is not prevented.

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
