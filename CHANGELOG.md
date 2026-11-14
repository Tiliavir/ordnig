# Changelog

All notable changes. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions: [Semantic Versioning](https://semver.org/). Releasing is described in
[CONTRIBUTING.md](CONTRIBUTING.md#releasing).

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
