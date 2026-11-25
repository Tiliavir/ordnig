#!/usr/bin/env python3
"""
check.py – checks an (old) file archive for longevity and consistency.

Python standard library only. Optionally used if installed:
  - `file`  (libmagic)  -> detects the real file type, finds wrong extensions
  - `sf`    (siegfried) -> PRONOM IDs, only reported, not needed

Usage:
  python3 check.py /path/to/archive            # report + manifest
  python3 check.py /path --out report_2026     # own output folder
  python3 check.py /path --verify old/manifest.sha256   # bit rot check
  python3 check.py /path --no-hash             # fast, without checksums

Output (in the --out folder, default ./archive_report):
  report.md          summary with findings
  files.csv          one row per file (path, size, date, type, risk, findings)
  manifest.sha256    checksums (sha256sum compatible) for later fixity checks
  duplicates.csv     groups of identical files (intended = rule copies of sort)
"""
import argparse, csv, re, hashlib, os, shutil, subprocess, sys, time, unicodedata, zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import importlib.util
_spec = importlib.util.spec_from_file_location("state", Path(__file__).resolve().parent / "state.py")
state = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(state)

# ---------------------------------------------------------------- Format rating
# ok      = open/widespread, fit for archiving
# watch   = widespread, but proprietary/complex – watch, maybe a second format
# risk    = outdated/proprietary – migration recommended
FORMATS = {
    # Text & documents
    "txt": ("ok", "Text"), "md": ("ok", "Text"), "csv": ("ok", "Text"), "tsv": ("ok", "Text"),
    "xml": ("ok", "Text"), "json": ("ok", "Text"), "html": ("ok", "Text"), "htm": ("ok", "Text"),
    "rtf": ("watch", "Document"), "pdf": ("watch", "Document (check PDF/A)"),
    "odt": ("ok", "Document"), "ods": ("ok", "Spreadsheet"), "odp": ("ok", "Presentation"),
    "docx": ("watch", "Document"), "xlsx": ("watch", "Spreadsheet"), "pptx": ("watch", "Presentation"),
    "doc": ("risk", "Word 97-2003"), "xls": ("risk", "Excel 97-2003"), "ppt": ("risk", "PowerPoint 97-2003"),
    "dot": ("risk", "old Word template"), "pps": ("risk", "old PowerPoint show"),
    "docm": ("risk", "Word with macros"), "xlsm": ("risk", "Excel with macros"),
    "wps": ("risk", "MS Works"), "wks": ("risk", "MS Works spreadsheet"), "wdb": ("risk", "MS Works DB"),
    "wri": ("risk", "Windows Write"), "wpd": ("risk", "WordPerfect"), "wp": ("risk", "WordPerfect"),
    "lwp": ("risk", "Lotus WordPro"), "123": ("risk", "Lotus 1-2-3"), "wk1": ("risk", "Lotus 1-2-3"),
    "sdw": ("risk", "StarOffice"), "sdc": ("risk", "StarOffice"), "sxw": ("risk", "OpenOffice 1.x"),
    "sxc": ("risk", "OpenOffice 1.x"), "pages": ("risk", "Apple Pages"), "numbers": ("risk", "Apple Numbers"),
    "key": ("risk", "Apple Keynote"), "pub": ("risk", "MS Publisher"), "one": ("risk", "OneNote"),
    "xps": ("risk", "XPS"), "oxps": ("risk", "XPS"), "djvu": ("watch", "DjVu"),
    "mdb": ("risk", "Access DB"), "accdb": ("risk", "Access DB"), "dbf": ("watch", "dBase"),
    "msg": ("risk", "Outlook mail"), "pst": ("risk", "Outlook archive"), "ost": ("risk", "Outlook cache"),
    "eml": ("ok", "Mail"), "mbox": ("ok", "Mail"), "vcf": ("ok", "Contact"), "ics": ("ok", "Calendar"),
    "lnk": ("risk", "Windows shortcut"), "url": ("watch", "Web shortcut"),
    # Images
    "tif": ("ok", "Image"), "tiff": ("ok", "Image"), "png": ("ok", "Image"), "jpg": ("ok", "Image"),
    "jpeg": ("ok", "Image"), "gif": ("ok", "Image"), "svg": ("ok", "Vector"), "jp2": ("ok", "Image"),
    "webp": ("watch", "Image"), "heic": ("watch", "Image (HEIF)"), "heif": ("watch", "Image (HEIF)"),
    "bmp": ("watch", "Image uncompressed"), "dng": ("ok", "RAW open"),
    "cr2": ("watch", "RAW Canon"), "cr3": ("watch", "RAW Canon"), "nef": ("watch", "RAW Nikon"),
    "arw": ("watch", "RAW Sony"), "orf": ("watch", "RAW Olympus"), "raf": ("watch", "RAW Fuji"),
    "rw2": ("watch", "RAW Panasonic"), "psd": ("watch", "Photoshop"), "xcf": ("watch", "GIMP"),
    "pcx": ("risk", "PCX"), "pct": ("risk", "Mac PICT"), "pict": ("risk", "Mac PICT"),
    "tga": ("watch", "Targa"), "wmf": ("risk", "Windows Metafile"), "emf": ("risk", "Windows Metafile"),
    "cdr": ("risk", "CorelDRAW"), "ai": ("watch", "Illustrator"), "eps": ("watch", "EPS"),
    "ico": ("watch", "Icon"), "thm": ("watch", "Camera thumbnail"),
    # Audio/Video
    "wav": ("ok", "Audio"), "flac": ("ok", "Audio"), "mp3": ("ok", "Audio"), "ogg": ("ok", "Audio"),
    "opus": ("ok", "Audio"), "m4a": ("watch", "Audio"), "aac": ("watch", "Audio"), "aiff": ("ok", "Audio"),
    "wma": ("risk", "Windows Media Audio"), "ra": ("risk", "RealAudio"), "rm": ("risk", "RealMedia"),
    "mid": ("ok", "MIDI"), "midi": ("ok", "MIDI"),
    "mp4": ("watch", "Video"), "mkv": ("ok", "Video"), "mov": ("watch", "Video"), "webm": ("ok", "Video"),
    "avi": ("watch", "Video (check codec)"), "wmv": ("risk", "Windows Media Video"),
    "mpg": ("watch", "Video"), "mpeg": ("watch", "Video"), "3gp": ("risk", "old phone video"),
    "flv": ("risk", "Flash video"), "swf": ("risk", "Flash"), "vob": ("watch", "DVD video"),
    "mts": ("watch", "AVCHD"), "m2ts": ("watch", "AVCHD"),
    # Archives / containers
    "zip": ("ok", "Archive"), "tar": ("ok", "Archive"), "gz": ("ok", "Archive"), "7z": ("watch", "Archive"),
    "bz2": ("ok", "Archive"), "xz": ("ok", "Archive"), "rar": ("risk", "RAR proprietary"),
    "ace": ("risk", "ACE"), "arj": ("risk", "ARJ"), "lzh": ("risk", "LHA"), "lha": ("risk", "LHA"),
    "cab": ("risk", "CAB"), "sit": ("risk", "StuffIt"), "sitx": ("risk", "StuffIt"),
    "iso": ("watch", "Image"), "img": ("watch", "Image"), "dmg": ("risk", "Mac image"),
    "nrg": ("risk", "Nero image"), "bin": ("watch", "Binary/image"), "cue": ("ok", "Cue sheet"),
    # Other
    "ged": ("ok", "GEDCOM"), "kml": ("ok", "Geo"), "gpx": ("ok", "Geo"), "kmz": ("ok", "Geo"),
    "fit": ("watch", "Garmin FIT"), "epub": ("ok", "E-book"), "mobi": ("risk", "old Kindle"),
    "azw": ("risk", "Kindle DRM"), "chm": ("risk", "Windows help"), "hlp": ("risk", "old Windows help"),
}
JUNK = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db", "~$", ".~lock", "._"}
WIN_BAD = set('<>:"|?*')
WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MOJIBAKE = ("Ã¤", "Ã¶", "Ã¼", "Ã„", "Ã–", "Ãœ", "ÃŸ", "Ã©", "â€", "Â ")

# Expected MIME prefixes per extension (for the extension-vs-content check)
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

# ---------------------------------------------------------------- Integrity checks
def tail(fp, n=1024):
    fp.seek(0, 2); size = fp.tell(); fp.seek(max(0, size - n)); return fp.read()

def check_integrity(path: Path, ext: str):
    """Light structure check. Returns a list of findings."""
    issues = []
    try:
        with open(path, "rb") as f:
            head = f.read(16)
            if ext == "pdf":
                if not head.startswith(b"%PDF"):
                    issues.append("PDF header missing")
                elif b"%%EOF" not in tail(f, 2048):
                    issues.append("PDF truncated (no %%EOF)")
                else:
                    f.seek(0); blob = f.read(200_000)
                    if b"/Encrypt" in blob: issues.append("PDF encrypted")
                    m = re.search(rb'pdfaid:part(?:="|>)(\d)', blob)
                    if m:
                        c = re.search(rb'pdfaid:conformance(?:="|>)([ABUabu])', blob)
                        issues.append(f"info:PDF/A-{m.group(1).decode()}{c.group(1).decode().lower() if c else ''}")
            elif ext in ("jpg", "jpeg"):
                if not head.startswith(b"\xff\xd8"): issues.append("JPEG header missing")
                elif b"\xff\xd9" not in tail(f, 64): issues.append("JPEG truncated (no EOI)")
            elif ext == "png":
                if not head.startswith(b"\x89PNG\r\n\x1a\n"): issues.append("PNG header missing")
                elif b"IEND" not in tail(f, 32): issues.append("PNG truncated (no IEND)")
            elif ext in ("gif",):
                if not head[:3] == b"GIF": issues.append("GIF header missing")
            elif ext in ("tif", "tiff"):
                if head[:4] not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"): issues.append("TIFF header missing")
    except OSError as e:
        issues.append(f"read error: {e.strerror}")
        return issues
    if ext in ("zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "kmz", "jar"):
        try:
            with zipfile.ZipFile(path) as z:
                if any(i.flag_bits & 0x1 for i in z.infolist()):
                    issues.append("ZIP encrypted")
                else:
                    bad = z.testzip()
                    if bad: issues.append(f"ZIP broken at {bad}")
        except zipfile.BadZipFile:
            issues.append("ZIP structure broken")
        except (OSError, NotImplementedError, RuntimeError) as e:
            issues.append(f"ZIP cannot be checked: {e}")
    return issues

# ---------------------------------------------------------------- Name checks
def check_name(rel: str, name: str, raw_bytes: bytes):
    issues = []
    try:
        raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        issues.append("File name is not valid UTF-8 (old code page?)")
    if any(m in name for m in MOJIBAKE):
        issues.append("Mojibake in the name (e.g. Ã¤ instead of ä)")
    if unicodedata.normalize("NFC", name) != name:
        issues.append("Unicode NFD (Mac spelling) – risk of duplicates/sync problems")
    if set(name) & WIN_BAD:
        issues.append("Characters invalid on Windows (<>:\"?* or pipe)")
    if name != name.rstrip(" ."):
        issues.append("Ends with a space/dot")
    if name != name.lstrip(" "):
        issues.append("Starts with a space")
    if name.split(".")[0].upper() in WIN_RESERVED:
        issues.append("Reserved Windows name")
    if any(ord(c) < 32 for c in name):
        issues.append("Control characters in the name")
    if len(rel) > 240:
        issues.append(f"Path very long ({len(rel)} characters, Windows limit 260)")
    if len(name.encode("utf-8", "surrogateescape")) > 255:
        issues.append("Name > 255 bytes")
    return issues

# ---------------------------------------------------------------- MIME via libmagic
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

# ---------------------------------------------------------------- Rule copies
def rule_copies(inbox):
    """Contents that sort filed several times by a rule (copy): SHA-256 -> number of instances
    {to, copies…}. Recognised by content, so renaming or moving changes nothing."""
    log = inbox / state.STATE / "sort-log.csv" if inbox else None
    if not log or not log.is_file():
        return {}
    counts = {}
    with open(log, encoding="utf-8", newline="", errors="surrogateescape") as f:
        for r in csv.DictReader(f):
            if r.get("status") != "filed" or not r.get("copies") or not r.get("sha256"):
                continue
            n = 1 + sum(1 for k in r["copies"].split(" | ") if k)
            counts[r["sha256"]] = max(counts.get(r["sha256"], 0), n)
    return counts


def intended(digest, group, copies):
    """Intended if a rule filed this content at least that many times; one more instance makes
    the group a finding again."""
    return len(group) <= copies.get(digest, 0)


# ---------------------------------------------------------------- Scan
def in_skip(p: Path, skip):
    return any(p == s or s in p.parents for s in skip)

def scan(root: Path, args, skip=()):
    """skip: absolute folders that are not checked (the inbox: mail not filed yet)."""
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

# ---------------------------------------------------------------- Output
def hs(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024: return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

def num(n):
    return f"{n:,}"

def progress(msg):
    """Progress only in a terminal, one line, overwritten."""
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[K" + msg[:100]); sys.stderr.flush()

def progress_done():
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[K"); sys.stderr.flush()

def console_table(title, rows, footer=()):
    """rows: (label, value, value2) – None = separator."""
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
    """Tree of all files; folder lines with totals, file lines with all findings."""
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
        bits = [f"{num(a['files'])} {'file' if a['files'] == 1 else 'files'}", hs(a["size"])] if a["files"] else ["empty"]
        if a["risk"]: bits.append(f"🔴 {num(a['risk'])}")
        if a["issues"]: bits.append(f"⚠ {num(a['issues'])}")
        if case_map.get(rel): bits.append("⚠ name collision: " + ", ".join(case_map[rel]))
        return " · ".join(bits)

    def file_note(r):
        bits = [hs(r["size"]), r["mtime"]] if r["mtime"] else [hs(r["size"])]
        if r["risk"] in RISK_MARK:
            bits.append(f"{RISK_MARK[r['risk']]} {r['kind'] or '.' + r['ext'] or 'no extension'}"
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
        out.append(f"⚠ not readable: {e}")
    return out

SUGGEST = {"doc": "ODT + PDF/A", "xls": "ODS", "ppt": "ODP + PDF/A",
           "wps": "ODT + PDF/A", "wri": "ODT", "wpd": "ODT + PDF/A", "lwp": "ODT + PDF/A",
           "sxw": "ODT", "sxc": "ODS", "sdw": "ODT", "sdc": "ODS", "mdb": "SQLite + CSV",
           "accdb": "SQLite + CSV", "wma": "FLAC/Opus", "wmv": "MKV/MP4", "rm": "MKV/MP4",
           "ra": "FLAC/Opus", "flv": "MP4", "3gp": "MP4", "rar": "unpack → ZIP/TAR",
           "ace": "unpack → ZIP/TAR", "arj": "unpack → ZIP/TAR", "lzh": "unpack → ZIP/TAR",
           "sit": "unpack → ZIP/TAR", "cab": "unpack", "msg": "EML", "pst": "MBOX/EML",
           "pct": "PNG/TIFF", "pcx": "PNG", "wmf": "SVG/PNG", "emf": "SVG/PNG", "cdr": "SVG + PDF",
           "xps": "PDF/A", "pages": "ODT/PDF", "numbers": "ODS/CSV", "key": "PDF", "pub": "PDF/A",
           "mobi": "EPUB", "chm": "HTML/PDF", "hlp": "HTML/PDF", "dmg": "unpack", "nrg": "ISO",
           "docm": "ODT (check macros)", "xlsm": "ODS (check macros)", "lnk": "delete/document"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--out", type=Path, default=Path("archive_report"))
    ap.add_argument("--no-hash", action="store_true", help="no checksums/duplicates (faster)")
    ap.add_argument("--no-integrity", action="store_true", help="no structure check")
    ap.add_argument("--verify", type=Path, help="check an earlier manifest.sha256 against the files")
    ap.add_argument("--quiet", action="store_true", help="no table on the console")
    ap.add_argument("--title", default="Check", help="title of the console table")
    ap.add_argument("--inbox", type=Path, default=None, help="inbox folder with .ordnig/ (log of run)")
    args = ap.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")
    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    progress(f"Scanning {root} …")
    inbox = state.inbox_dir(root, args.inbox)
    inbox = inbox if inbox.is_dir() else None
    skip = [inbox] if inbox and inbox != root else []
    paths, empty_dirs, dir_errors, case_map = scan(root, args, skip)
    progress(f"{num(len(paths))} files, detecting types …")
    mimes = detect_mimes(paths)

    # Originals already converted (from the log of migrate.py)
    migrated = defaultdict(list)
    log = state.state_dir(root, args.inbox) / state.LOG_NAME
    if log.exists():
        for r in csv.DictReader(open(log, encoding="utf-8", errors="surrogateescape")):
            target = state.to_abs(r.get("target", ""), root)
            if r.get("status") == "ok" and target and Path(target).exists():
                migrated[os.path.relpath(state.to_abs(r["source"], root), root)].append(Path(target).name)

    rows, hashes = [], {}
    now = time.time()
    for i, p in enumerate(paths, 1):
        if i % 500 == 0:
            progress(f"Analysing {num(i)}/{num(len(paths))}")
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
            issues.append("Symlink" + (" (broken)" if not p.exists() else ""))
            rows.append(dict(path=rel, size=0, mtime="", ext=ext, mime="", risk="?", kind="Symlink", issues="; ".join(issues)))
            continue
        size = st.st_size
        mt = st.st_mtime
        if size == 0: issues.append("Empty file (0 bytes)")
        if mt > now + 86400: issues.append("Modification date in the future")
        if mt < 315532800: issues.append("Modification date before 1980 (suspicious)")
        low = name.lower()
        if low in JUNK or any(low.startswith(j) for j in ("~$", ".~lock", "._")):
            issues.append("System junk/temp file")
        if low.endswith((".tmp", ".bak", ".old", "~")):
            issues.append("Temp/backup file")
        if not ext: issues.append("No file extension")
        risk, kind = FORMATS.get(ext, ("unknown", ""))
        if rel in migrated and risk in ("risk", "watch", "unknown"):
            risk, kind = "done", f"{kind or '.' + ext} → {', '.join(migrated[rel])}"
        mime = mimes.get(p, "")
        if mime_mismatch(ext, mime):
            issues.append(f"Extension does not match the content ({mime})")
        if not args.no_integrity and size > 0:
            for iss in check_integrity(p, ext):
                if iss.startswith("info:PDF/A"):
                    kind = iss[5:]
                    if risk == "watch": risk = "ok"
                elif iss.startswith("info:"): kind += " · " + iss[5:]
                else: issues.append(iss)
        rows.append(dict(path=rel, size=size, mtime=datetime.fromtimestamp(mt).strftime("%Y-%m-%d"),
                         ext=ext, mime=mime, risk=risk, kind=kind, issues="; ".join(issues), _p=p))

    # Checksums (all files, for the manifest) – duplicates from them
    dup_groups, copy_groups = [], []
    if not args.no_hash:
        todo = [r for r in rows if r.get("_p") is not None and r["size"] > 0]
        for i, r in enumerate(todo, 1):
            if i % 200 == 0: progress(f"Checksums {num(i)}/{num(len(todo))}")
            try: hashes[r["_p"]] = sha256(r["_p"])
            except OSError as e: r["issues"] += f"; read error while hashing: {e.strerror}"
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
                by_path[p]["copy"] = "rule copy: " + ", ".join(str(q.relative_to(root)) for q in g if q != p)
        for gi, g in enumerate(dup_groups, 1):
            for p in g:
                others = [str(q.relative_to(root)) for q in g if q != p]
                by_path[p]["dup"] = f"duplicate (group {gi}): " + ", ".join(others[:3]) + (f" +{len(others)-3}" if len(others) > 3 else "")
        with open(args.out / "manifest.sha256", "w", encoding="utf-8", errors="surrogateescape") as m:
            for r in rows:
                p = r.get("_p")
                if p in hashes: m.write(f"{hashes[p]}  {r['path']}\n")
        with open(args.out / "duplicates.csv", "w", newline="", encoding="utf-8", errors="surrogateescape") as f:
            w = csv.writer(f); w.writerow(["group", "sha256", "size", "path", "intended"])
            for gi, g in enumerate(dup_groups + copy_groups, 1):
                for p in g: w.writerow([gi, hashes[p], p.stat().st_size, str(p.relative_to(root)),
                                        "yes" if gi > len(dup_groups) else ""])

    # Fixity-Verify
    vstat = None
    if args.verify:
        progress("Comparing with the old manifest …")
        by_rel = {r["path"]: r for r in rows}
        cur = {r["path"]: hashes.get(r.get("_p")) for r in rows}
        old = {}
        for line in open(args.verify, encoding="utf-8", errors="surrogateescape"):
            if "  " in line:
                h, pth = line.rstrip("\n").split("  ", 1)
                if not in_skip(root / pth, skip):  # a manifest may still contain the inbox
                    old[pth] = h
        changed = [p for p, h in old.items() if p in cur and cur[p] and cur[p] != h]
        missing = [p for p in old if p not in cur]
        new = [p for p in cur if p not in old and cur[p]]
        oldh = defaultdict(list)
        for p, h in old.items(): oldh[h].append(p)
        moved = {p: oldh[cur[p]][0] for p in new if cur[p] in oldh and oldh[cur[p]][0] in missing}
        for p in changed:
            by_rel[p]["issues"] = "; ".join(filter(None, [by_rel[p]["issues"], "Content changed since the last check (bit rot or edited?)"]))
        for p in new:
            by_rel[p]["info"] = f"moved from {moved[p]}" if p in moved else "new since the last check"
        gone = [p for p in missing if p not in set(moved.values())]
        vstat = dict(changed=len(changed), gone=gone, moved=len(moved), new=len(new) - len(moved))
    progress_done()

    # CSV
    with open(args.out / "files.csv", "w", newline="", encoding="utf-8", errors="surrogateescape") as f:
        w = csv.DictWriter(f, fieldnames=["path", "size", "mtime", "ext", "mime", "risk", "kind", "issues", "dup", "info", "copy"],
                           extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

    # Figures
    total = sum(r["size"] for r in rows)
    risk_cnt, risk_size = Counter(), Counter()
    ext_cnt, ext_size = Counter(), Counter()
    issue_cnt = Counter()
    years = Counter()
    for r in rows:
        risk_cnt[r["risk"]] += 1; risk_size[r["risk"]] += r["size"]
        ext_cnt[r["ext"] or "(none)"] += 1; ext_size[r["ext"] or "(none)"] += r["size"]
        if r["mtime"]: years[r["mtime"][:4]] += 1
        for iss in filter(None, (s.strip() for s in r["issues"].split(";"))):
            key = iss.split("(")[0].strip() if "Extension does not match" in iss or "Path very long" in iss else iss
            key = "ZIP broken" if key.startswith("ZIP broken at") else key
            issue_cnt[key] += 1
    with_issues = sum(1 for r in rows if r["issues"])
    waste = sum(g[0].stat().st_size * (len(g) - 1) for g in dup_groups)
    n_case = sum(map(len, case_map.values()))
    labels = {"ok": "✅ fit for archiving", "done": "☑ converted (copy next to it)", "watch": "🟡 watch",
              "risk": "🔴 convert", "unknown": "⚪ unknown", "?": "❓ not readable"}

    # Report: tree, then summary
    L = [f"# Archive check: `{root}`", "",
         f"As of {datetime.now():%Y-%m-%d %H:%M} · {num(len(rows))} files · {hs(total)} · run time {time.time()-t0:.0f}s", "",
         "Legend: 🔴 convert · ☑ converted · 🟡 watch · ⚪ unknown format · ⚠ finding · ⧉ duplicate · ⎘ intended rule copy · ℹ note. "
         "Folder lines: totals including subfolders. Summary at the end.", ""]
    if not shutil.which("file"):
        L += ["> Note: `file` not found – extension-vs-content check skipped.", ""]
    L += ["## Tree", "", "```text"] + render_tree(root.name or str(root), rows, empty_dirs, case_map, dir_errors) + ["```", ""]

    L += ["## Summary", "", "### Longevity of the formats", "", "| Rating | Files | Size |", "|---|---:|---:|"]
    for k in ("ok", "done", "watch", "risk", "unknown", "?"):
        if risk_cnt[k]: L.append(f"| {labels[k]} | {num(risk_cnt[k])} | {hs(risk_size[k])} |")
    risky = Counter({e: c for e, c in ext_cnt.items() if FORMATS.get(e, ("",))[0] == "risk"})
    if risky:
        L += ["", "**Candidates for conversion** (format → suggestion):", ""]
        for e, c in risky.most_common():
            L.append(f"- `.{e}` – {FORMATS[e][1]}: {num(c)} files, {hs(ext_size[e])} → {SUGGEST.get(e, 'check')}")
    unk = Counter({e: c for e, c in ext_cnt.items() if e not in FORMATS})
    if unk:
        L += ["", "**Unknown extensions** (top 20, please classify):", "",
              ", ".join(f"`.{e}` ({c})" for e, c in unk.most_common(20))]
    L += ["", "### Top formats", "", "| Extension | Files | Size |", "|---|---:|---:|"]
    for e, c in ext_cnt.most_common(15):
        L.append(f"| {e} | {num(c)} | {hs(ext_size[e])} |")
    L += ["", "### Consistency & integrity", ""]
    if issue_cnt:
        L += ["| Finding | Count |", "|---|---:|"] + [f"| {k} | {num(c)} |" for k, c in issue_cnt.most_common()]
    else:
        L.append("Nothing found.")
    extra = [("Name collisions (upper/lower case)", n_case), ("Empty folders", len(empty_dirs)), ("Unreadable folders", len(dir_errors))]
    L += [""] + [f"- {k}: {v}" for k, v in extra if v]
    if dup_groups:
        L += ["", "### Duplicates", "", f"{num(len(dup_groups))} groups, {hs(waste)} redundant. "
              "Marked ⧉ in the tree; all groups in `duplicates.csv`."]
    if copy_groups:
        L += ["", "### Intended duplicates", "", f"{num(len(copy_groups))} groups that `sort` filed several times "
              "by a rule (`copy`). Marked ⎘ in the tree, in `duplicates.csv` with `intended = yes`.", ""]
        L += ["- " + " = ".join(f"`{q}`" for q in sorted(str(q.relative_to(root)) for q in g)) for g in copy_groups[:200]]
        if len(copy_groups) > 200: L.append(f"- … {num(len(copy_groups) - 200)} more")
    if years:
        L += ["", "### Files by year of modification", "", "```text"]
        mx = max(years.values())
        L += [f"{y} {'█' * max(1, round(40 * years[y] / mx))} {years[y]}" for y in sorted(years)] + ["```"]
    if vstat:
        L += ["", f"### Comparison with `{args.verify}`", "",
              f"- Content changed: {vstat['changed']} (marked ⚠ in the tree)",
              f"- Moved/renamed: {vstat['moved']} · new: {vstat['new']} (ℹ in the tree)",
              f"- Missing: {len(vstat['gone'])}"] + [f"  - `{p}`" for p in vstat["gone"][:200]]
    (args.out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8", errors="replace")

    code = state.ERROR if vstat and (vstat["changed"] or vstat["gone"]) else state.OK
    if args.quiet:
        return code
    t = [("Files in total", num(len(rows)), hs(total))]
    t += [(f"  {labels[k][2:]}", num(risk_cnt[k]), hs(risk_size[k])) for k in ("ok", "done", "watch", "risk", "unknown", "?") if risk_cnt[k]]
    t += [None, ("Files with findings", num(with_issues), "")]
    t += [(f"  {k[:38]}", num(c), "") for k, c in issue_cnt.most_common(5)]
    if len(issue_cnt) > 5: t.append((f"  … {len(issue_cnt) - 5} more kinds", "", ""))
    t.append(None)
    if not args.no_hash: t.append(("Duplicate groups", num(len(dup_groups)), hs(waste) + " redundant" if waste else ""))
    if copy_groups: t.append(("  intended (rule copies)", num(len(copy_groups)), ""))
    t += [(k, num(v), "") for k, v in extra if v]
    if vstat:
        t += [None, ("Changed since the last check", num(vstat["changed"]), ""), ("  missing", num(len(vstat["gone"])), ""),
              ("  moved", num(vstat["moved"]), ""), ("  new", num(vstat["new"]), "")]
    console_table(args.title, t, [f"Details: {args.out / 'report.md'}"])
    return code

if __name__ == "__main__":
    state.run(main)
