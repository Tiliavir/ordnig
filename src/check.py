#!/usr/bin/env python3
"""
ablage_check.py – Prüft eine (alte) Dateiablage auf Langlebigkeit und Konsistenz.

Nur Python-Standardbibliothek. Optional genutzt, falls installiert:
  - `file`  (libmagic)  -> erkennt echten Dateityp, findet falsche Endungen
  - `sf`    (siegfried) -> PRONOM-IDs, wird nur gemeldet, nicht benötigt

Aufruf:
  python3 check.py /pfad/zur/ablage            # Bericht + Manifest
  python3 check.py /pfad --out bericht_2026    # eigenes Ausgabeverzeichnis
  python3 check.py /pfad --verify alt/manifest.sha256   # Bitrot-Prüfung
  python3 check.py /pfad --no-hash             # schnell, ohne Prüfsummen

Ausgabe (im Ordner --out, Standard ./ablage_report):
  report.md          Zusammenfassung mit Befunden
  files.csv          eine Zeile pro Datei (Pfad, Größe, Datum, Typ, Risiko, Befunde)
  manifest.sha256    Prüfsummen (sha256sum-kompatibel) für spätere Fixity-Checks
  duplicates.csv     Gruppen identischer Dateien
"""
import argparse, csv, hashlib, os, shutil, subprocess, sys, time, unicodedata, zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- Formatbewertung
# ok      = offen/weit verbreitet, archivtauglich
# watch   = verbreitet, aber proprietär/komplex – beobachten, ggf. Zweitformat
# risk    = veraltet/proprietär – migrieren empfohlen
FORMATS = {
    # Text & Dokumente
    "txt": ("ok", "Text"), "md": ("ok", "Text"), "csv": ("ok", "Text"), "tsv": ("ok", "Text"),
    "xml": ("ok", "Text"), "json": ("ok", "Text"), "html": ("ok", "Text"), "htm": ("ok", "Text"),
    "rtf": ("watch", "Dokument"), "pdf": ("watch", "Dokument (PDF/A prüfen)"),
    "odt": ("ok", "Dokument"), "ods": ("ok", "Tabelle"), "odp": ("ok", "Präsentation"),
    "docx": ("watch", "Dokument"), "xlsx": ("watch", "Tabelle"), "pptx": ("watch", "Präsentation"),
    "doc": ("risk", "Word 97-2003"), "xls": ("risk", "Excel 97-2003"), "ppt": ("risk", "PowerPoint 97-2003"),
    "dot": ("risk", "Word-Vorlage alt"), "pps": ("risk", "PowerPoint-Show alt"),
    "docm": ("risk", "Word mit Makros"), "xlsm": ("risk", "Excel mit Makros"),
    "wps": ("risk", "MS Works"), "wks": ("risk", "MS Works Tabelle"), "wdb": ("risk", "MS Works DB"),
    "wri": ("risk", "Windows Write"), "wpd": ("risk", "WordPerfect"), "wp": ("risk", "WordPerfect"),
    "lwp": ("risk", "Lotus WordPro"), "123": ("risk", "Lotus 1-2-3"), "wk1": ("risk", "Lotus 1-2-3"),
    "sdw": ("risk", "StarOffice"), "sdc": ("risk", "StarOffice"), "sxw": ("risk", "OpenOffice 1.x"),
    "sxc": ("risk", "OpenOffice 1.x"), "pages": ("risk", "Apple Pages"), "numbers": ("risk", "Apple Numbers"),
    "key": ("risk", "Apple Keynote"), "pub": ("risk", "MS Publisher"), "one": ("risk", "OneNote"),
    "xps": ("risk", "XPS"), "oxps": ("risk", "XPS"), "djvu": ("watch", "DjVu"),
    "mdb": ("risk", "Access-DB"), "accdb": ("risk", "Access-DB"), "dbf": ("watch", "dBase"),
    "msg": ("risk", "Outlook-Mail"), "pst": ("risk", "Outlook-Archiv"), "ost": ("risk", "Outlook-Cache"),
    "eml": ("ok", "Mail"), "mbox": ("ok", "Mail"), "vcf": ("ok", "Kontakt"), "ics": ("ok", "Kalender"),
    "lnk": ("risk", "Windows-Verknüpfung"), "url": ("watch", "Web-Verknüpfung"),
    # Bilder
    "tif": ("ok", "Bild"), "tiff": ("ok", "Bild"), "png": ("ok", "Bild"), "jpg": ("ok", "Bild"),
    "jpeg": ("ok", "Bild"), "gif": ("ok", "Bild"), "svg": ("ok", "Vektor"), "jp2": ("ok", "Bild"),
    "webp": ("watch", "Bild"), "heic": ("watch", "Bild (HEIF)"), "heif": ("watch", "Bild (HEIF)"),
    "bmp": ("watch", "Bild unkomprimiert"), "dng": ("ok", "RAW offen"),
    "cr2": ("watch", "RAW Canon"), "cr3": ("watch", "RAW Canon"), "nef": ("watch", "RAW Nikon"),
    "arw": ("watch", "RAW Sony"), "orf": ("watch", "RAW Olympus"), "raf": ("watch", "RAW Fuji"),
    "rw2": ("watch", "RAW Panasonic"), "psd": ("watch", "Photoshop"), "xcf": ("watch", "GIMP"),
    "pcx": ("risk", "PCX"), "pct": ("risk", "Mac PICT"), "pict": ("risk", "Mac PICT"),
    "tga": ("watch", "Targa"), "wmf": ("risk", "Windows Metafile"), "emf": ("risk", "Windows Metafile"),
    "cdr": ("risk", "CorelDRAW"), "ai": ("watch", "Illustrator"), "eps": ("watch", "EPS"),
    "ico": ("watch", "Icon"), "thm": ("watch", "Kamera-Thumbnail"),
    # Audio/Video
    "wav": ("ok", "Audio"), "flac": ("ok", "Audio"), "mp3": ("ok", "Audio"), "ogg": ("ok", "Audio"),
    "opus": ("ok", "Audio"), "m4a": ("watch", "Audio"), "aac": ("watch", "Audio"), "aiff": ("ok", "Audio"),
    "wma": ("risk", "Windows Media Audio"), "ra": ("risk", "RealAudio"), "rm": ("risk", "RealMedia"),
    "mid": ("ok", "MIDI"), "midi": ("ok", "MIDI"),
    "mp4": ("watch", "Video"), "mkv": ("ok", "Video"), "mov": ("watch", "Video"), "webm": ("ok", "Video"),
    "avi": ("watch", "Video (Codec prüfen)"), "wmv": ("risk", "Windows Media Video"),
    "mpg": ("watch", "Video"), "mpeg": ("watch", "Video"), "3gp": ("risk", "Handyvideo alt"),
    "flv": ("risk", "Flash-Video"), "swf": ("risk", "Flash"), "vob": ("watch", "DVD-Video"),
    "mts": ("watch", "AVCHD"), "m2ts": ("watch", "AVCHD"),
    # Archive / Container
    "zip": ("ok", "Archiv"), "tar": ("ok", "Archiv"), "gz": ("ok", "Archiv"), "7z": ("watch", "Archiv"),
    "bz2": ("ok", "Archiv"), "xz": ("ok", "Archiv"), "rar": ("risk", "RAR proprietär"),
    "ace": ("risk", "ACE"), "arj": ("risk", "ARJ"), "lzh": ("risk", "LHA"), "lha": ("risk", "LHA"),
    "cab": ("risk", "CAB"), "sit": ("risk", "StuffIt"), "sitx": ("risk", "StuffIt"),
    "iso": ("watch", "Image"), "img": ("watch", "Image"), "dmg": ("risk", "Mac-Image"),
    "nrg": ("risk", "Nero-Image"), "bin": ("watch", "Binär/Image"), "cue": ("ok", "Cue-Sheet"),
    # Sonstiges
    "ged": ("ok", "GEDCOM"), "kml": ("ok", "Geo"), "gpx": ("ok", "Geo"), "kmz": ("ok", "Geo"),
    "fit": ("watch", "Garmin FIT"), "epub": ("ok", "E-Book"), "mobi": ("risk", "Kindle alt"),
    "azw": ("risk", "Kindle DRM"), "chm": ("risk", "Windows-Hilfe"), "hlp": ("risk", "Windows-Hilfe alt"),
}
JUNK = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db", "~$", ".~lock", "._"}
WIN_BAD = set('<>:"|?*')
WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MOJIBAKE = ("Ã¤", "Ã¶", "Ã¼", "Ã„", "Ã–", "Ãœ", "ÃŸ", "Ã©", "â€", "Â ")

# Erwartete MIME-Präfixe je Endung (für Endung-vs-Inhalt-Prüfung)
MIME_EXPECT = {
    "pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "tif": "image/tiff", "tiff": "image/tiff", "zip": "application/zip",
    "docx": ("application/vnd.openxmlformats", "application/zip"),
    "xlsx": ("application/vnd.openxmlformats", "application/zip"),
    "pptx": ("application/vnd.openxmlformats", "application/zip"),
    "odt": ("application/vnd.oasis", "application/zip"), "ods": ("application/vnd.oasis", "application/zip"),
    "doc": ("application/msword", "application/x-ole", "application/CDFV2", "application/vnd.ms"),
    "xls": ("application/vnd.ms-excel", "application/x-ole", "application/CDFV2", "application/vnd.ms"),
    "mp3": ("audio/mpeg", "audio/x-mp3"), "flac": "audio/flac", "wav": ("audio/x-wav", "audio/wav"),
    "mp4": "video/mp4", "html": "text/html", "txt": "text/", "csv": ("text/", "application/csv"),
    "rar": "application/x-rar", "7z": "application/x-7z",
}

# ---------------------------------------------------------------- Integritätsprüfungen
def tail(fp, n=1024):
    fp.seek(0, 2); size = fp.tell(); fp.seek(max(0, size - n)); return fp.read()

def check_integrity(path: Path, ext: str):
    """Leichte Strukturprüfung. Liefert Liste von Befunden."""
    issues = []
    try:
        with open(path, "rb") as f:
            head = f.read(16)
            if ext == "pdf":
                if not head.startswith(b"%PDF"):
                    issues.append("PDF-Header fehlt")
                elif b"%%EOF" not in tail(f, 2048):
                    issues.append("PDF abgeschnitten (kein %%EOF)")
                else:
                    f.seek(0); blob = f.read(200_000)
                    if b"/Encrypt" in blob: issues.append("PDF verschlüsselt")
                    if b"pdfaid" in blob or b"PDF/A" in blob: issues.append("info:PDF/A-Kennung vorhanden")
            elif ext in ("jpg", "jpeg"):
                if not head.startswith(b"\xff\xd8"): issues.append("JPEG-Header fehlt")
                elif b"\xff\xd9" not in tail(f, 64): issues.append("JPEG abgeschnitten (kein EOI)")
            elif ext == "png":
                if not head.startswith(b"\x89PNG\r\n\x1a\n"): issues.append("PNG-Header fehlt")
                elif b"IEND" not in tail(f, 32): issues.append("PNG abgeschnitten (kein IEND)")
            elif ext in ("gif",):
                if not head[:3] == b"GIF": issues.append("GIF-Header fehlt")
            elif ext in ("tif", "tiff"):
                if head[:4] not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"): issues.append("TIFF-Header fehlt")
    except OSError as e:
        issues.append(f"Lesefehler: {e.strerror}")
        return issues
    if ext in ("zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "kmz", "jar"):
        try:
            with zipfile.ZipFile(path) as z:
                if any(i.flag_bits & 0x1 for i in z.infolist()):
                    issues.append("ZIP verschlüsselt")
                else:
                    bad = z.testzip()
                    if bad: issues.append(f"ZIP defekt bei {bad}")
        except zipfile.BadZipFile:
            issues.append("ZIP-Struktur defekt")
        except (OSError, NotImplementedError, RuntimeError) as e:
            issues.append(f"ZIP nicht prüfbar: {e}")
    return issues

# ---------------------------------------------------------------- Namensprüfungen
def check_name(rel: str, name: str, raw_bytes: bytes):
    issues = []
    try:
        raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        issues.append("Dateiname kein gültiges UTF-8 (alte Codepage?)")
    if any(m in name for m in MOJIBAKE):
        issues.append("Mojibake im Namen (z. B. Ã¤ statt ä)")
    if unicodedata.normalize("NFC", name) != name:
        issues.append("Unicode NFD (Mac-Schreibweise) – Duplikat-/Sync-Gefahr")
    if set(name) & WIN_BAD:
        issues.append("Zeichen ungültig unter Windows (<>:\"?* oder Pipe)")
    if name != name.rstrip(" ."):
        issues.append("Endet mit Leerzeichen/Punkt")
    if name != name.lstrip(" "):
        issues.append("Beginnt mit Leerzeichen")
    if name.split(".")[0].upper() in WIN_RESERVED:
        issues.append("Reservierter Windows-Name")
    if any(ord(c) < 32 for c in name):
        issues.append("Steuerzeichen im Namen")
    if len(rel) > 240:
        issues.append(f"Pfad sehr lang ({len(rel)} Zeichen, Windows-Limit 260)")
    if len(name.encode("utf-8", "surrogateescape")) > 255:
        issues.append("Name > 255 Byte")
    return issues

# ---------------------------------------------------------------- MIME per libmagic
def detect_mimes(paths):
    if not shutil.which("file") or not paths:
        return {}
    out = {}
    CH = 2000
    for i in range(0, len(paths), CH):
        chunk = paths[i:i + CH]
        try:
            r = subprocess.run(["file", "-b", "--mime-type", "--", *map(str, chunk)],
                               capture_output=True, text=True, errors="replace", timeout=600)
            for p, m in zip(chunk, r.stdout.splitlines()):
                out[p] = m.strip()
        except (subprocess.SubprocessError, OSError):
            pass
    return out

def mime_mismatch(ext, mime):
    exp = MIME_EXPECT.get(ext)
    if not exp or not mime or mime in ("application/octet-stream", "inode/x-empty"):
        return False
    exp = (exp,) if isinstance(exp, str) else exp
    return not any(mime.startswith(e) for e in exp)

def sha256(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(bufsize):
            h.update(chunk)
    return h.hexdigest()

# ---------------------------------------------------------------- Hauptlauf
def scan(root: Path, args):
    files, empty_dirs, dir_errors = [], [], []
    case_map = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: dir_errors.append(str(e))):
        dirnames[:] = [d for d in dirnames if not d.startswith("@eaDir") and d not in ("#recycle", ".archive-validator")]  # Synology, eigene Daten
        if not dirnames and not filenames:
            empty_dirs.append(os.path.relpath(dirpath, root))
        lower = Counter(n.lower() for n in filenames + dirnames)
        for n, c in lower.items():
            if c > 1:
                case_map[os.path.relpath(dirpath, root)].append(n)
        for n in filenames:
            files.append(Path(dirpath) / n)
    return files, empty_dirs, dir_errors, case_map

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--out", type=Path, default=Path("ablage_report"))
    ap.add_argument("--no-hash", action="store_true", help="keine Prüfsummen/Duplikate (schneller)")
    ap.add_argument("--no-integrity", action="store_true", help="keine Strukturprüfung")
    ap.add_argument("--verify", type=Path, help="früheres manifest.sha256 gegen Bestand prüfen")
    args = ap.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        sys.exit(f"Kein Verzeichnis: {root}")
    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print(f"Scanne {root} …", file=sys.stderr)
    paths, empty_dirs, dir_errors, case_map = scan(root, args)
    print(f"{len(paths)} Dateien gefunden, analysiere …", file=sys.stderr)
    mimes = detect_mimes(paths)

    rows, hashes = [], {}
    by_size = defaultdict(list)
    now = time.time()
    for i, p in enumerate(paths, 1):
        if i % 5000 == 0:
            print(f"  {i}/{len(paths)}", file=sys.stderr)
        rel = str(p.relative_to(root))
        name = p.name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name.lstrip(".") else ""
        issues = check_name(rel, name, os.fsencode(name))
        try:
            st = p.lstat()
        except OSError as e:
            rows.append(dict(path=rel, size=0, mtime="", ext=ext, mime="", risk="?", kind="", issues=f"stat: {e}"))
            continue
        if p.is_symlink():
            issues.append("Symlink" + (" (kaputt)" if not p.exists() else ""))
            rows.append(dict(path=rel, size=0, mtime="", ext=ext, mime="", risk="?", kind="Symlink", issues="; ".join(issues)))
            continue
        size = st.st_size
        mt = st.st_mtime
        if size == 0: issues.append("Leere Datei (0 Byte)")
        if mt > now + 86400: issues.append("Änderungsdatum in der Zukunft")
        if mt < 315532800: issues.append("Änderungsdatum vor 1980 (verdächtig)")
        low = name.lower()
        if low in JUNK or any(low.startswith(j) for j in ("~$", ".~lock", "._")):
            issues.append("Systemmüll/Temp-Datei")
        if low.endswith((".tmp", ".bak", ".old", "~")):
            issues.append("Temp/Backup-Datei")
        if not ext: issues.append("Keine Dateiendung")
        risk, kind = FORMATS.get(ext, ("unknown", ""))
        mime = mimes.get(p, "")
        if mime_mismatch(ext, mime):
            issues.append(f"Endung passt nicht zum Inhalt ({mime})")
        if not args.no_integrity and size > 0:
            for iss in check_integrity(p, ext):
                if iss.startswith("info:"): kind += " · " + iss[5:]
                else: issues.append(iss)
        if not args.no_hash and size > 0:
            by_size[size].append(p)
        rows.append(dict(path=rel, size=size, mtime=datetime.fromtimestamp(mt).strftime("%Y-%m-%d"),
                         ext=ext, mime=mime, risk=risk, kind=kind, issues="; ".join(issues), _p=p))

    # Prüfsummen (alle Dateien, fürs Manifest) – Duplikate daraus
    dup_groups = []
    if not args.no_hash:
        print("Berechne Prüfsummen …", file=sys.stderr)
        for r in rows:
            p = r.get("_p")
            if p is None or r["size"] == 0: continue
            try: hashes[p] = sha256(p)
            except OSError as e: r["issues"] += f"; Lesefehler beim Hashen: {e.strerror}"
        groups = defaultdict(list)
        for p, h in hashes.items(): groups[h].append(p)
        dup_groups = sorted((g for g in groups.values() if len(g) > 1),
                            key=lambda g: -g[0].stat().st_size * (len(g) - 1))
        with open(args.out / "manifest.sha256", "w", encoding="utf-8", errors="surrogateescape") as m:
            for r in rows:
                p = r.get("_p")
                if p in hashes: m.write(f"{hashes[p]}  {r['path']}\n")
        with open(args.out / "duplicates.csv", "w", newline="", encoding="utf-8", errors="surrogateescape") as f:
            w = csv.writer(f); w.writerow(["gruppe", "sha256", "groesse", "pfad"])
            for gi, g in enumerate(dup_groups, 1):
                for p in g: w.writerow([gi, hashes[p], p.stat().st_size, str(p.relative_to(root))])

    # Fixity-Verify
    verify_lines = []
    if args.verify:
        print("Vergleiche mit altem Manifest …", file=sys.stderr)
        cur = {r["path"]: hashes.get(r.get("_p")) for r in rows}
        old = {}
        for line in open(args.verify, encoding="utf-8", errors="surrogateescape"):
            if "  " in line:
                h, pth = line.rstrip("\n").split("  ", 1); old[pth] = h
        changed = [p for p, h in old.items() if p in cur and cur[p] and cur[p] != h]
        missing = [p for p in old if p not in cur]
        new = [p for p in cur if p not in old and cur[p]]
        oldh = defaultdict(list)
        for p, h in old.items(): oldh[h].append(p)
        moved = [(oldh[cur[p]][0], p) for p in new if cur[p] in oldh and oldh[cur[p]][0] in missing]
        verify_lines = [f"- Geändert (Inhalt anders, gleicher Pfad – **Bitrot oder bewusst editiert?**): {len(changed)}",
                        f"- Fehlend: {len(missing)} (davon verschoben/umbenannt erkannt: {len(moved)})",
                        f"- Neu: {len(new)}"]
        for p in changed[:50]: verify_lines.append(f"  - geändert: `{p}`")
        for p in [m for m in missing if m not in {a for a, _ in moved}][:50]: verify_lines.append(f"  - fehlt: `{p}`")

    # CSV
    with open(args.out / "files.csv", "w", newline="", encoding="utf-8", errors="surrogateescape") as f:
        w = csv.DictWriter(f, fieldnames=["path", "size", "mtime", "ext", "mime", "risk", "kind", "issues"], extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

    # Bericht
    total = sum(r["size"] for r in rows)
    risk_cnt, risk_size = Counter(), Counter()
    ext_cnt, ext_size = Counter(), Counter()
    issue_cnt = Counter()
    years = Counter()
    for r in rows:
        risk_cnt[r["risk"]] += 1; risk_size[r["risk"]] += r["size"]
        ext_cnt[r["ext"] or "(keine)"] += 1; ext_size[r["ext"] or "(keine)"] += r["size"]
        if r["mtime"]: years[r["mtime"][:4]] += 1
        for iss in filter(None, (s.strip() for s in r["issues"].split(";"))):
            key = iss.split("(")[0].strip() if "Endung passt" in iss or "Pfad sehr lang" in iss else iss
            key = "ZIP defekt" if key.startswith("ZIP defekt bei") else key
            issue_cnt[key] += 1

    def hs(n):
        for u in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024: return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
            n /= 1024
        return f"{n:.1f} PB"

    L = [f"# Ablage-Prüfung: `{root}`", "",
         f"Stand {datetime.now():%Y-%m-%d %H:%M} · {len(rows):,} Dateien · {hs(total)} · Laufzeit {time.time()-t0:.0f}s".replace(",", "."), ""]
    if not shutil.which("file"):
        L += ["> Hinweis: `file` nicht gefunden – Endung-vs-Inhalt-Prüfung übersprungen.", ""]
    L += ["## Langlebigkeit der Formate", "", "| Bewertung | Dateien | Größe |", "|---|---:|---:|"]
    labels = {"ok": "✅ archivtauglich", "watch": "🟡 beobachten", "risk": "🔴 migrieren", "unknown": "⚪ unbekannt", "?": "❓ nicht lesbar"}
    for k in ("ok", "watch", "risk", "unknown", "?"):
        if risk_cnt[k]: L.append(f"| {labels[k]} | {risk_cnt[k]} | {hs(risk_size[k])} |")
    risky = Counter({e: c for e, c in ext_cnt.items() if FORMATS.get(e, ("",))[0] == "risk"})
    if risky:
        L += ["", "**Migrationskandidaten** (Format → Vorschlag):", ""]
        sugg = {"doc": "ODT/DOCX + PDF/A", "xls": "ODS/XLSX (+CSV)", "ppt": "ODP/PPTX + PDF/A",
                "wps": "ODT + PDF/A", "wri": "ODT/TXT", "wpd": "ODT + PDF/A", "lwp": "ODT + PDF/A",
                "sxw": "ODT", "sxc": "ODS", "sdw": "ODT", "sdc": "ODS", "mdb": "SQLite + CSV",
                "accdb": "SQLite + CSV", "wma": "FLAC (verlustfrei aus Quelle) / Opus", "wmv": "MKV/MP4 (H.264)",
                "rm": "MKV/MP4", "ra": "FLAC/Opus", "flv": "MP4", "3gp": "MP4", "rar": "auspacken → ZIP/TAR",
                "ace": "auspacken → ZIP/TAR", "arj": "auspacken → ZIP/TAR", "lzh": "auspacken → ZIP/TAR",
                "sit": "auspacken → ZIP/TAR", "cab": "auspacken", "msg": "EML", "pst": "MBOX/EML",
                "pct": "PNG/TIFF", "pcx": "PNG", "wmf": "SVG/PNG", "emf": "SVG/PNG", "cdr": "SVG + PDF",
                "xps": "PDF/A", "pages": "ODT/PDF", "numbers": "ODS/CSV", "key": "PDF", "pub": "PDF/A",
                "mobi": "EPUB", "chm": "HTML/PDF", "hlp": "HTML/PDF", "dmg": "auspacken", "nrg": "ISO",
                "docm": "DOCX/ODT (Makros prüfen)", "xlsm": "XLSX/ODS (Makros prüfen)", "lnk": "löschen oder als Text dokumentieren"}
        for e, c in risky.most_common():
            L.append(f"- `.{e}` – {FORMATS[e][1]}: {c} Dateien, {hs(ext_size[e])} → {sugg.get(e, 'prüfen')}")
    unk = Counter({e: c for e, c in ext_cnt.items() if e not in FORMATS})
    if unk:
        L += ["", "**Unbekannte Endungen** (Top 20, bitte einordnen):", "",
              ", ".join(f"`.{e}` ({c})" for e, c in unk.most_common(20))]

    L += ["", "## Top-Formate", "", "| Endung | Dateien | Größe |", "|---|---:|---:|"]
    for e, c in ext_cnt.most_common(15):
        L.append(f"| {e} | {c} | {hs(ext_size[e])} |")

    L += ["", "## Konsistenz & Integrität", ""]
    if issue_cnt:
        L += ["| Befund | Anzahl |", "|---|---:|"]
        for k, c in issue_cnt.most_common(): L.append(f"| {k} | {c} |")
        L += ["", "Details pro Datei: Spalte `issues` in `files.csv` (z. B. in LibreOffice filtern)."]
    else:
        L.append("Keine Auffälligkeiten.")
    if case_map:
        L += ["", f"**Namen, die sich nur in Groß/Kleinschreibung unterscheiden** ({sum(map(len, case_map.values()))}) – "
              "kollidieren unter Windows/macOS:", ""]
        for d, ns in list(case_map.items())[:20]: L.append(f"- `{d}/`: {', '.join(ns)}")
    if empty_dirs:
        L += ["", f"**Leere Ordner:** {len(empty_dirs)}", ""] + [f"- `{d}`" for d in empty_dirs[:20]]
        if len(empty_dirs) > 20: L.append(f"- … und {len(empty_dirs)-20} weitere")
    if dir_errors:
        L += ["", f"**Nicht lesbare Ordner:** {len(dir_errors)}", ""] + [f"- {e}" for e in dir_errors[:20]]

    if dup_groups:
        waste = sum(g[0].stat().st_size * (len(g) - 1) for g in dup_groups)
        L += ["", "## Duplikate", "",
              f"{len(dup_groups)} Gruppen identischer Dateien, {hs(waste)} redundant. Größte:", ""]
        for g in dup_groups[:15]:
            L.append(f"- {hs(g[0].stat().st_size)} × {len(g)}: " + " | ".join(f"`{p.relative_to(root)}`" for p in g[:4]))
        L.append("\nAlle Gruppen: `duplicates.csv`.")

    if years:
        L += ["", "## Dateien nach Änderungsjahr", "", "```"]
        mx = max(years.values())
        for y in sorted(years): L.append(f"{y} {'█' * max(1, round(40 * years[y] / mx))} {years[y]}")
        L.append("```")

    if verify_lines:
        L += ["", "## Fixity-Vergleich mit " + f"`{args.verify}`", ""] + verify_lines

    if not args.no_hash:
        L += ["", "## Nächster Schritt: Fixity", "",
              "`manifest.sha256` an einem **zweiten Ort** aufbewahren und regelmäßig prüfen:", "",
              f"```\npython3 check.py {root} --verify {args.out}/manifest.sha256 --out report_neu\n"
              f"# oder direkt:  cd {root} && sha256sum -c --quiet /pfad/manifest.sha256\n```"]

    (args.out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8", errors="replace")
    print(f"Fertig: {args.out/'report.md'}", file=sys.stderr)

if __name__ == "__main__":
    main()
