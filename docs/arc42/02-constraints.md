# 2 Constraints

- Runs as one Docker image (`ghcr.io/tiliavir/ordnig`, amd64 and arm64); nothing
  installed on the host.
- Started on Markus's PC, not on the NAS (see [7](07-deployment.md)); the NAS
  shares are mounted into the container.
- Everything that must survive a run lives on the NAS, in `00_Eingang/.ordnig/`
  ([ADR state in the inbox](adr/ADR-20261117-state-in-the-inbox.md)).
- Python standard library only; external tools come as Ubuntu packages
  (LibreOffice, OCRmyPDF/Tesseract, Ghostscript, img2pdf) plus veraPDF.
- Code and docs in English; reports and CLI messages for the user in German.
- `main` only through pull requests with green checks; releases from `CHANGELOG.md`.
