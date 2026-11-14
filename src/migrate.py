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
LOG_FIELDS = ["quelle", "aktion", "ziel", "status", "pdfa", "hinweis", "zeit"]


# ----------------------------------------------------------------- Hilfen
def run(cmd, timeout=900, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, **kw)
    return r.returncode, (r.stdout + r.stderr)[-1500:]

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

def ocr_pdfa(src_pdf: Path, dst_pdf: Path, lang: str, image_input=False):
    cmd = ["ocrmypdf", "--output-type", "pdfa-2", "-l", lang, "--rotate-pages", "--deskew",
           "--optimize", "1", "-q"]
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
    code, out = run(["verapdf", "--flavour", flavour, str(pdf)], timeout=1800)
    if 'isCompliant="true"' in out or re.search(r"\bPASS\b", out):
        return True
    return False

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
def plan(root: Path, images: set, done: set, generated: set):
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
def process(job, lang):
    kind, src, ext, odf_t, pdf_t = job
    rows = []
    def log(action, target, status, pdfa="", hint=""):
        rows.append(dict(quelle=str(src), aktion=action, ziel=str(target or ""), status=status,
                         pdfa=pdfa, hinweis=hint, zeit=time.strftime("%Y-%m-%d %H:%M:%S")))
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
                hint = ocr_pdfa(pdf_source, tmp_out, lang)
            elif kind == "pdf":
                t = pdf_t
                if not t:
                    log("pdfa", None, "übersprungen", hint="Ziel belegt"); return rows
                tmp_out = work / "out.pdf"
                hint = ocr_pdfa(src, tmp_out, lang)
            else:  # image
                t = pdf_t
                if not t:
                    log("pdfa", None, "übersprungen", hint="Ziel belegt"); return rows
                tmp_in = work / "in.pdf"
                code, out = run(["img2pdf", "-o", str(tmp_in), str(src)])   # auch mehrseitige TIFFs
                if code != 0:
                    raise RuntimeError(f"img2pdf: {out[-300:]}")
                tmp_out = work / "out.pdf"
                hint = ocr_pdfa(tmp_in, tmp_out, lang, image_input=True)
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

def write_report(path, title, root, tree_lines, table, notes):
    if not path:
        return
    L = [f"# {title}: `{root}`", "", f"Stand {time.strftime('%Y-%m-%d %H:%M')}", "",
         "Legende: → wird/wurde angelegt · ✓ erledigt · ✗ Fehler · ⊘ übersprungen. Zusammenfassung am Ende.", "",
         "## Baum", "", "```text"] + tree_lines + ["```", "", "## Zusammenfassung", "", "| | Anzahl |", "|---|---:|"]
    L += [f"| {r[0].strip()} | {r[1]} |" for r in table if r is not None]
    L += [""] + [f"- {n}" for n in notes if n]
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

def suggest(images: set, lang: str, jobs, found: Counter, planned: int):
    """Vorschlag für den run-Aufruf + Hinweise. jobs=None: Standard."""
    want = set(images) | {e for e in IMAGE_SCAN if found[e]}
    if not planned and not want - set(images):
        return None, ["Nichts zu tun – alles erledigt."]
    cmd = ["ordnig run"]
    if want: cmd.append("--images " + ",".join(sorted(want)))
    if lang != DEFAULT_LANG: cmd.append(f"--lang {lang}")
    if jobs: cmd.append(f"-j {jobs}")
    hints = []
    scans = sum(found[e] for e in IMAGE_SCAN)
    if scans: hints.append(f"{scans} TIFF-Dateien gefunden → als Scans eingeplant (OCR → PDF/A).")
    maybe = [(e, found[e]) for e in IMAGE_MAYBE if found[e]]
    if maybe:
        lst = ", ".join(f"{n} .{e}" for e, n in maybe)
        hints.append(f"Nicht eingeplant: {lst} – meist Fotos. Falls Scans: --images "
                     + ",".join(sorted(want | {e for e, _ in maybe})))
    return " ".join(cmd), hints

KIND_LABEL = {"office": "Office → ODF + PDF/A", "odf": "ODF → PDF/A", "pdf": "PDF → PDF/A + OCR", "image": "Bild → PDF/A + OCR"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--apply", action="store_true", help="wirklich ausführen (sonst Vorschau)")
    ap.add_argument("--images", default="", help="Bildendungen für OCR-PDF, z. B. tif,tiff,png")
    ap.add_argument("--lang", default=DEFAULT_LANG, help="Tesseract-Sprachen (Standard deu+eng)")
    ap.add_argument("-j", "--jobs", type=int, default=None, help="parallele Jobs (Standard: halbe CPU-Anzahl)")
    ap.add_argument("--log", type=Path, default=None,
                    help="Protokoll (Standard: <root>/.ordnig/migration_log.csv, reist mit der Ablage)")
    ap.add_argument("--report", type=Path, default=None, help="Bericht als Markdown-Baum schreiben")
    args = ap.parse_args()
    jobs_given = args.jobs
    args.jobs = args.jobs or max(1, (os.cpu_count() or 2) // 2)
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
    jobs = plan(root, images, done, generated)
    progress_done()

    missing = [t for t in ("soffice", "ocrmypdf", "img2pdf") if not shutil.which(t)]
    vera_note = "" if shutil.which("verapdf") else "veraPDF fehlt: PDFs werden als 2b deklariert, nicht 2u."
    rel = lambda p: str(Path(p).relative_to(root))

    if not args.apply:
        notes, kinds, blocked = {}, Counter(), 0
        for j in jobs:
            kind, src, ext, odf_t, pdf_t = j
            kinds[kind] += 1
            targets = ([odf_t.name if odf_t else "⊘ ODF (Ziel belegt)"] if kind == "office" else []) + \
                      [pdf_t.name if pdf_t else "⊘ PDF (Ziel belegt)"]
            blocked += (kind == "office" and odf_t is None) + (pdf_t is None)
            notes[rel(src)] = "→ " + " · ".join(targets)
        table = [(f"  {KIND_LABEL[k]}", kinds[k]) for k in KIND_LABEL if kinds[k]]
        table = [("Geplant", len(jobs))] + table + [None, ("Ziel belegt (übersprungen)", blocked),
                                                    ("Bereits erledigt", len(done))]
        found = count_images(root, images, done, generated)
        if found:
            table.append(("Bilder nicht eingeplant", sum(found.values())))
        cmd, hints = suggest(images, args.lang, jobs_given, found, len(jobs))
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

    args.log.parent.mkdir(parents=True, exist_ok=True)
    new_log = not args.log.exists()
    results = defaultdict(list)
    with open(args.log, "a", newline="", encoding="utf-8") as lf:
        w = csv.DictWriter(lf, fieldnames=LOG_FIELDS)
        if new_log: w.writeheader()
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(process, j, args.lang): j for j in jobs}
            for i, f in enumerate(as_completed(futs), 1):
                j = futs[f]
                try:
                    rows = f.result()
                except Exception as e:
                    rows = [dict(quelle=str(j[1]), aktion="pdfa", ziel="", status="fehler", pdfa="", hinweis=str(e),
                                 zeit=time.strftime("%Y-%m-%d %H:%M:%S"))]
                for r in rows:
                    w.writerow(r); results[r["quelle"]].append(r)
                lf.flush()
                progress(f"Migriere {i}/{len(jobs)}: {j[1].name}")
    progress_done()

    c = Counter()
    notes = {}
    for src, rows in results.items():
        bits = []
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
        notes[rel(src)] = " · ".join(bits)
    table = [("Dateien bearbeitet", len(results)), ("  ODF angelegt", c["odf_ok"]), ("  PDF/A-2u", c["pdf_2u"]),
             ("  PDF/A-2b", c["pdf_2b"]), None, ("Fehler / ungültig", c["fail"]), ("Übersprungen", c["skip"]),
             ("Bereits erledigt (früher)", len(done))]
    foot = [vera_note, f"Protokoll: {args.log}"]
    write_report(args.report, "Migration", root, render_tree(root.name, notes), table, foot)
    console_table("Migration", table, foot[:1] + [f"Details: {args.report}" if args.report else foot[1]])

if __name__ == "__main__":
    main()
