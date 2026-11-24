#!/usr/bin/env python3
"""
migrate.py – puts archival versions next to every file:

  Office file (doc, docx, xls, ppt, rtf, wpd, wps, sxw, …)
      -> ODF counterpart next to it (.odt / .ods / .odp)
      -> PDF/A-2u with OCR text layer next to it
  ODF file (odt, ods, odp)   -> PDF/A-2u next to it
  PDF                        -> <name>_pdfa.pdf (OCR for pages without text, PDF/A-2u)
  Images (only with --images) -> PDF/A-2u with OCR (for scans as TIFF/JPG/PNG)

Originals are NEVER changed or deleted. Existing targets are not overwritten.
Without --apply nothing happens except a preview.

PDF/A-2u is only declared when veraPDF confirms the file as 2u. If the check fails
(e.g. symbol fonts without Unicode mapping), the file stays 2b and the log says so.
Without veraPDF installed: 2b, status "unchecked".

Requirements (Debian/Ubuntu):
  sudo apt install libreoffice ocrmypdf tesseract-ocr-deu ghostscript img2pdf
  veraPDF: https://verapdf.org/software/ (installer, Java) – command `verapdf` on the PATH
  Optional for Fraktur: tesseract-ocr-script-latn or tessdata "deu_latf"

Usage:
  python3 migrate.py /archive                    # preview
  python3 migrate.py /archive --apply            # run
  python3 migrate.py /archive --apply --images tif,tiff --lang deu+eng -j 4
The log is <inbox>/.ordnig/run-log.csv (without inbox <archive>/.ordnig/); repeated runs
skip what is done and continue after errors. Its paths are relative to the archive, so
they survive any mount layout. The inbox is not converted: sort files the mail, the next
run converts it where it landed.
"""
import argparse, csv, os, re, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import Counter, defaultdict
from pathlib import Path
import importlib.util
_spec = importlib.util.spec_from_file_location("state", Path(__file__).resolve().parent / "state.py")
state = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(state)

TEXT = {"doc", "docx", "docm", "dot", "dotx", "rtf", "wpd", "wp", "wps", "wri", "lwp", "sxw", "sdw",
        "pages", "txt"}
CALC = {"xls", "xlsx", "xlsm", "xlt", "xltx", "wks", "wk1", "123", "sxc", "sdc", "numbers", "dbf"}
IMPRESS = {"ppt", "pptx", "pptm", "pps", "ppsx", "pot", "potx", "sxi", "sdd", "key"}
ODF = {"odt": "odt", "ods": "ods", "odp": "odp", "fodt": "odt", "fods": "ods", "fodp": "odp"}
ODF_FOR = {**{e: "odt" for e in TEXT - {"txt"}}, **{e: "ods" for e in CALC - {"dbf"}},
           **{e: "odp" for e in IMPRESS}}
PDF_FILTER = {"odt": "writer_pdf_Export", "ods": "calc_pdf_Export", "odp": "impress_pdf_Export"}
SKIP_DIRS = state.SYSTEM_DIRS
LOG_NAME = state.LOG_NAME
LOG_FIELDS = ["source", "action", "target", "status", "pdfa", "note", "time", "seconds"]
VERA_BATCH = 50


# ----------------------------------------------------------------- Helpers
def run(cmd, timeout=900, tail=1500, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, **kw)
    out = r.stdout + r.stderr
    return r.returncode, out[-tail:] if tail else out

RESERVED = set()

def free_target(src: Path, ext: str, suffix=""):
    """letter.doc -> letter.odt; if taken: letter.doc.odt; if that is taken too: None.
    Targets are reserved while planning so parallel jobs do not collide."""
    for cand in (src.with_name(src.stem + suffix + "." + ext), src.with_name(src.name + suffix + "." + ext)):
        key = str(cand).lower()
        if not cand.exists() and key not in RESERVED:
            RESERVED.add(key)
            return cand
    return None

def soffice(src: Path, fmt: str, workdir: Path, profile: Path):
    """Converts with LibreOffice into a temporary folder, returns the result file."""
    out = workdir / "lo"; out.mkdir(exist_ok=True)
    for f in out.iterdir(): f.unlink()
    code, log = run(["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
                     "--convert-to", fmt, "--outdir", str(out), str(src)])
    res = list(out.iterdir())
    if code != 0 or not res:
        raise RuntimeError(f"LibreOffice: {log.strip()[-300:] or 'no output'}")
    return res[0]

def lo_pdfa_filter(kind):
    # PDF/A-2, tagged, fonts embedded
    opts = '{"SelectPdfVersion":{"type":"long","value":"2"},"UseTaggedPDF":{"type":"boolean","value":"true"}}'
    return f"pdf:{PDF_FILTER[kind]}:{opts}"

def page_has_text(page, depth=0):
    """Text = fonts in the resources of the page or its form XObjects."""
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
    """(pages, pages without text, PDF/A id such as '2u' or ''). Raises for encrypted/broken files."""
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
        cmd += ["--rotate-pages", "--deskew"]   # rotation/skew: ~40 % more time
    cmd += ["--image-dpi", "300"] if image_input else ["--skip-text"]
    code, log = run(cmd + [str(src_pdf), str(dst_pdf)], timeout=3600)
    # 6 = text already there (only without --skip-text), 10 = PDF/A conversion incomplete
    if code not in (0, 10) or not dst_pdf.exists():
        raise RuntimeError(f"ocrmypdf ({code}): {log.strip()[-300:]}")
    return "ocrmypdf: PDF/A only partly reached" if code == 10 else ""

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
    """veraPDF XML (several jobs) -> {file name: compliant?}."""
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
    """One JVM start for many files. Falls back to single calls if the output cannot be read."""
    if not pdfs:
        return {}, True
    code, out = run(["verapdf", "--flavour", flavour, *map(str, pdfs)], timeout=3600, tail=None)
    try:
        res = parse_verapdf(out)
        if all(p.name in res for p in pdfs):
            return {p: res[p.name] for p in pdfs}, True
    except Exception:
        pass
    # output not readable: check one by one and say so in the log (no silent fallback)
    return {p: bool(verapdf(p, flavour)) for p in pdfs}, False

def validate_all(targets, progress_cb=lambda i, n: None):
    """targets: finished PDFs (declared 2b). Checks copies for 2u, then 2b if needed, and replaces
    the target only with exactly the checked copy. -> {target: (pdfa, note)}"""
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
            note = "" if batch_u and batch_b else " (single checks: veraPDF batch output not readable)"
            for c, t in copies.items():
                if ok_u[c]:
                    st = t.stat(); shutil.copyfile(c, t); os.utime(t, (st.st_atime, st.st_mtime))
                    results[t] = ("2u", PASSED + note)
                elif ok_b[c]:
                    results[t] = ("2b", "2u not met, 2b passed" + note)
                else:
                    results[t] = ("invalid", "veraPDF: neither 2u nor 2b" + note)
    return results

def finalize_pdfa(pdf: Path):
    """Declares 2u if veraPDF agrees, else 2b. Returns (pdfa, note)."""
    set_conformance(pdf, "U")
    ok = verapdf(pdf, "2u")
    if ok:
        return "2u", PASSED
    set_conformance(pdf, "B")
    if ok is None:
        return "2b", "unchecked (veraPDF missing) – 2u not declared"
    ok_b = verapdf(pdf, "2b")
    return ("2b", "2u not met, 2b passed") if ok_b else ("invalid", "veraPDF: neither 2u nor 2b")


# ----------------------------------------------------------------- Planning
def walk(root: Path, skip=()):
    """os.walk without our own/system folders and without skip (absolute paths, e.g. the inbox)."""
    skip = {Path(s) for s in skip}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and Path(dirpath) / d not in skip)
        yield dirpath, dirnames, filenames

def plan(root: Path, images: set, done: set, generated: set, info=None, skip=()):
    """info (dict, optional) collects: already_pdfa (list), scan_pdfs (count).
    skip: folders that are not converted (the inbox: sort files the mail first)."""
    info = info if info is not None else {}
    info.setdefault("already_pdfa", []); info.setdefault("scan_pdfs", 0)
    jobs = []
    for dirpath, dirnames, filenames in walk(root, skip):
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
                        pages, no_text, pdfa = 0, 0, ""      # encrypted/broken: the job reports the error
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
    taken = "(target taken, skipped)"
    acts = []
    if kind == "office":
        acts.append(f"ODF   -> {odf_t.name if odf_t else taken}")
    label = {"pdf": "PDF/A+OCR", "image": "OCR-PDF/A"}.get(kind, "PDF/A")
    acts.append(f"{label} -> {pdf_t.name if pdf_t else taken}")
    return acts


# ----------------------------------------------------------------- Running (worker)
def process(job, lang, fix_scans=False, ocr_jobs=1, defer_validation=False):
    kind, src, ext, odf_t, pdf_t = job
    rows = []
    t0 = time.monotonic()
    def log(action, target, status, pdfa="", hint=""):
        rows.append(dict(source=str(src), action=action, target=str(target or ""), status=status,
                         pdfa=pdfa, note=hint, time=time.strftime("%Y-%m-%d %H:%M:%S"),
                         seconds=f"{time.monotonic() - t0:.1f}"))
    with tempfile.TemporaryDirectory(prefix="mig_") as td:
        work = Path(td); profile = work / "profile"
        mtime = src.stat().st_mtime
        def place(tmp: Path, target: Path):
            shutil.move(str(tmp), target)
            os.utime(target, (mtime, mtime))          # keep the date of the original
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
                        log("odf", t, "error", hint=str(e))
                else:
                    log("odf", None, "skipped", hint="target taken")
                kind_odf = odf
            elif kind == "odf":
                kind_odf = ODF[ext]
            if kind in ("office", "odf"):
                t = pdf_t
                if not t:
                    log("pdfa", None, "skipped", hint="target taken"); return rows
                pdf_source = soffice(src, lo_pdfa_filter(kind_odf), work, profile)
                tmp_out = work / "out.pdf"
                _, no_text, _ = inspect_pdf(pdf_source)
                if no_text:      # e.g. an embedded scan: OCR needed (tagging gets lost)
                    hint = ocr_pdfa(pdf_source, tmp_out, lang, fix_scans=fix_scans, ocr_jobs=ocr_jobs)
                    hint = "; ".join(filter(None, [hint, f"OCR for {no_text} page(s) without text"]))
                else:            # LibreOffice PDF/A directly: faster, keeps tagging
                    shutil.move(str(pdf_source), tmp_out); hint = ""
            elif kind == "pdf":
                t = pdf_t
                if not t:
                    log("pdfa", None, "skipped", hint="target taken"); return rows
                tmp_out = work / "out.pdf"
                hint = ocr_pdfa(src, tmp_out, lang, fix_scans=fix_scans, ocr_jobs=ocr_jobs)
            else:  # image
                t = pdf_t
                if not t:
                    log("pdfa", None, "skipped", hint="target taken"); return rows
                tmp_in = work / "in.pdf"
                code, out = run(["img2pdf", "-o", str(tmp_in), str(src)])   # also multi-page TIFFs
                if code != 0:
                    raise RuntimeError(f"img2pdf: {out[-300:]}")
                tmp_out = work / "out.pdf"
                hint = ocr_pdfa(tmp_in, tmp_out, lang, image_input=True, fix_scans=fix_scans, ocr_jobs=ocr_jobs)
            if defer_validation:     # veraPDF later in a batch; until then honestly 2b
                set_conformance(tmp_out, "B")
                pdfa, vhint = "2b", PENDING
            else:
                pdfa, vhint = finalize_pdfa(tmp_out)
            place(tmp_out, t)
            log("pdfa", t, "ok" if pdfa != "invalid" else "warning", pdfa, "; ".join(filter(None, [hint, vhint])))
        except Exception as e:
            msg = str(e)
            if "encrypt" in msg.lower(): msg = "PDF encrypted – please unlock it by hand"
            log("pdfa", None, "error", hint=msg)
    return rows


# ----------------------------------------------------------------- Output
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
    """notes: {relpath: text} -> tree lines, folders with the number of files concerned."""
    tree = {}
    for rel, note in notes.items():
        n = tree
        parts = Path(rel).parts
        for part in parts[:-1]:
            n = n.setdefault(part + "/", {})
        n[parts[-1]] = note
    def count(n):
        return sum(count(v) if isinstance(v, dict) else 1 for v in n.values())
    out = [f"{root_name}/  —  {count(tree)} files"]
    def walk(n, prefix):
        keys = sorted(n, key=lambda k: (not k.endswith("/"), k.lower()))
        for i, k in enumerate(keys):
            branch, cont = ("└── ", "    ") if i == len(keys) - 1 else ("├── ", "│   ")
            v = n[k]
            if isinstance(v, dict):
                c = count(v)
                out.append(f"{prefix}{branch}{k}  —  {c} {'file' if c == 1 else 'files'}")
                walk(v, prefix + cont)
            else:
                out.append(f"{prefix}{branch}{k}  {v}")
    walk(tree, "")
    return out

def write_report(path, title, root, tree_lines, table, notes, sections=()):
    if not path:
        return
    L = [f"# {title}: `{root}`", "", f"As of {time.strftime('%Y-%m-%d %H:%M')}", "",
         "Legend: → will be/was created · ✓ done · ✗ error · ⊘ skipped. Summary at the end.", "",
         "## Tree", "", "```text"] + tree_lines + ["```", "", "## Summary", "", "| | Count |", "|---|---:|"]
    L += [f"| {r[0].strip()} | {r[1]} |" for r in table if r is not None]
    L += [""] + [f"- {n}" for n in notes if n] + list(sections)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8", errors="replace")

# Image formats: TIFF is almost always a scan -> suggested; the rest are mostly photos -> only a hint.
IMAGE_SCAN = ("tif", "tiff")
IMAGE_MAYBE = ("jpg", "jpeg", "png", "bmp", "gif", "jp2", "webp")
DEFAULT_LANG = "deu+eng"

def count_images(root: Path, images: set, done: set, generated: set, skip=()):
    """Images that are not planned yet (not in --images, not done)."""
    c = Counter()
    for dirpath, dirnames, filenames in walk(root, skip):
        for n in filenames:
            ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
            p = str(Path(dirpath) / n)
            if ext in IMAGE_SCAN + IMAGE_MAYBE and ext not in images and p not in done and p not in generated:
                c[ext] += 1
    return c

def suggest(images: set, lang: str, jobs, found: Counter, planned: int, scan_pdfs=0, fix_scans=False):
    """Suggested run call + hints. jobs=None: default."""
    want = set(images) | {e for e in IMAGE_SCAN if found[e]}
    if not planned and not want - set(images):
        return None, ["Nothing to do – all done."]
    cmd = ["ordnig run"]
    if want: cmd.append("--images " + ",".join(sorted(want)))
    if lang != DEFAULT_LANG: cmd.append(f"--lang {lang}")
    if jobs: cmd.append(f"-j {jobs}")
    if fix_scans: cmd.append("--fix-scans")
    hints = []
    scans = sum(found[e] for e in IMAGE_SCAN)
    if scans: hints.append(f"{scans} TIFF files found → planned as scans (OCR → PDF/A).")
    maybe = [(e, found[e]) for e in IMAGE_MAYBE if found[e]]
    if maybe:
        lst = ", ".join(f"{n} .{e}" for e, n in maybe)
        hints.append(f"Not planned: {lst} – mostly photos. If they are scans: --images "
                     + ",".join(sorted(want | {e for e, _ in maybe})))
    if (scans or scan_pdfs) and not fix_scans:
        what = " and ".join(filter(None, [f"{scans} TIFF" if scans else "", f"{scan_pdfs} PDF with scanned pages" if scan_pdfs else ""]))
        hints.append(f"Scans ({what}): rotated or skewed? Then also --fix-scans (≈ 40 % more time).")
    return " ".join(cmd), hints

KIND_LABEL = {"office": "Office → ODF + PDF/A", "odf": "ODF → PDF/A", "pdf": "PDF → PDF/A + OCR", "image": "Image → PDF/A + OCR"}
PASSED = "veraPDF passed"
PENDING = "veraPDF check pending"


def fmt_dur(sec):
    sec = int(round(sec))
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d} min"

def check_log(log: Path):
    """A log with other columns is not migrated (before production): stop."""
    if not log.is_file():
        return
    with open(log, encoding="utf-8", newline="", errors="surrogateescape") as f:
        head = next(csv.reader(f), [])
    if head != LOG_FIELDS:
        state.stop(f"{log}: columns of another ordnig version ({', '.join(head)}). Move it away; run starts a new one.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--apply", action="store_true", help="really run (otherwise preview)")
    ap.add_argument("--images", default="", help="image extensions for OCR PDF, e.g. tif,tiff,png")
    ap.add_argument("--lang", default=DEFAULT_LANG, help="Tesseract languages (default deu+eng)")
    ap.add_argument("--fix-scans", action="store_true",
                    help="rotate and deskew scans automatically (≈ 40 %% more OCR time)")
    ap.add_argument("-j", "--jobs", type=int, default=None, help="files in parallel (default: number of CPUs)")
    ap.add_argument("--inbox", type=Path, default=None,
                    help="inbox folder; it holds .ordnig/ (default: the folder holding .ordnig/, else <root>/Inbox)")
    ap.add_argument("--log", type=Path, default=None,
                    help="log (default: <inbox>/.ordnig/run-log.csv)")
    ap.add_argument("--report", type=Path, default=None, help="write a Markdown tree report")
    args = ap.parse_args()
    jobs_given = args.jobs
    cpus = os.cpu_count() or 1
    args.jobs = args.jobs or cpus
    ocr_jobs = max(1, cpus // args.jobs)          # no oversubscription: files × OCR threads ≈ CPUs
    root = args.root.resolve()
    if args.log is None:
        args.log = state.state_dir(root, args.inbox) / LOG_NAME

    done, generated = set(), set()
    if args.log.exists():
        check_log(args.log)
        for r in csv.DictReader(open(args.log, encoding="utf-8")):
            if r["target"]: generated.add(state.to_abs(r["target"], root))
            if r["action"] == "pdfa" and r["status"] in ("ok", "warning", "skipped"):
                done.add(state.to_abs(r["source"], root))
    images = {e.strip().lower().lstrip(".") for e in args.images.split(",") if e.strip()}
    progress("Planning …")
    info = {}
    inbox = state.find_inbox(root, args.inbox)
    skip = [inbox] if inbox else []
    jobs = plan(root, images, done, generated, info, skip)
    progress_done()

    missing = [t for t in ("soffice", "ocrmypdf", "img2pdf") if not shutil.which(t)]
    has_vera = bool(shutil.which("verapdf"))
    vera_note = "" if has_vera else "veraPDF missing: PDFs are declared 2b, not 2u."
    rel = lambda p: str(Path(p).relative_to(root))
    already = [("Already PDF/A with text (stays)", len(info["already_pdfa"]))] if info["already_pdfa"] else []

    if not args.apply:
        notes, kinds, blocked = {}, Counter(), 0
        for j in jobs:
            kind, src, ext, odf_t, pdf_t = j
            kinds[kind] += 1
            targets = ([odf_t.name if odf_t else "⊘ ODF (target taken)"] if kind == "office" else []) + \
                      [pdf_t.name if pdf_t else "⊘ PDF (target taken)"]
            blocked += (kind == "office" and odf_t is None) + (pdf_t is None)
            notes[rel(src)] = "→ " + " · ".join(targets)
        for p in info["already_pdfa"]:
            notes[rel(p)] = "⊘ already PDF/A with text"
        table = [(f"  {KIND_LABEL[k]}", kinds[k]) for k in KIND_LABEL if kinds[k]]
        table = [("Planned", len(jobs))] + table + [None, ("Target taken (skipped)", blocked)] + already + \
                [("Already done", len(done))]
        found = count_images(root, images, done, generated, skip)
        if found:
            table.append(("Images not planned", sum(found.values())))
        cmd, hints = suggest(images, args.lang, jobs_given, found, len(jobs), info["scan_pdfs"], args.fix_scans)
        foot = [vera_note, f"Missing tools: {', '.join(missing)}" if missing else "",
                f"Inbox left out: {inbox} (converted after filing)" if inbox else ""]
        advice = ([f"Suggestion: {cmd}", "            (same docker call, archive without :ro – take a snapshot first)"]
                  if cmd else []) + hints
        write_report(args.report, "Conversion plan", root, render_tree(root.name, notes), table,
                     foot + [f"Suggestion: `{cmd}`" if cmd else ""] + hints)
        console_table("Conversion (preview)", table,
                      foot + [f"Details: {args.report}" if args.report else ""] + [""] + advice)
        return
    if missing:
        sys.exit(f"Missing tools: {', '.join(missing)}")

    t_start = time.monotonic()
    state.open_state(args.log.parent, root)
    new_log = not args.log.exists()
    results = defaultdict(list)
    with open(args.log, "a", newline="", encoding="utf-8") as lf:
        w = csv.DictWriter(lf, fieldnames=LOG_FIELDS)
        if new_log: w.writeheader()
        put = lambda r: w.writerow({**r, **{k: state.to_rel(r[k], root) for k in state.LOG_PATHS}})
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(process, j, args.lang, args.fix_scans, ocr_jobs, has_vera): j for j in jobs}
            for i, f in enumerate(as_completed(futs), 1):
                j = futs[f]
                try:
                    rows = f.result()
                except Exception as e:
                    rows = [dict(source=str(j[1]), action="pdfa", target="", status="error", pdfa="", note=str(e),
                                 time=time.strftime("%Y-%m-%d %H:%M:%S"), seconds="")]
                for r in rows:
                    put(r); results[r["source"]].append(r)
                lf.flush()
                progress(f"Converting {i}/{len(jobs)}: {j[1].name}")

        # veraPDF in batches: one JVM start per 50 files instead of one or two per file
        pending = [r for rows in results.values() for r in rows
                   if r["action"] == "pdfa" and r["note"].endswith(PENDING)]
        if pending:
            t_v = time.monotonic()
            verdict = validate_all([Path(r["target"]) for r in pending],
                                   lambda i, n: progress(f"veraPDF {i}/{n}"))
            per_file = (time.monotonic() - t_v) / len(pending)
            for r in pending:
                pdfa, vhint = verdict[Path(r["target"])]
                r["pdfa"] = pdfa
                r["status"] = "ok" if pdfa != "invalid" else "warning"
                r["note"] = "; ".join(filter(None, [r["note"].replace(PENDING, "").strip("; "), vhint]))
                r["seconds"] = f"{float(r['seconds'] or 0) + per_file:.1f}"
                r["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                put(r)      # the newer row wins
            lf.flush()
    progress_done()
    wall = time.monotonic() - t_start

    c = Counter()
    notes, durations = {}, []
    for src, rows in results.items():
        bits = []
        secs = max((float(r["seconds"]) for r in rows if r.get("seconds")), default=0)
        durations.append((secs, rel(src)))
        for r in rows:
            name = Path(r["target"]).name if r["target"] else r["action"].upper()
            if r["status"] == "ok":
                bits.append(f"✓ {name}" + (f" ({r['pdfa']})" if r["pdfa"] else ""))
                c["odf_ok" if r["action"] == "odf" else f"pdf_{r['pdfa']}"] += 1
            elif r["status"] == "skipped":
                bits.append(f"⊘ {name}: {r['note']}"); c["skip"] += 1
            else:
                bits.append(f"✗ {name}: {r['note'] or r['status']}" + (f" ({r['pdfa']})" if r["pdfa"] else ""))
                c["fail"] += 1
            if r["status"] == "ok" and r["pdfa"] and r["note"] and "passed" not in r["note"]:
                bits[-1] += f" – {r['note']}"
        notes[rel(src)] = " · ".join(bits) + f" · {secs:.1f} s"
    for p in info["already_pdfa"]:
        notes[rel(p)] = "⊘ already PDF/A with text"
    durations.sort(reverse=True)
    table = [("Files processed", len(results)), ("  ODF created", c["odf_ok"]), ("  PDF/A-2u", c["pdf_2u"]),
             ("  PDF/A-2b", c["pdf_2b"]), None, ("Errors / invalid", c["fail"]), ("Skipped", c["skip"])] + \
            already + [("Already done (earlier)", len(done)), None, ("Run time", fmt_dur(wall))]
    if results:
        table.append(("  Ø per file (compute time)", f"{sum(d for d, _ in durations) / len(durations):.1f} s"))
    foot = [vera_note, f"Log: {args.log}",
            f"{args.jobs} files in parallel × {ocr_jobs} OCR thread(s)" + (" · --fix-scans" if args.fix_scans else "")]
    slow = ["", "### Slowest files", "", "| Seconds | File |", "|---:|---|"] + \
           [f"| {d:.1f} | `{p}` |" for d, p in durations[:15]] if durations else []
    write_report(args.report, "Conversion", root, render_tree(root.name, notes), table, foot, slow)
    console_table("Conversion", table, [foot[0], f"Details: {args.report}" if args.report else foot[1]])
    if args.report:
        shutil.copy2(args.log, args.report.with_name(args.report.stem + "_log.csv"))
    return state.ERROR if c["fail"] else state.OK

if __name__ == "__main__":
    state.run(main)
