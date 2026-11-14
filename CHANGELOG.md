# Changelog

All notable changes. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions: [Semantic Versioning](https://semver.org/). Releasing is described in
[CONTRIBUTING.md](CONTRIBUTING.md#releasing).

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
