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
  duplicates.csv     Gruppen identischer Dateien (gewollt = Regel-Kopien von sort)
"""
import argparse, csv, re, hashlib, os, shutil, subprocess, sys, time, unicodedata, zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import importlib.util
_spec = importlib.util.spec_from_file_location("state", Path(__file__).resolve().parent / "state.py")
state = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(state)

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
                    m = re.search(rb'pdfaid:part(?:="|>)(\d)', blob)
                    if m:
                        c = re.search(rb'pdfaid:conformance(?:="|>)([ABUabu])', blob)
                        issues.append(f"info:PDF/A-{m.group(1).decode()}{c.group(1).decode().lower() if c else ''}")
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

# ---------------------------------------------------------------- Regel-Kopien
def rule_copies(inbox):
    """Inhalte, die sort per Regel (kopie) mehrfach abgelegt hat: SHA-256 -> Anzahl der Exemplare
    {nach, kopien…}. Am Inhalt erkannt, damit Umbenennen oder Verschieben nichts ändert."""
    log = inbox / ".ordnig" / "protokoll.csv" if inbox else None
    if not log or not log.is_file():
        return {}
    counts = {}
    with open(log, encoding="utf-8", newline="", errors="surrogateescape") as f:
        for r in csv.DictReader(f):
            if r.get("status") != "abgelegt" or not r.get("kopien") or not r.get("sha256"):
                continue
            n = 1 + sum(1 for k in r["kopien"].split(" | ") if k)
            counts[r["sha256"]] = max(counts.get(r["sha256"], 0), n)
    return counts


def intended(digest, group, copies):
    """Gewollt, wenn eine Regel diesen Inhalt mindestens so oft abgelegt hat; ein weiteres Exemplar
    macht die Gruppe wieder zum Befund."""
    return len(group) <= copies.get(digest, 0)


# ---------------------------------------------------------------- Scan
def in_skip(p: Path, skip):
    return any(p == s or s in p.parents for s in skip)

def scan(root: Path, args, skip=()):
    """skip: absolute Ordner, die nicht geprüft werden (der Eingang: noch nicht abgelegte Post)."""
    skip = {Path(s) for s in skip}
    files, empty_dirs, dir_errors = [], [], []
    case_map = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: dir_errors.append(str(e))):
        dirnames[:] = [d for d in dirnames if d not in state.SYSTEM_DIRS
                       and Path(dirpath) / d not in skip]
        if not dirnames and not filenames:
            empty_dirs.append(os.path.relpath(dirpath, root))
        lower = Counter(n.lower() for n in filenames + dirnames)
        for n, c in lower.items():
            if c > 1:
                case_map[os.path.relpath(dirpath, root)].append(n)
        for n in filenames:
            files.append(Path(dirpath) / n)
    return files, empty_dirs, dir_errors, case_map

# ---------------------------------------------------------------- Ausgabe
def hs(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024: return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

def num(n):
    return f"{n:,}".replace(",", ".")

def progress(msg):
    """Fortschritt nur im Terminal, eine Zeile, wird überschrieben."""
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[K" + msg[:100]); sys.stderr.flush()

def progress_done():
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[K"); sys.stderr.flush()

def console_table(title, rows, footer=()):
    """rows: (label, value, value2) – None = Trennlinie."""
    data = [r for r in rows if r is not None]
    w = [max(len(str(r[i])) for r in data + [("", "", "")]) for i in range(3)]
    w[0] = max(w[0], len(title))
    line = lambda l, m, r: l + m.join("─" * (x + 2) for x in w) + r
    out = [line("┌", "┬", "┐"), f"│ {title:<{w[0]}} │ {'':>{w[1]}} │ {'':>{w[2]}} │", line("├", "┼", "┤")]
    for r in rows:
        out.append(line("├", "┼", "┤") if r is None else f"│ {r[0]:<{w[0]}} │ {str(r[1]):>{w[1]}} │ {str(r[2]):>{w[2]}} │")
    out.append(line("└", "┴", "┘"))
    print("\n".join(out + list(footer)))

RISK_MARK = {"watch": "🟡", "risk": "🔴", "unknown": "⚪", "?": "❓", "done": "☑"}

def render_tree(root_name, rows, empty_dirs, case_map, dir_errors):
    """Baum aller Dateien; Ordnerzeilen mit Summen, Dateizeilen mit allen Befunden."""
    tree = {}
    def node(parts):
        n = tree
        for part in parts:
            n = n.setdefault(part + "/", {})
        return n
    for d in empty_dirs:
        if d != ".": node(Path(d).parts)
    for r in rows:
        parts = Path(r["path"]).parts
        node(parts[:-1])[parts[-1]] = r

    def agg(n):
        a = Counter()
        for k, v in n.items():
            if k.endswith("/") and isinstance(v, dict):
                a.update(agg(v))
            else:
                a["files"] += 1; a["size"] += v["size"]
                if v["risk"] == "risk": a["risk"] += 1
                if v["issues"]: a["issues"] += 1
        n["\0agg"] = a
        return a
    agg(tree)

    def dir_note(n, rel):
        a = n["\0agg"]
        bits = [f"{num(a['files'])} {'Datei' if a['files'] == 1 else 'Dateien'}", hs(a["size"])] if a["files"] else ["leer"]
        if a["risk"]: bits.append(f"🔴 {num(a['risk'])}")
        if a["issues"]: bits.append(f"⚠ {num(a['issues'])}")
        if case_map.get(rel): bits.append("⚠ Namenskollision: " + ", ".join(case_map[rel]))
        return " · ".join(bits)

    def file_note(r):
        bits = [hs(r["size"]), r["mtime"]] if r["mtime"] else [hs(r["size"])]
        if r["risk"] in RISK_MARK:
            bits.append(f"{RISK_MARK[r['risk']]} {r['kind'] or '.' + r['ext'] or 'ohne Endung'}"
                        + (f" → {SUGGEST[r['ext']]}" if r["ext"] in SUGGEST and r["risk"] != "done" else ""))
        elif r["kind"].startswith("PDF/A"):
            bits.append(f"✅ {r['kind']}")
        bits += [f"⚠ {i.strip()}" for i in r["issues"].split(";") if i.strip()]
        if r.get("dup"): bits.append(f"⧉ {r['dup']}")
        if r.get("copy"): bits.append(f"⎘ {r['copy']}")
        if r.get("info"): bits.append(f"ℹ {r['info']}")
        return " · ".join(b for b in bits if b)

    out = [f"{root_name}/  —  {dir_note(tree, '.')}"]
    def walk(n, prefix, rel):
        keys = sorted((k for k in n if k != "\0agg"), key=lambda k: (not k.endswith("/"), k.lower()))
        for i, k in enumerate(keys):
            last = i == len(keys) - 1
            branch, cont = ("└── ", "    ") if last else ("├── ", "│   ")
            v = n[k]
            if k.endswith("/") and isinstance(v, dict):
                sub = os.path.join(rel, k[:-1]) if rel != "." else k[:-1]
                out.append(f"{prefix}{branch}{k}  —  {dir_note(v, sub)}")
                walk(v, prefix + cont, sub)
            else:
                out.append(f"{prefix}{branch}{k}  {file_note(v)}")
    walk(tree, "", ".")
    for e in dir_errors:
        out.append(f"⚠ nicht lesbar: {e}")
    return out

SUGGEST = {"doc": "ODT + PDF/A", "xls": "ODS", "ppt": "ODP + PDF/A",
           "wps": "ODT + PDF/A", "wri": "ODT", "wpd": "ODT + PDF/A", "lwp": "ODT + PDF/A",
           "sxw": "ODT", "sxc": "ODS", "sdw": "ODT", "sdc": "ODS", "mdb": "SQLite + CSV",
           "accdb": "SQLite + CSV", "wma": "FLAC/Opus", "wmv": "MKV/MP4", "rm": "MKV/MP4",
           "ra": "FLAC/Opus", "flv": "MP4", "3gp": "MP4", "rar": "auspacken → ZIP/TAR",
           "ace": "auspacken → ZIP/TAR", "arj": "auspacken → ZIP/TAR", "lzh": "auspacken → ZIP/TAR",
           "sit": "auspacken → ZIP/TAR", "cab": "auspacken", "msg": "EML", "pst": "MBOX/EML",
           "pct": "PNG/TIFF", "pcx": "PNG", "wmf": "SVG/PNG", "emf": "SVG/PNG", "cdr": "SVG + PDF",
           "xps": "PDF/A", "pages": "ODT/PDF", "numbers": "ODS/CSV", "key": "PDF", "pub": "PDF/A",
           "mobi": "EPUB", "chm": "HTML/PDF", "hlp": "HTML/PDF", "dmg": "auspacken", "nrg": "ISO",
           "docm": "ODT (Makros prüfen)", "xlsm": "ODS (Makros prüfen)", "lnk": "löschen/dokumentieren"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--out", type=Path, default=Path("ablage_report"))
    ap.add_argument("--no-hash", action="store_true", help="keine Prüfsummen/Duplikate (schneller)")
    ap.add_argument("--no-integrity", action="store_true", help="keine Strukturprüfung")
    ap.add_argument("--verify", type=Path, help="früheres manifest.sha256 gegen Bestand prüfen")
    ap.add_argument("--quiet", action="store_true", help="keine Tabelle auf der Konsole")
    ap.add_argument("--title", default="Prüfung", help="Titel der Konsolentabelle")
    ap.add_argument("--eingang", type=Path, default=None, help="Eingangsordner mit .ordnig/ (Protokoll von run)")
    args = ap.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        sys.exit(f"Kein Verzeichnis: {root}")
    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    progress(f"Scanne {root} …")
    inbox = state.find_inbox(root, args.eingang)
    skip = [inbox] if inbox and inbox != root else []
    paths, empty_dirs, dir_errors, case_map = scan(root, args, skip)
    progress(f"{num(len(paths))} Dateien, erkenne Typen …")
    mimes = detect_mimes(paths)

    # Bereits migrierte Originale (aus dem Protokoll von migrate.py)
    migrated = defaultdict(list)
    log = state.state_dir(root, args.eingang) / "migration_log.csv"
    for log in [p for p in (state.legacy(root, log.parent, log.name), log) if p and p.exists()]:
        for r in csv.DictReader(open(log, encoding="utf-8", errors="surrogateescape")):
            ziel = state.to_abs(r["ziel"], root)
            if r["status"] == "ok" and ziel and Path(ziel).exists():
                migrated[os.path.relpath(state.to_abs(r["quelle"], root), root)].append(Path(ziel).name)

    rows, hashes = [], {}
    now = time.time()
    for i, p in enumerate(paths, 1):
        if i % 500 == 0:
            progress(f"Analysiere {num(i)}/{num(len(paths))}")
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
        if rel in migrated and risk in ("risk", "watch", "unknown"):
            risk, kind = "done", f"{kind or '.' + ext} → {', '.join(migrated[rel])}"
        mime = mimes.get(p, "")
        if mime_mismatch(ext, mime):
            issues.append(f"Endung passt nicht zum Inhalt ({mime})")
        if not args.no_integrity and size > 0:
            for iss in check_integrity(p, ext):
                if iss.startswith("info:PDF/A"):
                    kind = iss[5:]
                    if risk == "watch": risk = "ok"
                elif iss.startswith("info:"): kind += " · " + iss[5:]
                else: issues.append(iss)
        rows.append(dict(path=rel, size=size, mtime=datetime.fromtimestamp(mt).strftime("%Y-%m-%d"),
                         ext=ext, mime=mime, risk=risk, kind=kind, issues="; ".join(issues), _p=p))

    # Prüfsummen (alle Dateien, fürs Manifest) – Duplikate daraus
    dup_groups, copy_groups = [], []
    if not args.no_hash:
        todo = [r for r in rows if r.get("_p") is not None and r["size"] > 0]
        for i, r in enumerate(todo, 1):
            if i % 200 == 0: progress(f"Prüfsummen {num(i)}/{num(len(todo))}")
            try: hashes[r["_p"]] = sha256(r["_p"])
            except OSError as e: r["issues"] += f"; Lesefehler beim Hashen: {e.strerror}"
        groups = defaultdict(list)
        for p, h in hashes.items(): groups[h].append(p)
        dup_groups = sorted((g for g in groups.values() if len(g) > 1),
                            key=lambda g: -g[0].stat().st_size * (len(g) - 1))
        copies = rule_copies(inbox)
        copy_groups = [g for g in dup_groups if intended(hashes[g[0]], g, copies)]
        dup_groups = [g for g in dup_groups if not intended(hashes[g[0]], g, copies)]
        by_path = {r.get("_p"): r for r in rows}
        for g in copy_groups:
            for p in g:
                by_path[p]["copy"] = "Regel-Kopie: " + ", ".join(str(q.relative_to(root)) for q in g if q != p)
        for gi, g in enumerate(dup_groups, 1):
            for p in g:
                others = [str(q.relative_to(root)) for q in g if q != p]
                by_path[p]["dup"] = f"Duplikat (Gruppe {gi}): " + ", ".join(others[:3]) + (f" +{len(others)-3}" if len(others) > 3 else "")
        with open(args.out / "manifest.sha256", "w", encoding="utf-8", errors="surrogateescape") as m:
            for r in rows:
                p = r.get("_p")
                if p in hashes: m.write(f"{hashes[p]}  {r['path']}\n")
        with open(args.out / "duplicates.csv", "w", newline="", encoding="utf-8", errors="surrogateescape") as f:
            w = csv.writer(f); w.writerow(["gruppe", "sha256", "groesse", "pfad", "gewollt"])
            for gi, g in enumerate(dup_groups + copy_groups, 1):
                for p in g: w.writerow([gi, hashes[p], p.stat().st_size, str(p.relative_to(root)),
                                        "ja" if gi > len(dup_groups) else ""])

    # Fixity-Verify
    vstat = None
    if args.verify:
        progress("Vergleiche mit altem Manifest …")
        by_rel = {r["path"]: r for r in rows}
        cur = {r["path"]: hashes.get(r.get("_p")) for r in rows}
        old = {}
        for line in open(args.verify, encoding="utf-8", errors="surrogateescape"):
            if "  " in line:
                h, pth = line.rstrip("\n").split("  ", 1)
                if not in_skip(root / pth, skip):  # ältere Manifeste enthalten noch den Eingang
                    old[pth] = h
        changed = [p for p, h in old.items() if p in cur and cur[p] and cur[p] != h]
        missing = [p for p in old if p not in cur]
        new = [p for p in cur if p not in old and cur[p]]
        oldh = defaultdict(list)
        for p, h in old.items(): oldh[h].append(p)
        moved = {p: oldh[cur[p]][0] for p in new if cur[p] in oldh and oldh[cur[p]][0] in missing}
        for p in changed:
            by_rel[p]["issues"] = "; ".join(filter(None, [by_rel[p]["issues"], "Inhalt geändert seit letzter Prüfung (Bitrot oder bearbeitet?)"]))
        for p in new:
            by_rel[p]["info"] = f"verschoben von {moved[p]}" if p in moved else "neu seit letzter Prüfung"
        gone = [p for p in missing if p not in set(moved.values())]
        vstat = dict(changed=len(changed), gone=gone, moved=len(moved), new=len(new) - len(moved))
    progress_done()

    # CSV
    with open(args.out / "files.csv", "w", newline="", encoding="utf-8", errors="surrogateescape") as f:
        w = csv.DictWriter(f, fieldnames=["path", "size", "mtime", "ext", "mime", "risk", "kind", "issues", "dup", "info", "copy"],
                           extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

    # Kennzahlen
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
    with_issues = sum(1 for r in rows if r["issues"])
    waste = sum(g[0].stat().st_size * (len(g) - 1) for g in dup_groups)
    n_case = sum(map(len, case_map.values()))
    labels = {"ok": "✅ archivtauglich", "done": "☑ migriert (Kopie daneben)", "watch": "🟡 beobachten",
              "risk": "🔴 migrieren", "unknown": "⚪ unbekannt", "?": "❓ nicht lesbar"}

    # Bericht: Baum, dann Zusammenfassung
    L = [f"# Ablage-Prüfung: `{root}`", "",
         f"Stand {datetime.now():%Y-%m-%d %H:%M} · {num(len(rows))} Dateien · {hs(total)} · Laufzeit {time.time()-t0:.0f}s", "",
         "Legende: 🔴 migrieren · ☑ migriert · 🟡 beobachten · ⚪ unbekanntes Format · ⚠ Befund · ⧉ Duplikat · ⎘ gewollte Regel-Kopie · ℹ Hinweis. "
         "Ordnerzeilen: Summen inkl. Unterordner. Zusammenfassung am Ende.", ""]
    if not shutil.which("file"):
        L += ["> Hinweis: `file` nicht gefunden – Endung-vs-Inhalt-Prüfung übersprungen.", ""]
    L += ["## Baum", "", "```text"] + render_tree(root.name or str(root), rows, empty_dirs, case_map, dir_errors) + ["```", ""]

    L += ["## Zusammenfassung", "", "### Langlebigkeit der Formate", "", "| Bewertung | Dateien | Größe |", "|---|---:|---:|"]
    for k in ("ok", "done", "watch", "risk", "unknown", "?"):
        if risk_cnt[k]: L.append(f"| {labels[k]} | {num(risk_cnt[k])} | {hs(risk_size[k])} |")
    risky = Counter({e: c for e, c in ext_cnt.items() if FORMATS.get(e, ("",))[0] == "risk"})
    if risky:
        L += ["", "**Migrationskandidaten** (Format → Vorschlag):", ""]
        for e, c in risky.most_common():
            L.append(f"- `.{e}` – {FORMATS[e][1]}: {num(c)} Dateien, {hs(ext_size[e])} → {SUGGEST.get(e, 'prüfen')}")
    unk = Counter({e: c for e, c in ext_cnt.items() if e not in FORMATS})
    if unk:
        L += ["", "**Unbekannte Endungen** (Top 20, bitte einordnen):", "",
              ", ".join(f"`.{e}` ({c})" for e, c in unk.most_common(20))]
    L += ["", "### Top-Formate", "", "| Endung | Dateien | Größe |", "|---|---:|---:|"]
    for e, c in ext_cnt.most_common(15):
        L.append(f"| {e} | {num(c)} | {hs(ext_size[e])} |")
    L += ["", "### Konsistenz & Integrität", ""]
    if issue_cnt:
        L += ["| Befund | Anzahl |", "|---|---:|"] + [f"| {k} | {num(c)} |" for k, c in issue_cnt.most_common()]
    else:
        L.append("Keine Auffälligkeiten.")
    extra = [("Namenskollisionen (Groß/Klein)", n_case), ("Leere Ordner", len(empty_dirs)), ("Nicht lesbare Ordner", len(dir_errors))]
    L += [""] + [f"- {k}: {v}" for k, v in extra if v]
    if dup_groups:
        L += ["", "### Duplikate", "", f"{num(len(dup_groups))} Gruppen, {hs(waste)} redundant. "
              "Im Baum mit ⧉ markiert; alle Gruppen in `duplicates.csv`."]
    if copy_groups:
        L += ["", "### Gewollte Duplikate", "", f"{num(len(copy_groups))} Gruppen, die `sort` per Regel (`kopie`) "
              "mehrfach abgelegt hat. Im Baum mit ⎘ markiert, in `duplicates.csv` mit `gewollt = ja`.", ""]
        L += ["- " + " = ".join(f"`{q}`" for q in sorted(str(q.relative_to(root)) for q in g)) for g in copy_groups[:200]]
        if len(copy_groups) > 200: L.append(f"- … {num(len(copy_groups) - 200)} weitere")
    if years:
        L += ["", "### Dateien nach Änderungsjahr", "", "```text"]
        mx = max(years.values())
        L += [f"{y} {'█' * max(1, round(40 * years[y] / mx))} {years[y]}" for y in sorted(years)] + ["```"]
    if vstat:
        L += ["", f"### Vergleich mit `{args.verify}`", "",
              f"- Inhalt geändert: {vstat['changed']} (im Baum mit ⚠ markiert)",
              f"- Verschoben/umbenannt: {vstat['moved']} · neu: {vstat['new']} (ℹ im Baum)",
              f"- Fehlt: {len(vstat['gone'])}"] + [f"  - `{p}`" for p in vstat["gone"][:200]]
    (args.out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8", errors="replace")

    code = state.FEHLER if vstat and (vstat["changed"] or vstat["gone"]) else state.OK
    if args.quiet:
        return code
    t = [("Dateien gesamt", num(len(rows)), hs(total))]
    t += [(f"  {labels[k][2:]}", num(risk_cnt[k]), hs(risk_size[k])) for k in ("ok", "done", "watch", "risk", "unknown", "?") if risk_cnt[k]]
    t += [None, ("Dateien mit Befunden", num(with_issues), "")]
    t += [(f"  {k[:38]}", num(c), "") for k, c in issue_cnt.most_common(5)]
    if len(issue_cnt) > 5: t.append((f"  … {len(issue_cnt) - 5} weitere Arten", "", ""))
    t.append(None)
    if not args.no_hash: t.append(("Duplikat-Gruppen", num(len(dup_groups)), hs(waste) + " redundant" if waste else ""))
    if copy_groups: t.append(("  gewollt (Regel-Kopien)", num(len(copy_groups)), ""))
    t += [(k, num(v), "") for k, v in extra if v]
    if vstat:
        t += [None, ("Seit letzter Prüfung geändert", num(vstat["changed"]), ""), ("  fehlt", num(len(vstat["gone"])), ""),
              ("  verschoben", num(vstat["moved"]), ""), ("  neu", num(vstat["new"]), "")]
    console_table(args.title, t, [f"Details: {args.out / 'report.md'}"])
    return code

if __name__ == "__main__":
    state.run(main)
