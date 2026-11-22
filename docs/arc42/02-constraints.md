# 2 Constraints

- Delivered as one Docker image (`ghcr.io/tiliavir/ordnig`, amd64 and arm64);
  nothing installed on the host.
- The container may run on the storage host (NAS) or on another machine with the
  storage mounted; the archive may span several mounts.
- Containers are short-lived and may change host and version between runs, so
  state lives with the archive, not in the container ([ADR](adr/ADR-20261117-state-in-the-inbox.md)).
- Python standard library only; external tools come as Ubuntu LTS packages
  (LibreOffice, OCRmyPDF/Tesseract, Ghostscript, img2pdf) plus veraPDF.
- Code and docs in English; user interface (reports, messages, rule keys) in German ([Q16](#q16)).
- `main` only through pull requests with green checks; releases from `CHANGELOG.md`.

<a id="q16"></a>
> [!IMPORTANT]
> **Q16 – Language of the user interface:** Reports, CLI messages, folder defaults (`00_Eingang`, `_Prüfen`) and the keys in
> `regeln.toml` are German. That limits the product to German speakers. Keep it that way
> for now, or plan English as the default with German as a second language?
