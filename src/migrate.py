#!/usr/bin/env python3
"""
ablage_migrate.py – Legt neben jede Datei archivtaugliche Fassungen:

  Office-Datei (doc, docx, xls, ppt, rtf, wpd, wps, sxw, …)
      -> ODF-Pendant daneben (.odt / .ods / .odp)
      -> PDF/A-2u mit OCR-Textebene daneben
  ODF-Datei (odt, ods, odp)  -> PDF/A-2u daneben
  PDF                        -> <name>_pdfa.pdf (OCR für Seiten ohne Text, PDF/A-2u)
  Bilder (nur mit --images)  -> PDF/A-2u mit OCR (für Scans als TIFF/JPG/PNG)

Originale werden NIE verändert oder gelöscht. Bereits vorhandene Ziele werden
nicht überschrieben. Ohne --apply passiert nichts außer einer Vorschau.

PDF/A-2u wird nur deklariert, wenn veraPDF die Datei als 2u bestätigt.
Fällt die Prüfung durch (z. B. Symbolschriften ohne Unicode-Zuordnung), bleibt
die Datei 2b und das steht im Protokoll. Ohne installiertes veraPDF: 2b,
Status "ungeprüft".

Voraussetzungen (Linux Mint / Debian):
  sudo apt install libreoffice ocrmypdf tesseract-ocr-deu ghostscript img2pdf
  veraPDF: https://verapdf.org/software/ (Installer, Java) – Befehl `verapdf` im PATH
  Optional für Fraktur: tesseract-ocr-script-latn bzw. tessdata "deu_latf"

Aufruf:
  python3 ablage_migrate.py /ablage                    # Vorschau
  python3 ablage_migrate.py /ablage --apply            # ausführen
  python3 ablage_migrate.py /ablage --apply --images tif,tiff --lang deu+eng -j 4
Das Protokoll liegt in <ablage>/.ordnig/migration_log.csv; Wiederholungs-
läufe überspringen Erledigtes und machen bei Fehlern weiter.
"""
import argparse, csv, os, re, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import Counter, defaultdict
from pathlib import Path

TEXT = {"doc", "docx", "docm", "dot", "dotx", "rtf", "wpd", "wp", "wps", "wri", "lwp", "sxw", "sdw",
        "pages", "txt"}
CALC = {"xls", "xlsx", "xlsm", "xlt", "xltx", "wks", "wk1", "123", "sxc", "sdc", "numbers", "dbf"}
IMPRESS = {"ppt", "pptx", "pptm", "pps", "ppsx", "pot", "potx", "sxi", "sdd", "key"}
ODF = {"odt": "odt", "ods": "ods", "odp": "odp", "fodt": "odt", "fods": "ods", "fodp": "odp"}
ODF_FOR = {**{e: "odt" for e in TEXT - {"txt"}}, **{e: "ods" for e in CALC - {"dbf"}},
           **{e: "odp" for e in IMPRESS}}
PDF_FILTER = {"odt": "writer_pdf_Export", "ods": "calc_pdf_Export", "odp": "impress_pdf_Export"}
SKIP_DIRS = {"@eaDir", "#recycle", ".git", ".ordnig"}
LOG_FIELDS = ["quelle", "aktion", "ziel", "status", "pdfa", "hinweis", "zeit", "sekunden"]
VERA_BATCH = 50


# ----------------------------------------------------------------- Hilfen
def run(cmd, timeout=900, tail=1500, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, **kw)
    out = r.stdout + r.stderr
    return r.returncode, out[-tail:] if tail else out

RESERVED = set()

def free_target(src: Path, ext: str, suffix=""):
    """brief.doc -> brief.odt; falls belegt: brief.doc.odt; falls auch belegt: None.
    Ziele werden bei der Planung reserviert, damit parallele Jobs nicht kollidieren."""
    for cand in (src.with_name(src.stem + suffix + "." + ext), src.with_name(src.name + suffix + "." + ext)):
        key = str(cand).lower()
        if not cand.exists() and key not in RESERVED:
            RESERVED.add(key)
            return cand
    return None

def soffice(src: Path, fmt: str, workdir: Path, profile: Path):
    """Konvertiert mit LibreOffice in ein temporäres Verzeichnis, gibt Ergebnisdatei zurück."""
    out = workdir / "lo"; out.mkdir(exist_ok=True)
    for f in out.iterdir(): f.unlink()
    code, log = run(["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
                     "--convert-to", fmt, "--outdir", str(out), str(src)])
    res = list(out.iterdir())
    if code != 0 or not res:
        raise RuntimeError(f"LibreOffice: {log.strip()[-300:] or 'keine Ausgabe'}")
    return res[0]

def lo_pdfa_filter(kind):
    # PDF/A-2, getaggt, Schriften eingebettet
    opts = '{"SelectPdfVersion":{"type":"long","value":"2"},"UseTaggedPDF":{"type":"boolean","value":"true"}}'
    return f"pdf:{PDF_FILTER[kind]}:{opts}"

def page_has_text(page, depth=0):
    """Text = Schriften in den Ressourcen der Seite oder ihrer Form-XObjects."""
    res = page.get("/Resources") if hasattr(page, "get") else None
    if res is None:
        return False
    if res.get("/Font"):
        return True
    if depth < 3:
        for xo in (res.get("/XObject") or {}).values():
            if xo.get("/Subtype") == "/Form" and page_has_text(xo, depth + 1):
                return True
    return False

def inspect_pdf(pdf: Path):
    """(Seiten, Seiten ohne Text, PDF/A-Kennung wie '2u' oder ''). Wirft bei Verschlüsselung/Defekt."""
    import pikepdf
    with pikepdf.open(pdf) as p:
        pages = len(p.pages)
        no_text = sum(1 for pg in p.pages if not page_has_text(pg.obj))
        part = conf = ""
        try:
            m = p.open_metadata()
            part, conf = str(m.get("pdfaid:part", "")), str(m.get("pdfaid:conformance", ""))
        except Exception:
            pass
    return pages, no_text, (part + conf.lower()) if part else ""

def ocr_pdfa(src_pdf: Path, dst_pdf: Path, lang: str, image_input=False, fix_scans=False, ocr_jobs=1):
    cmd = ["ocrmypdf", "--output-type", "pdfa-2", "-l", lang, "--jobs", str(ocr_jobs), "--optimize", "1", "-q"]
    if fix_scans:
        cmd += ["--rotate-pages", "--deskew"]   # Ausrichtung/Schräglage: ~40 % mehr Zeit
    cmd += ["--image-dpi", "300"] if image_input else ["--skip-text"]
    code, log = run(cmd + [str(src_pdf), str(dst_pdf)], timeout=3600)
    # 6 = bereits Text vorhanden (nur ohne --skip-text), 10 = PDF/A-Konvertierung unvollständig
    if code not in (0, 10) or not dst_pdf.exists():
        raise RuntimeError(f"ocrmypdf ({code}): {log.strip()[-300:]}")
    return "ocrmypdf: PDF/A nur teilweise erreicht" if code == 10 else ""

def set_conformance(pdf: Path, level: str):
    import pikepdf
    with pikepdf.open(pdf, allow_overwriting_input=True) as p:
        with p.open_metadata(set_pikepdf_as_editor=False) as m:
            m["pdfaid:part"] = "2"
            m["pdfaid:conformance"] = level
        p.save(pdf)

def verapdf(pdf: Path, flavour: str):
    if not shutil.which("verapdf"):
        return None
    code, out = run(["verapdf", "--flavour", flavour, str(pdf)], timeout=1800, tail=None)
    if 'isCompliant="true"' in out or re.search(r"\bPASS\b", out):
        return True
    return False

def parse_verapdf(xml_text: str):
    """veraPDF-XML (mehrere Jobs) -> {Dateiname: konform?}."""
    import xml.etree.ElementTree as ET
    out = {}
    root = ET.fromstring(xml_text[xml_text.index("<"):])
    for job in root.iter():
        if not job.tag.endswith("job"):
            continue
        name = next((e.text for e in job.iter() if e.tag.endswith("name") and e.text), None)
        rep = next((e for e in job.iter() if e.tag.endswith("validationReport")), None)
        if name:
            out[Path(name).name] = rep is not None and rep.get("isCompliant") == "true"
    return out

def verapdf_batch(pdfs, flavour):
    """Ein JVM-Start für viele Dateien. Fallback auf Einzelaufrufe, wenn die Ausgabe nicht lesbar ist."""
    if not pdfs:
        return {}, True
    code, out = run(["verapdf", "--flavour", flavour, *map(str, pdfs)], timeout=3600, tail=None)
    try:
        res = parse_verapdf(out)
        if all(p.name in res for p in pdfs):
            return {p: res[p.name] for p in pdfs}, True
    except Exception:
        pass
    # Ausgabe nicht lesbar: einzeln prüfen und das im Protokoll sagen (kein stiller Rückfall)
    return {p: bool(verapdf(p, flavour)) for p in pdfs}, False

def validate_all(targets, progress_cb=lambda i, n: None):
    """targets: Liste fertiger PDFs (deklariert 2b). Prüft Kopien auf 2u, dann ggf. 2b, und ersetzt
    das Ziel nur durch genau die geprüfte Kopie. -> {target: (pdfa, hinweis)}"""
    results = {}
    for i in range(0, len(targets), VERA_BATCH):
        chunk = targets[i:i + VERA_BATCH]
        progress_cb(i, len(targets))
        with tempfile.TemporaryDirectory(prefix="vera_") as td:
            copies = {}
            for k, t in enumerate(chunk):
                c = Path(td) / f"{k:04d}.pdf"
                shutil.copy2(t, c); set_conformance(c, "U"); copies[c] = t
            ok_u, batch_u = verapdf_batch(list(copies), "2u")
            failed = [c for c, ok in ok_u.items() if not ok]
            for c in failed:
                set_conformance(c, "B")
            ok_b, batch_b = verapdf_batch(failed, "2b") if failed else ({}, True)
            note = "" if batch_u and batch_b else " (Einzelprüfung: veraPDF-Stapelausgabe nicht lesbar)"
            for c, t in copies.items():
                if ok_u[c]:
                    st = t.stat(); shutil.copyfile(c, t); os.utime(t, (st.st_atime, st.st_mtime))
                    results[t] = ("2u", "veraPDF bestanden" + note)
                elif ok_b[c]:
                    results[t] = ("2b", "2u nicht erfüllt, 2b bestanden" + note)
                else:
                    results[t] = ("ungültig", "veraPDF: weder 2u noch 2b" + note)
    return results

def finalize_pdfa(pdf: Path):
    """Deklariert 2u, wenn veraPDF zustimmt, sonst 2b. Gibt (pdfa, hinweis) zurück."""
    set_conformance(pdf, "U")
    ok = verapdf(pdf, "2u")
    if ok:
        return "2u", "veraPDF bestanden"
    set_conformance(pdf, "B")
    if ok is None:
        return "2b", "ungeprüft (veraPDF fehlt) – 2u nicht deklariert"
    ok_b = verapdf(pdf, "2b")
    return ("2b", "2u nicht erfüllt, 2b bestanden") if ok_b else ("ungültig", "veraPDF: weder 2u noch 2b")


# ----------------------------------------------------------------- Planung
def plan(root: Path, images: set, done: set, generated: set, info=None):
    """info (dict, optional) sammelt: already_pdfa (Liste), scan_pdfs (Anzahl)."""
    info = info if info is not None else {}
    info.setdefault("already_pdfa", []); info.setdefault("scan_pdfs", 0)
    jobs = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for n in sorted(filenames):
            p = Path(dirpath) / n
            if str(p) in generated or n.startswith(("~$", ".~lock", "._")) or p.is_symlink():
                continue
            ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
            if ext in ODF_FOR:
                jobs.append(("office", p, ext))
            elif ext in ODF:
                jobs.append(("odf", p, ext))
            elif ext == "pdf" and not n.lower().endswith("_pdfa.pdf"):
                if str(p) not in done:
                    try:
                        pages, no_text, pdfa = inspect_pdf(p)
                    except Exception:
                        pages, no_text, pdfa = 0, 0, ""      # verschlüsselt/defekt: Job meldet den Fehler
                    if pdfa and pages and not no_text:
                        info["already_pdfa"].append(p); continue
                    if no_text:
                        info["scan_pdfs"] += 1
                jobs.append(("pdf", p, ext))
            elif ext in images:
                jobs.append(("image", p, ext))
    out = []
    for kind, p, ext in jobs:
        if str(p) in done:
            continue
        odf_t = free_target(p, ODF_FOR[ext]) if kind == "office" else None
        pdf_t = free_target(p, "pdf", "_pdfa" if kind == "pdf" else "")
        out.append((kind, p, ext, odf_t, pdf_t))
    return out

def describe(kind, p, ext, odf_t, pdf_t):
    belegt = "(Ziel belegt, übersprungen)"
    acts = []
    if kind == "office":
        acts.append(f"ODF   -> {odf_t.name if odf_t else belegt}")
    label = {"pdf": "PDF/A+OCR", "image": "OCR-PDF/A"}.get(kind, "PDF/A")
    acts.append(f"{label} -> {pdf_t.name if pdf_t else belegt}")
    return acts


# ----------------------------------------------------------------- Ausführung (Worker)
def process(job, lang, fix_scans=False, ocr_jobs=1, defer_validation=False):
    kind, src, ext, odf_t, pdf_t = job
    rows = []
    t0 = time.monotonic()
    def log(action, target, status, pdfa="", hint=""):
        rows.append(dict(quelle=str(src), aktion=action, ziel=str(target or ""), status=status,
                         pdfa=pdfa, hinweis=hint, zeit=time.strftime("%Y-%m-%d %H:%M:%S"),
                         sekunden=f"{time.monotonic() - t0:.1f}"))
    with tempfile.TemporaryDirectory(prefix="mig_") as td:
        work = Path(td); profile = work / "profile"
        mtime = src.stat().st_mtime
        def place(tmp: Path, target: Path):
            shutil.move(str(tmp), target)
            os.utime(target, (mtime, mtime))          # Datum des Originals übernehmen
        try:
            pdf_source = None
            if kind == "office":
                odf = ODF_FOR[ext]
                t = odf_t
                if t:
                    try:
                        res = soffice(src, odf, work, profile)
                        place(res, t); log("odf", t, "ok")
                    except Exception as e:
                        log("odf", t, "fehler", hint=str(e))
                else:
                    log("odf", None, "übersprungen", hint="Ziel belegt")
                kind_odf = odf
            elif kind == "odf":
                kind_odf = ODF[ext]
            if kind in ("office", "odf"):
                t = pdf_t
                if not t:
                    log("pdfa", None, "übersprungen", hint="Ziel belegt"); return rows
                pdf_source = soffice(src, lo_pdfa_filter(kind_odf), work, profile)
                tmp_out = work / "out.pdf"
                _, no_text, _ = inspect_pdf(pdf_source)
                if no_text:      # z. B. eingefügter Scan: OCR nötig (Tagging geht dabei verloren)
                    hint = ocr_pdfa(pdf_source, tmp_out, lang, fix_scans=fix_scans, ocr_jobs=ocr_jobs)
                    hint = "; ".join(filter(None, [hint, f"OCR für {no_text} Seite(n) ohne Text"]))
                else:            # LibreOffice-PDF/A direkt: schneller, behält Tagging
                    shutil.move(str(pdf_source), tmp_out); hint = ""
            elif kind == "pdf":
                t = pdf_t
                if not t:
                    log("pdfa", None, "übersprungen", hint="Ziel belegt"); return rows
                tmp_out = work / "out.pdf"
                hint = ocr_pdfa(src, tmp_out, lang, fix_scans=fix_scans, ocr_jobs=ocr_jobs)
            else:  # image
                t = pdf_t
                if not t:
                    log("pdfa", None, "übersprungen", hint="Ziel belegt"); return rows
                tmp_in = work / "in.pdf"
                code, out = run(["img2pdf", "-o", str(tmp_in), str(src)])   # auch mehrseitige TIFFs
                if code != 0:
                    raise RuntimeError(f"img2pdf: {out[-300:]}")
                tmp_out = work / "out.pdf"
                hint = ocr_pdfa(tmp_in, tmp_out, lang, image_input=True, fix_scans=fix_scans, ocr_jobs=ocr_jobs)
            if defer_validation:     # veraPDF später im Stapel; bis dahin ehrlich 2b
                set_conformance(tmp_out, "B")
                pdfa, vhint = "2b", "veraPDF-Prüfung ausstehend"
            else:
                pdfa, vhint = finalize_pdfa(tmp_out)
            place(tmp_out, t)
            log("pdfa", t, "ok" if pdfa != "ungültig" else "warnung", pdfa, "; ".join(filter(None, [hint, vhint])))
        except Exception as e:
            msg = str(e)
            if "encrypt" in msg.lower(): msg = "PDF verschlüsselt – bitte manuell entsperren"
            log("pdfa", None, "fehler", hint=msg)
    return rows


# ----------------------------------------------------------------- main
# ----------------------------------------------------------------- Ausgabe
def progress(msg):
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[K" + msg[:100]); sys.stderr.flush()

def progress_done():
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[K"); sys.stderr.flush()

def console_table(title, rows, footer=()):
    data = [r for r in rows if r is not None]
    w = [max(len(str(r[i])) for r in data + [("", "")]) for i in range(2)]
    w[0] = max(w[0], len(title))
    line = lambda l, m, r: l + m.join("─" * (x + 2) for x in w) + r
    out = [line("┌", "┬", "┐"), f"│ {title:<{w[0]}} │ {'':>{w[1]}} │", line("├", "┼", "┤")]
    for r in rows:
        out.append(line("├", "┼", "┤") if r is None else f"│ {r[0]:<{w[0]}} │ {str(r[1]):>{w[1]}} │")
    out.append(line("└", "┴", "┘"))
    print("\n".join(out + [f for f in footer if f]))

def render_tree(root_name, notes):
    """notes: {relpath: text} -> Baumzeilen, Ordner mit Anzahl betroffener Dateien."""
    tree = {}
    for rel, note in notes.items():
        n = tree
        parts = Path(rel).parts
        for part in parts[:-1]:
            n = n.setdefault(part + "/", {})
        n[parts[-1]] = note
    def count(n):
        return sum(count(v) if isinstance(v, dict) else 1 for v in n.values())
    out = [f"{root_name}/  —  {count(tree)} Dateien"]
    def walk(n, prefix):
        keys = sorted(n, key=lambda k: (not k.endswith("/"), k.lower()))
        for i, k in enumerate(keys):
            branch, cont = ("└── ", "    ") if i == len(keys) - 1 else ("├── ", "│   ")
            v = n[k]
            if isinstance(v, dict):
                c = count(v)
                out.append(f"{prefix}{branch}{k}  —  {c} {'Datei' if c == 1 else 'Dateien'}")
                walk(v, prefix + cont)
            else:
                out.append(f"{prefix}{branch}{k}  {v}")
    walk(tree, "")
    return out

def write_report(path, title, root, tree_lines, table, notes, sections=()):
    if not path:
        return
    L = [f"# {title}: `{root}`", "", f"Stand {time.strftime('%Y-%m-%d %H:%M')}", "",
         "Legende: → wird/wurde angelegt · ✓ erledigt · ✗ Fehler · ⊘ übersprungen. Zusammenfassung am Ende.", "",
         "## Baum", "", "```text"] + tree_lines + ["```", "", "## Zusammenfassung", "", "| | Anzahl |", "|---|---:|"]
    L += [f"| {r[0].strip()} | {r[1]} |" for r in table if r is not None]
    L += [""] + [f"- {n}" for n in notes if n] + list(sections)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8", errors="replace")

# Bildformate: TIFF ist fast immer ein Scan -> wird vorgeschlagen; der Rest sind meist Fotos -> nur Hinweis.
IMAGE_SCAN = ("tif", "tiff")
IMAGE_MAYBE = ("jpg", "jpeg", "png", "bmp", "gif", "jp2", "webp")
DEFAULT_LANG = "deu+eng"

def count_images(root: Path, images: set, done: set, generated: set):
    """Bilder, die noch nicht eingeplant sind (nicht in --images, nicht erledigt)."""
    c = Counter()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for n in filenames:
            ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
            p = str(Path(dirpath) / n)
            if ext in IMAGE_SCAN + IMAGE_MAYBE and ext not in images and p not in done and p not in generated:
                c[ext] += 1
    return c

def suggest(images: set, lang: str, jobs, found: Counter, planned: int, scan_pdfs=0, fix_scans=False):
    """Vorschlag für den run-Aufruf + Hinweise. jobs=None: Standard."""
    want = set(images) | {e for e in IMAGE_SCAN if found[e]}
    if not planned and not want - set(images):
        return None, ["Nichts zu tun – alles erledigt."]
    cmd = ["ordnig run"]
    if want: cmd.append("--images " + ",".join(sorted(want)))
    if lang != DEFAULT_LANG: cmd.append(f"--lang {lang}")
    if jobs: cmd.append(f"-j {jobs}")
    if fix_scans: cmd.append("--fix-scans")
    hints = []
    scans = sum(found[e] for e in IMAGE_SCAN)
    if scans: hints.append(f"{scans} TIFF-Dateien gefunden → als Scans eingeplant (OCR → PDF/A).")
    maybe = [(e, found[e]) for e in IMAGE_MAYBE if found[e]]
    if maybe:
        lst = ", ".join(f"{n} .{e}" for e, n in maybe)
        hints.append(f"Nicht eingeplant: {lst} – meist Fotos. Falls Scans: --images "
                     + ",".join(sorted(want | {e for e, _ in maybe})))
    if (scans or scan_pdfs) and not fix_scans:
        what = " und ".join(filter(None, [f"{scans} TIFF" if scans else "", f"{scan_pdfs} PDF mit Scan-Seiten" if scan_pdfs else ""]))
        hints.append(f"Scans ({what}): gedreht oder schief? Dann zusätzlich --fix-scans (≈ 40 % mehr Zeit).")
    return " ".join(cmd), hints

KIND_LABEL = {"office": "Office → ODF + PDF/A", "odf": "ODF → PDF/A", "pdf": "PDF → PDF/A + OCR", "image": "Bild → PDF/A + OCR"}


def fmt_dur(sec):
    sec = int(round(sec))
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d} min"

def upgrade_log(log: Path):
    """Ältere Protokolle ohne neue Spalten auf LOG_FIELDS heben (Inhalt bleibt)."""
    with open(log, encoding="utf-8", newline="") as f:
        r = csv.DictReader(f); fields, rows = r.fieldnames or [], list(r)
    if fields == LOG_FIELDS:
        return
    tmp = log.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    tmp.replace(log)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--apply", action="store_true", help="wirklich ausführen (sonst Vorschau)")
    ap.add_argument("--images", default="", help="Bildendungen für OCR-PDF, z. B. tif,tiff,png")
    ap.add_argument("--lang", default=DEFAULT_LANG, help="Tesseract-Sprachen (Standard deu+eng)")
    ap.add_argument("--fix-scans", action="store_true",
                    help="Scans automatisch drehen und gerade richten (≈ 40 %% mehr OCR-Zeit)")
    ap.add_argument("-j", "--jobs", type=int, default=None, help="parallele Dateien (Standard: CPU-Anzahl)")
    ap.add_argument("--log", type=Path, default=None,
                    help="Protokoll (Standard: <root>/.ordnig/migration_log.csv, reist mit der Ablage)")
    ap.add_argument("--report", type=Path, default=None, help="Bericht als Markdown-Baum schreiben")
    args = ap.parse_args()
    jobs_given = args.jobs
    cpus = os.cpu_count() or 1
    args.jobs = args.jobs or cpus
    ocr_jobs = max(1, cpus // args.jobs)          # keine Überbelegung: Dateien × OCR-Threads ≈ CPUs
    root = args.root.resolve()
    if args.log is None:
        args.log = root / ".ordnig" / "migration_log.csv"

    done, generated = set(), set()
    if args.log.exists():
        for r in csv.DictReader(open(args.log, encoding="utf-8")):
            if r["ziel"]: generated.add(r["ziel"])
            if r["aktion"] == "pdfa" and r["status"] in ("ok", "warnung", "übersprungen"):
                done.add(r["quelle"])
    images = {e.strip().lower().lstrip(".") for e in args.images.split(",") if e.strip()}
    progress("Plane …")
    info = {}
    jobs = plan(root, images, done, generated, info)
    progress_done()

    missing = [t for t in ("soffice", "ocrmypdf", "img2pdf") if not shutil.which(t)]
    has_vera = bool(shutil.which("verapdf"))
    vera_note = "" if has_vera else "veraPDF fehlt: PDFs werden als 2b deklariert, nicht 2u."
    rel = lambda p: str(Path(p).relative_to(root))
    already = [("Bereits PDF/A mit Text (bleibt)", len(info["already_pdfa"]))] if info["already_pdfa"] else []

    if not args.apply:
        notes, kinds, blocked = {}, Counter(), 0
        for j in jobs:
            kind, src, ext, odf_t, pdf_t = j
            kinds[kind] += 1
            targets = ([odf_t.name if odf_t else "⊘ ODF (Ziel belegt)"] if kind == "office" else []) + \
                      [pdf_t.name if pdf_t else "⊘ PDF (Ziel belegt)"]
            blocked += (kind == "office" and odf_t is None) + (pdf_t is None)
            notes[rel(src)] = "→ " + " · ".join(targets)
        for p in info["already_pdfa"]:
            notes[rel(p)] = "⊘ bereits PDF/A mit Text"
        table = [(f"  {KIND_LABEL[k]}", kinds[k]) for k in KIND_LABEL if kinds[k]]
        table = [("Geplant", len(jobs))] + table + [None, ("Ziel belegt (übersprungen)", blocked)] + already + \
                [("Bereits erledigt", len(done))]
        found = count_images(root, images, done, generated)
        if found:
            table.append(("Bilder nicht eingeplant", sum(found.values())))
        cmd, hints = suggest(images, args.lang, jobs_given, found, len(jobs), info["scan_pdfs"], args.fix_scans)
        foot = [vera_note, f"Fehlende Programme: {', '.join(missing)}" if missing else ""]
        advice = ([f"Vorschlag: {cmd}", "           (gleicher docker-Aufruf, Ablage ohne :ro – vorher Snapshot)"]
                  if cmd else []) + hints
        write_report(args.report, "Migrationsplan", root, render_tree(root.name, notes), table,
                     foot + [f"Vorschlag: `{cmd}`" if cmd else ""] + hints)
        console_table("Migration (Vorschau)", table,
                      foot + [f"Details: {args.report}" if args.report else ""] + [""] + advice)
        return
    if missing:
        sys.exit(f"Fehlende Programme: {', '.join(missing)}")

    t_start = time.monotonic()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    if args.log.exists():
        upgrade_log(args.log)
    new_log = not args.log.exists()
    results = defaultdict(list)
    with open(args.log, "a", newline="", encoding="utf-8") as lf:
        w = csv.DictWriter(lf, fieldnames=LOG_FIELDS)
        if new_log: w.writeheader()
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(process, j, args.lang, args.fix_scans, ocr_jobs, has_vera): j for j in jobs}
            for i, f in enumerate(as_completed(futs), 1):
                j = futs[f]
                try:
                    rows = f.result()
                except Exception as e:
                    rows = [dict(quelle=str(j[1]), aktion="pdfa", ziel="", status="fehler", pdfa="", hinweis=str(e),
                                 zeit=time.strftime("%Y-%m-%d %H:%M:%S"), sekunden="")]
                for r in rows:
                    w.writerow(r); results[r["quelle"]].append(r)
                lf.flush()
                progress(f"Migriere {i}/{len(jobs)}: {j[1].name}")

        # veraPDF im Stapel: ein JVM-Start je 50 Dateien statt ein bis zwei je Datei
        pending = [r for rows in results.values() for r in rows
                   if r["aktion"] == "pdfa" and r["hinweis"].endswith("veraPDF-Prüfung ausstehend")]
        if pending:
            t_v = time.monotonic()
            verdict = validate_all([Path(r["ziel"]) for r in pending],
                                   lambda i, n: progress(f"veraPDF {i}/{n}"))
            per_file = (time.monotonic() - t_v) / len(pending)
            for r in pending:
                pdfa, vhint = verdict[Path(r["ziel"])]
                r["pdfa"] = pdfa
                r["status"] = "ok" if pdfa != "ungültig" else "warnung"
                r["hinweis"] = "; ".join(filter(None, [r["hinweis"].replace("veraPDF-Prüfung ausstehend", "").strip("; "), vhint]))
                r["sekunden"] = f"{float(r['sekunden'] or 0) + per_file:.1f}"
                r["zeit"] = time.strftime("%Y-%m-%d %H:%M:%S")
                w.writerow(r)      # neuere Zeile gilt
            lf.flush()
    progress_done()
    wall = time.monotonic() - t_start

    c = Counter()
    notes, durations = {}, []
    for src, rows in results.items():
        bits = []
        secs = max((float(r["sekunden"]) for r in rows if r.get("sekunden")), default=0)
        durations.append((secs, rel(src)))
        for r in rows:
            name = Path(r["ziel"]).name if r["ziel"] else r["aktion"].upper()
            if r["status"] == "ok":
                bits.append(f"✓ {name}" + (f" ({r['pdfa']})" if r["pdfa"] else ""))
                c["odf_ok" if r["aktion"] == "odf" else f"pdf_{r['pdfa']}"] += 1
            elif r["status"] == "übersprungen":
                bits.append(f"⊘ {name}: {r['hinweis']}"); c["skip"] += 1
            else:
                bits.append(f"✗ {name}: {r['hinweis'] or r['status']}" + (f" ({r['pdfa']})" if r["pdfa"] else ""))
                c["fail"] += 1
            if r["status"] == "ok" and r["pdfa"] and r["hinweis"] and "bestanden" not in r["hinweis"]:
                bits[-1] += f" – {r['hinweis']}"
        notes[rel(src)] = " · ".join(bits) + f" · {secs:.1f} s"
    for p in info["already_pdfa"]:
        notes[rel(p)] = "⊘ bereits PDF/A mit Text"
    durations.sort(reverse=True)
    table = [("Dateien bearbeitet", len(results)), ("  ODF angelegt", c["odf_ok"]), ("  PDF/A-2u", c["pdf_2u"]),
             ("  PDF/A-2b", c["pdf_2b"]), None, ("Fehler / ungültig", c["fail"]), ("Übersprungen", c["skip"])] + \
            already + [("Bereits erledigt (früher)", len(done)), None, ("Laufzeit", fmt_dur(wall))]
    if results:
        table.append(("  Ø pro Datei (Rechenzeit)", f"{sum(d for d, _ in durations) / len(durations):.1f} s"))
    foot = [vera_note, f"Protokoll: {args.log}",
            f"{args.jobs} Dateien parallel × {ocr_jobs} OCR-Thread(s)" + (" · --fix-scans" if args.fix_scans else "")]
    slow = ["", "### Langsamste Dateien", "", "| Sekunden | Datei |", "|---:|---|"] + \
           [f"| {d:.1f} | `{p}` |" for d, p in durations[:15]] if durations else []
    write_report(args.report, "Migration", root, render_tree(root.name, notes), table, foot, slow)
    console_table("Migration", table, [foot[0], f"Details: {args.report}" if args.report else foot[1]])

if __name__ == "__main__":
    main()
