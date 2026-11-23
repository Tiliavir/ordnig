# 2 Constraints

- Delivered as one Docker image (`ghcr.io/tiliavir/ordnig`, amd64 and arm64);
  nothing installed on the host.
- The container may run on the storage host (NAS) or on another machine with the
  storage mounted; the archive may span several mounts.
- Containers are short-lived and may change host and version between runs, so
  state lives with the archive, not in the container ([ADR](adr/ADR-20261117-state-in-the-inbox.md)).
- Python standard library only; external tools come as Ubuntu LTS packages
  (LibreOffice, OCRmyPDF/Tesseract, Ghostscript, img2pdf) plus veraPDF.
- English throughout: code, docs and user interface (reports, messages, rule keys); folder
  names are configuration ([ADR](adr/ADR-20261123-english-ui-configurable-folders.md)).
- `main` only through pull requests with green checks; releases from `CHANGELOG.md`.

