#!/usr/bin/env python3
"""
sort.py – files incoming mail from the inbox into the archive.

For every file in the inbox (default: <archive>/Inbox):
  1. Read the text: PDF (no text: OCR), image (OCR), Office/ODF, e-mail (.eml), text.
  2. Rules from <inbox>/.ordnig/rules.toml: the first match wins.
  3. Otherwise the most similar documents of the archive vote (k nearest neighbours, TF-IDF).
     It learns from the archive itself: every folder is a class, year folders
     (2019, 2020 …) become {year}; the year comes from the newest date in the document.
  4. Unsure, unreadable, no date for {year} or already filed: <inbox>/_Review/ (configurable).
  Rules can also make copies (copy = "…/0_Tax/{year}"), e.g. for taxes.

Without --apply only a preview. --eval measures on the archive itself how well this works.
Everything that stays between runs lives in <inbox>/.ordnig/ (the container may run on any
machine): rules.toml, texts.json.gz (texts read from the archive = what was learned),
sort-log.csv (every filing with --apply), version.json (format version) and lock (one run
at a time). If a share is not mounted, sort stops instead of dropping what it learned.
Exit codes: see state.py (0 ok, 1 file errors, 2 call/setup, 3 locked, 4 review folder not empty).
Files are moved, never overwritten or deleted; across drives they are first copied and
compared by SHA-256, then the original is removed.

Usage:
  python3 sort.py /archive                      # preview
  python3 sort.py /archive --apply              # file
  python3 sort.py /archive --eval               # measure accuracy on the archive
"""
import argparse, csv, email, multiprocessing, gzip, hashlib, heapq, html, importlib.util, json, math, os, random, re, \
    shutil, subprocess, sys, tempfile, time, tomllib, unicodedata, zipfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from email import policy
from pathlib import Path

def _load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod
migrate, state = _load("migrate"), _load("state")

REVIEW = "_Review"        # default; [folders] review in rules.toml
STATE = ".ordnig"
SKIP_DIRS = state.SYSTEM_DIRS
YEAR_DIR = re.compile(r"(19|20)\d\d")
YEAR = "{year}"
MIN_WORDS = 8            # fewer words: no usable text
MAX_CHARS = 8000         # read and stored per document (letterhead, subject, first pages)
PAGES = 3                # pages read from PDFs
TOP_TERMS = 60           # strongest terms per document in the index
DEFAULTS = {"k": 7, "share": 0.7, "similarity": 0.2}
SHRINK_MIN = 20          # mount guard: from this many known documents per area …
SHRINK_KEEP = 0.5        # … at least this share must still be there

IMAGES = {"jpg", "jpeg", "png", "tif", "tiff", "bmp", "gif", "webp", "jp2", "pnm"}
PLAIN = {"txt", "md", "csv", "tsv", "xml", "json"}
HTML_EXT = {"html", "htm"}
ODF_EXT = {"odt", "ods", "odp", "fodt", "fods", "fodp"}
OOXML = {"docx": ("word/document.xml",), "docm": ("word/document.xml",),
         "xlsx": ("xl/sharedStrings.xml",), "xlsm": ("xl/sharedStrings.xml",),
         "pptx": ("ppt/slides/",)}
LEGACY = set(migrate.ODF_FOR) - set(OOXML)          # doc, xls, rtf, wpd … -> LibreOffice
# Preferred source per document group: ordnig's archival copies have OCR text.
PREFER = ["_pdfa.pdf", ".pdf", ".odt", ".ods", ".odp", ".docx", ".xlsx", ".pptx", ".eml", ".txt"]


class Unreadable(Exception):
    """File cannot be read; the reason is in the message."""


# ----------------------------------------------------------------- Configuration
def load_config(path: Path, root: Path):
    """Reads rules.toml strictly: unknown keys, broken patterns, missing targets are errors."""
    cfg = {"ignore": [], "thresholds": dict(DEFAULTS), "year": {}, "rules": [], "review": REVIEW}
    if not path.exists():
        return cfg
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise SystemExit(f"{path}: {e}")
    errors = []
    unknown = set(raw) - {"ignore", "thresholds", "year", "folders", "rule"}
    if unknown:
        errors.append(f"unknown keys: {', '.join(sorted(unknown))}")
    ign = raw.get("ignore", [])
    if not (isinstance(ign, list) and all(isinstance(x, str) for x in ign)):
        errors.append("ignore: list of folders expected")
    else:
        cfg["ignore"] = [x.strip("/") for x in ign]
        errors += [f"ignore: folder missing: {x}" for x in cfg["ignore"] if not (root / x).is_dir()]
    folders = raw.get("folders", {})
    for key, val in folders.items():
        if key != "review":
            errors.append(f"folders: unknown key {key}")
        elif not (isinstance(val, str) and val.strip() and "/" not in val and not val.startswith(".")):
            errors.append("folders.review: folder name expected (no / and no leading dot)")
        else:
            cfg["review"] = val.strip()
    th = raw.get("thresholds", {})
    for key, val in th.items():
        if key not in DEFAULTS:
            errors.append(f"thresholds: unknown key {key}")
        elif key == "k" and not (isinstance(val, int) and val >= 1):
            errors.append("thresholds.k: whole number ≥ 1 expected")
        elif key != "k" and not (isinstance(val, (int, float)) and 0 <= val <= 1):
            errors.append(f"thresholds.{key}: number between 0 and 1 expected")
        else:
            cfg["thresholds"][key] = val
    for target, pattern in raw.get("year", {}).items():
        if YEAR not in target:
            errors.append(f"year: {target} contains no {YEAR}")
        rx = _compile(pattern, f"year.{target}", errors, need_group=True)
        if rx:
            cfg["year"][target.strip("/")] = rx
        errors += _check_target(root, target, f"year.{target}")
    rules = raw.get("rule", [])
    if not isinstance(rules, list):
        errors.append("rule: [[rule]] entries expected")
        rules = []
    for i, r in enumerate(rules, 1):
        where = f"rule {i}" + (f" ({r['name']})" if isinstance(r.get("name"), str) else "")
        unknown = set(r) - {"name", "target", "any", "all", "none", "year", "copy", "copy_year"}
        if unknown:
            errors.append(f"{where}: unknown keys: {', '.join(sorted(unknown))}")
        target = r.get("target")
        if not isinstance(target, str) or not target.strip("/"):
            errors.append(f"{where}: target missing"); continue
        errors += _check_target(root, target, where)
        rule = {"name": r.get("name") or target, "target": target.strip("/"), "year": None}
        for key in ("any", "all", "none"):
            vals = r.get(key, [])
            vals = [vals] if isinstance(vals, str) else vals
            if not (isinstance(vals, list) and all(isinstance(v, str) for v in vals)):
                errors.append(f"{where}: {key} must be a text or a list of texts"); vals = []
            rule[key] = [rx for v in vals if (rx := _compile(v, f"{where}.{key}", errors))]
        if not rule["any"] and not rule["all"]:
            errors.append(f"{where}: any or all needed")
        if "year" in r:
            if YEAR not in rule["target"]:
                errors.append(f"{where}: year set, but target contains no {YEAR}")
            rule["year"] = _compile(r["year"], f"{where}.year", errors, need_group=True)
        copy = r.get("copy", [])
        copy = [copy] if isinstance(copy, str) else copy
        if not (isinstance(copy, list) and all(isinstance(k, str) and k.strip("/") for k in copy)):
            errors.append(f"{where}: copy must be a folder or a list of folders"); copy = []
        for k in copy:
            errors += _check_target(root, k, f"{where}.copy")
        rule["copy"] = [k.strip("/") for k in copy if k.strip("/") != rule["target"]]
        rule["copy_year"] = None
        if "copy_year" in r:
            if not any(YEAR in k for k in rule["copy"]):
                errors.append(f"{where}: copy_year set, but no copy with {YEAR}")
            rule["copy_year"] = _compile(r["copy_year"], f"{where}.copy_year", errors, need_group=True)
        cfg["rules"].append(rule)
    if errors:
        raise SystemExit(f"{path}:\n  " + "\n  ".join(errors))
    return cfg

def _compile(pattern, where, errors, need_group=False):
    if not isinstance(pattern, str):
        errors.append(f"{where}: pattern must be a text"); return None
    try:
        rx = re.compile(pattern, re.I | re.M)
    except re.error as e:
        errors.append(f"{where}: invalid pattern {pattern!r}: {e}"); return None
    if need_group and rx.groups < 1:
        errors.append(f"{where}: pattern needs a group for the year, e.g. (\\d{{4}})"); return None
    return rx

def _check_target(root: Path, target: str, where: str):
    """The target must exist; with {year} the folder above it (the year is created)."""
    target = target.strip("/")
    base = target.split("/" + YEAR)[0] if YEAR in target else target
    if YEAR in target and not target.endswith("/" + YEAR):
        return [f"{where}: {YEAR} only possible as the last folder: {target}"]
    return [] if (root / base).is_dir() else [f"{where}: folder missing: {base}"]


# ----------------------------------------------------------------- Reading text
def _run(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise Unreadable(f"{cmd[0]}: {(r.stderr or r.stdout).strip()[-200:] or f'code {r.returncode}'}")
    return r.stdout

def words(text):
    return len(re.findall(r"[^\W\d_]{2,}", text))

def pdf_text(p: Path):
    return _run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=txtwrite",
                 "-dFirstPage=1", f"-dLastPage={PAGES}", "-o", "-", str(p)])

def ocr_pdf(p: Path, lang: str):
    with tempfile.TemporaryDirectory(prefix="sort_") as td:
        _run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pnggray", "-r300",
              "-dFirstPage=1", f"-dLastPage={PAGES}", "-o", f"{td}/s%03d.png", str(p)])
        return "\n".join(ocr_image(f, lang) for f in sorted(Path(td).glob("s*.png")))

def ocr_image(p: Path, lang: str):
    return _run(["tesseract", str(p), "stdout", "-l", lang, "--psm", "3"], timeout=900)

def xml_text(data: bytes):
    t = re.sub(rb"<(text:p|text:h|w:p|a:p|table:table-row)\b", rb"\n<\1", data)
    t = re.sub(rb"<[^>]+>", b" ", t)
    return html.unescape(t.decode("utf-8", "replace"))

def zip_text(p: Path, ext: str):
    try:
        with zipfile.ZipFile(p) as z:
            if ext in ODF_EXT:
                names = ["content.xml"]
            else:
                pre = OOXML[ext]
                names = sorted(n for n in z.namelist() if n.startswith(pre[0]) and n.endswith(".xml")) \
                    if pre[0].endswith("/") else list(pre)
            return "\n".join(xml_text(z.read(n)) for n in names)
    except (zipfile.BadZipFile, KeyError) as e:
        raise Unreadable(f"broken .{ext} file: {e}")

def flat_odf_text(p: Path):
    return xml_text(p.read_bytes())

def decode(data: bytes):
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")

def eml_text(p: Path):
    msg = email.message_from_bytes(p.read_bytes(), policy=policy.default)
    head = f"{msg.get('subject', '')}\n{msg.get('from', '')}\n{msg.get('date', '')}"
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body and body.get_content_type() == "text/html":
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return head + "\n" + text

def legacy_text(p: Path):
    with tempfile.TemporaryDirectory(prefix="sort_") as td:
        try:
            pdf = migrate.soffice(p, "pdf", Path(td), Path(td) / "profile")
        except RuntimeError as e:
            raise Unreadable(str(e))
        return pdf_text(pdf)

def extract(p: Path, lang: str, ocr=True):
    """Text of a file. ocr=False when learning (too slow for the whole archive)."""
    ext = p.suffix.lower().lstrip(".")
    if ext == "pdf":
        text = pdf_text(p)
        if words(text) < MIN_WORDS and ocr:
            text = ocr_pdf(p, lang)
    elif ext in IMAGES:
        if not ocr:
            return ""
        text = ocr_image(p, lang)
    elif ext in ODF_EXT:
        text = flat_odf_text(p) if ext.startswith("f") else zip_text(p, ext)
    elif ext in OOXML:
        text = zip_text(p, ext)
    elif ext == "eml":
        text = eml_text(p)
    elif ext in PLAIN:
        text = decode(p.read_bytes()[:200_000])
    elif ext in HTML_EXT:
        text = html.unescape(re.sub(r"<[^>]+>", " ", decode(p.read_bytes()[:500_000])))
    elif ext in LEGACY:
        if not ocr:
            return ""        # when learning, ordnig's ODF/PDF copy provides the text
        text = legacy_text(p)
    else:
        raise Unreadable(f"format .{ext or '(no extension)'} is not read")
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]


# ----------------------------------------------------------------- Features
TOKEN = re.compile(r"[^\W_]+")

def tokens(text):
    """Words of 3+ characters; numbers only with 5+ digits (customer/contract numbers, not amounts/dates)."""
    out = []
    for w in TOKEN.findall(unicodedata.normalize("NFKC", text).casefold()):
        if w.isdigit():
            if len(w) >= 5:
                out.append(w)
        elif 3 <= len(w) <= 40:
            out.append(w)
    return out

# German month names: dates in German letters
MONTHS = {"januar": 1, "jänner": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3,
          "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "august": 8, "aug": 8,
          "september": 9, "sep": 9, "sept": 9, "oktober": 10, "okt": 10, "november": 11, "nov": 11,
          "dezember": 12, "dez": 12}
D_DOT = re.compile(r"(?<![\d.])(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})(?![\d.])")
D_DOT2 = re.compile(r"(?<![\d.])(\d{2})\.(\d{2})\.(\d{2})(?![\d.])")
D_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
D_WORD = re.compile(r"(?<!\d)(\d{1,2})\.?\s+(" + "|".join(sorted(MONTHS, key=len, reverse=True)) +
                    r")\.?\s+(\d{4})(?!\d)", re.I)

def dates(text, today: date):
    found = []
    def add(y, m, d):
        try:
            dt = date(y, m, d)
        except ValueError:
            return
        if 1950 <= y and dt <= today:
            found.append(dt)
    for d, m, y in D_DOT.findall(text):
        add(int(y), int(m), int(d))
    for d, m, y in D_DOT2.findall(text):
        yy = int(y); add(2000 + yy if 2000 + yy <= today.year else 1900 + yy, int(m), int(d))
    for y, m, d in D_ISO.findall(text):
        add(int(y), int(m), int(d))
    for d, mon, y in D_WORD.findall(text):
        add(int(y), MONTHS[mon.casefold()], int(d))
    return found

def year_of(text, today: date, pattern=None):
    """Year for filing: group 1 of the pattern, else the newest date up to today."""
    if pattern is not None:
        m = pattern.search(text)
        if not m:
            return None
        g = m.group(1)
        y = int(g) if g and g.isdigit() else None
        return y if y and 1950 <= y <= today.year else None
    ds = dates(text, today)
    return max(ds).year if ds else None


# ----------------------------------------------------------------- Learning from the archive
def label_of(rel_dir: str):
    """Folder relative to the archive -> class; year folders become {year}."""
    return "/".join(YEAR if YEAR_DIR.fullmatch(part) else part for part in rel_dir.split("/"))

def group_key(name: str):
    """x.doc, x.odt, x.pdf, x_pdfa.pdf, x.doc.odt -> 'x' (one document, several versions)."""
    stem = name.lower()
    known = migrate.ODF_FOR.keys() | ODF_EXT | IMAGES | {"pdf", "txt", "eml", "html", "htm"}
    while "." in stem and stem.rsplit(".", 1)[1] in known:
        stem = stem.rsplit(".", 1)[0]
    return stem[:-5] if stem.endswith("_pdfa") else stem

def preference(name: str):
    n = name.lower()
    for i, suffix in enumerate(PREFER):
        if n.endswith(suffix):
            return i
    return len(PREFER)

def walk_archive(root: Path, inbox: Path, ignore):
    """{(folder, group): [paths]} of all documents usable as examples."""
    ignore_abs = [root / x for x in ignore]
    groups = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames if n not in SKIP_DIRS and not n.startswith((".", "#", "@"))
                             and d / n != inbox and d / n not in ignore_abs)
        if d == root:
            continue                                # loose files at the top: no class
        for n in filenames:
            if n.startswith((".", "~$", "._")) or (d / n).is_symlink():
                continue
            groups[(str(d.relative_to(root)), group_key(n))].append(n)
    return groups

class _Inline:
    """Stand-in for the process pool with -j 1 (and in tests)."""
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def map(self, fn, items, chunksize=1): return map(fn, items)

def _extract_group(args):
    """Versions of a document in order until one gives text -> {path: text}."""
    paths, lang = args
    out = {}
    for path in paths:
        try:
            out[path] = extract(Path(path), lang, ocr=False)
        except (Unreadable, OSError, subprocess.TimeoutExpired, ValueError):
            out[path] = ""
        if words(out[path]) >= MIN_WORDS:
            break
    return out

def learn(root: Path, inbox: Path, cfg, cache_path: Path, lang: str, jobs: int, changed_ok=False):
    """Reads the archive (with cache) -> (documents [{rel, label, text}], number of documents)."""
    cache = {}
    if cache_path and cache_path.exists():
        with gzip.open(cache_path, "rt", encoding="utf-8") as f:
            cache = json.load(f)
    groups = walk_archive(root, inbox, cfg["ignore"])
    plan = []                                        # (folder, [(rel, sig)] in order of preference)
    for (rel_dir, _), names in sorted(groups.items()):
        # ordnig's PDF/A copy first (it has the OCR text), then the original
        usable = sorted((n for n in names if preference(n) < len(PREFER) or Path(n).suffix.lower()[1:] in OOXML),
                        key=lambda x: (preference(x), x))
        cands = []
        for n in usable:
            st = (root / rel_dir / n).stat()
            cands.append((f"{rel_dir}/{n}", f"{st.st_size}:{st.st_mtime_ns}"))
        if cands:
            plan.append((rel_dir, cands))
    if not changed_ok:
        check_shrink(root, cache, [rel for _, cands in plan for rel, _ in cands])

    def known(cands):
        """Text from the cache if the order can be decided without reading again."""
        for rel, sig in cands:
            c = cache.get(rel)
            if not c or c[0] != sig:
                return None
            if words(c[1]) >= MIN_WORDS:
                return rel
        return ""
    pending = [cands for _, cands in plan if known(cands) is None]
    if pending:
        args = [([str(root / r) for r, _ in cands], lang) for cands in pending]
        with ProcessPoolExecutor(max_workers=jobs, mp_context=multiprocessing.get_context("fork")) if jobs > 1 else _Inline() as ex:
            for i, (cands, texts) in enumerate(zip(pending, ex.map(_extract_group, args, chunksize=8)), 1):
                sigs = dict(cands)
                for path, text in texts.items():
                    rel = str(Path(path).relative_to(root))
                    cache[rel] = [sigs[rel], text]
                if i % 50 == 0:
                    migrate.progress(f"Reading archive … {i}/{len(pending)}")
        migrate.progress_done()
    docs, new_cache = [], {}
    for rel_dir, cands in plan:
        for rel, sig in cands:
            if rel in cache and cache[rel][0] == sig:
                new_cache[rel] = cache[rel]
        rel = known(cands)
        if rel:
            docs.append({"rel": rel, "label": label_of(rel_dir), "text": Path(rel).stem + " " + cache[rel][1]})
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(new_cache, f, ensure_ascii=False)
        tmp.replace(cache_path)
    return docs, len(groups)


def check_mounts(root: Path):
    """An empty mount point is almost always a share that was not mounted on the host
    (docker -v then creates an empty folder)."""
    for d in [root] + sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        if os.path.ismount(d) and not any(d.iterdir()):
            sys.exit(f"{d} is empty – is the share mounted on the host?")

def check_shrink(root: Path, cache, current):
    """Compares with the last run: if more than half of the known documents are gone from the
    archive or a mounted share, something is more likely unmounted than deleted.
    Without this stop the cache would drop what was learned (hours of reading/OCR)."""
    mounts = {}
    def area(rel):
        top = rel.split("/", 1)[0]
        if top not in mounts:
            mounts[top] = top if os.path.ismount(root / top) else ""
        return mounts[top]
    before, now = Counter(map(area, cache)), Counter(map(area, current))
    before[""], now[""] = len(cache), len(current)
    for a in sorted(before):
        before_n, now_n = before[a], now[a]
        if before_n >= SHRINK_MIN and now_n < before_n * SHRINK_KEEP:
            where = f"in {root / a}" if a else "in the archive"
            sys.exit(f"{where} only {now_n} of {before_n} known documents are left – share not mounted?\n"
                     f"If this is on purpose (archive reorganised), learn again with --archive-changed.")


# ----------------------------------------------------------------- Similarity (k nearest neighbours)
class Index:
    def __init__(self, docs):
        self.docs = docs
        toks = [tokens(d["text"]) for d in docs]
        df = Counter(t for ts in toks for t in set(ts))
        n = len(docs)
        self.idf = {t: math.log((n + 1) / c) for t, c in df.items() if c < n}   # in every document: worthless
        self.postings = defaultdict(list)
        for i, ts in enumerate(toks):
            for t, w in self.vector(ts):
                self.postings[t].append((i, w))

    def vector(self, toks):
        tf = Counter(t for t in toks if t in self.idf)
        top = heapq.nlargest(TOP_TERMS, ((t, (1 + math.log(c)) * self.idf[t]) for t, c in tf.items()),
                             key=lambda x: x[1])
        norm = math.sqrt(sum(w * w for _, w in top)) or 1.0
        return [(t, w / norm) for t, w in top]

    def neighbors(self, toks, k, exclude=None):
        scores = defaultdict(float)
        for t, w in self.vector(toks):
            for i, dw in self.postings.get(t, ()):
                scores[i] += w * dw
        scores.pop(exclude, None)
        return heapq.nlargest(k, scores.items(), key=lambda x: x[1])

    def classify(self, toks, k, exclude=None):
        """-> (class, share of the votes, best similarity, most similar document) or None."""
        nb = self.neighbors(toks, k, exclude)
        if not nb:
            return None
        votes = defaultdict(float)
        for i, s in nb:
            votes[self.docs[i]["label"]] += s
        label, score = max(votes.items(), key=lambda x: x[1])
        best_i, best_s = max(((i, s) for i, s in nb if self.docs[i]["label"] == label), key=lambda x: x[1])
        return label, score / sum(votes.values()), best_s, self.docs[best_i]["rel"]


# ----------------------------------------------------------------- Deciding
def match_rule(rules, text):
    for r in rules:
        if (not r["any"] or any(rx.search(text) for rx in r["any"])) \
                and all(rx.search(text) for rx in r["all"]) and not any(rx.search(text) for rx in r["none"]):
            return r
    return None

def decide(path: Path, root: Path, cfg, index, lang, today):
    """-> dict(target=folder relative to the archive or None, copies=[folders], source, reason, suggestion)."""
    res = {"target": None, "copies": [], "source": "", "reason": "", "suggestion": ""}
    try:
        text = path.name + " " + extract(path, lang)
    except (Unreadable, OSError, subprocess.TimeoutExpired, ValueError) as e:
        res["reason"] = f"not readable: {e}"; return res
    rule = match_rule(cfg["rules"], text)
    if rule:
        label, year_rx = rule["target"], rule["year"] or cfg["year"].get(rule["target"])
        res["source"] = f"rule {rule['name']}"
    else:
        if words(text) < MIN_WORDS:
            res["reason"] = "no text found"; return res
        if index is None:
            res["reason"] = "no rule matches, archive without examples"; return res
        th = cfg["thresholds"]
        found = index.classify(tokens(text), th["k"])
        if not found:
            res["reason"] = "no similar documents"; return res
        label, share, sim, near = found
        res["suggestion"] = f"{label} ({share:.0%}, similarity {sim:.2f}: {near})"
        if share < th["share"] or sim < th["similarity"]:
            res["reason"] = "unsure"; return res
        res["source"] = f"similar {share:.0%} / {sim:.2f}"
        year_rx = cfg["year"].get(label)
    if YEAR in label:
        y = year_of(text, today, year_rx)
        if y is None:
            res["reason"] = f"no year found for {label}"; res["suggestion"] = res["suggestion"] or label
            return res
        label = label.replace(YEAR, str(y))
    if rule:
        # Copies: year from copy_year, else the newest date (not the [year] pattern of the folder:
        # that describes letters *in* that folder, not the copied ones)
        for k in rule["copy"]:
            if YEAR in k:
                y = year_of(text, today, rule["copy_year"])
                if y is None:
                    res["reason"] = f"no year found for the copy to {k}"; res["suggestion"] = label
                    return res
                k = k.replace(YEAR, str(y))
            res["copies"].append(k)
    res["target"] = label
    return res

def sha256(p: Path):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def free_name(folder: Path, name: str, reserved: set):
    """name, else name_2, name_3 … (never overwrite, not even within one run)."""
    cand = folder / name
    stem, suf = Path(name).stem, Path(name).suffix
    i = 2
    while cand.exists() or str(cand).casefold() in reserved:
        cand = folder / f"{stem}_{i}{suf}"; i += 1
    reserved.add(str(cand).casefold())
    return cand

def same_file_in(folder: Path, src: Path):
    """Same content already in the target folder? -> path or None."""
    if not folder.is_dir():
        return None
    size, digest = src.stat().st_size, None
    for f in folder.iterdir():
        if f.is_file() and f.stat().st_size == size:
            digest = digest or sha256(src)
            if sha256(f) == digest:
                return f
    return None

def copy_safely(src: Path, dst: Path):
    """Copies via a temporary file, checks by SHA-256 and never overwrites."""
    if dst.exists():
        raise FileExistsError(dst)
    tmp = dst.with_name(f".{dst.name}.ordnig-tmp")
    shutil.copy2(src, tmp)
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    if sha256(tmp) != sha256(src):
        tmp.unlink()
        raise OSError(f"copy differs: {dst}")
    if dst.exists():
        tmp.unlink(); raise FileExistsError(dst)
    os.rename(tmp, dst)

def move_safely(src: Path, dst: Path):
    """Moves without data loss: rename on the same drive, else copy + check + delete."""
    if dst.exists():
        raise FileExistsError(dst)
    if src.stat().st_dev == dst.parent.stat().st_dev:
        os.rename(src, dst); return
    copy_safely(src, dst)
    src.unlink()


# ----------------------------------------------------------------- Flow
def inbox_files(inbox: Path):
    return sorted(p for p in inbox.iterdir()
                  if p.is_file() and not p.name.startswith((".", "~$", "_")) and not p.is_symlink())

def evaluate(index, cfg, sample, today, rng):
    """Sort every document of the archive as if it arrived new (without itself)."""
    docs = index.docs
    ids = list(range(len(docs)))
    if len(ids) > sample:
        ids = rng.sample(ids, sample)
    preds = []
    k = cfg["thresholds"]["k"]
    for n, i in enumerate(ids, 1):
        found = index.classify(tokens(docs[i]["text"]), k, exclude=i)
        preds.append((i, found))
        if n % 100 == 0:
            migrate.progress(f"Evaluating … {n}/{len(ids)}")
    migrate.progress_done()
    rows = []
    for share in (0.5, 0.6, 0.7, 0.8, 0.9):
        auto = [(i, f) for i, f in preds if f and f[1] >= share and f[2] >= cfg["thresholds"]["similarity"]]
        right = sum(1 for i, f in auto if f[0] == docs[i]["label"])
        rows.append((share, len(auto), right))
    confusions = Counter((docs[i]["label"], f[0]) for i, f in preds if f and f[0] != docs[i]["label"])
    years = [(docs[i], year_of(docs[i]["text"], today, cfg["year"].get(docs[i]["label"])))
             for i in ids if YEAR in docs[i]["label"]]
    year_ok = sum(1 for d, y in years if y is not None and str(y) in d["rel"].split("/"))
    year_none = sum(1 for _, y in years if y is None)
    return len(ids), rows, confusions, (len(years), year_ok, year_none)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--inbox", type=Path, default=None,
                    help="inbox folder (default: the folder holding .ordnig/, else <archive>/Inbox)")
    ap.add_argument("--apply", action="store_true", help="really file (otherwise preview)")
    ap.add_argument("--eval", action="store_true", help="measure accuracy on the archive")
    ap.add_argument("--sample", type=int, default=2000, help="documents for --eval (default 2000)")
    ap.add_argument("--lang", default=migrate.DEFAULT_LANG, help="Tesseract languages (default deu+eng)")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1, help="parallel readers")
    ap.add_argument("--report", type=Path, default=None, help="write a Markdown report")
    ap.add_argument("--archive-changed", action="store_true",
                    help="many documents are gone on purpose (archive reorganised): learn anyway")
    args = ap.parse_args()
    root = args.root.resolve()
    inbox = state.find_inbox(root, args.inbox) if root.is_dir() else None
    inbox = inbox or root / state.INBOX
    if not root.is_dir():
        sys.exit(f"Archive missing: {root}")
    check_mounts(root)
    if not inbox.is_dir():
        sys.exit(f"Inbox missing: {inbox} (--inbox /path)")
    if not os.access(inbox, os.W_OK):
        sys.exit(f"Inbox is read-only: {inbox} (it holds .ordnig/ with what was learned)")
    missing = [t for t in ("gs", "tesseract", "soffice") if not shutil.which(t)]
    if missing:
        sys.exit(f"Missing tools: {', '.join(missing)}")
    state.open_state(inbox / STATE, root)
    cfg_path = inbox / STATE / "rules.toml"
    cfg = load_config(cfg_path, root)
    review = cfg["review"]
    today = date.today()
    t0 = time.monotonic()

    migrate.progress("Reading archive …")
    docs, n_groups = learn(root, inbox, cfg, inbox / STATE / "texts.json.gz", args.lang, args.jobs,
                           args.archive_changed)
    index = Index(docs) if docs else None
    labels = Counter(d["label"] for d in docs)
    learned = [("Examples from the archive", len(docs)), ("Folders (classes)", len(labels)),
               ("Without text (photos, scans before ordnig run …)", n_groups - len(docs)), ("Rules", len(cfg["rules"]))]
    cfg_note = f"Rules: {cfg_path}" if cfg_path.exists() else f"No rules ({cfg_path} missing)"

    if args.eval:
        if not index:
            sys.exit("No documents with text in the archive – `ordnig run` first?")
        n, rows, conf, (ny, yok, ynone) = evaluate(index, cfg, args.sample, today, random.Random(1))
        table = learned + [None, ("Checked (sample)", n)]
        for share, auto, right in rows:
            mark = " ←" if share == cfg["thresholds"]["share"] else ""
            table.append((f"Share ≥ {share:.0%}: automatic / of these right{mark}",
                          f"{auto / n:.0%} / {right / auto:.1%}" if auto else "0% / –"))
        if ny:
            table += [None, (f"Year right ({ny} in year folders)", f"{yok / ny:.1%}"),
                      ("  no year found", ynone)]
        sec = ["", "## Most frequent confusions", "", "| right | suggested | count |", "|---|---|---:|"]
        sec += [f"| {a} | {b} | {c} |" for (a, b), c in conf.most_common(20)]
        notes = [cfg_note, f"Run time {migrate.fmt_dur(time.monotonic() - t0)}"]
        write_eval(args.report, root, table, notes, sec, labels)
        migrate.console_table("Inbox: evaluation", table, notes + [f"Details: {args.report}" if args.report else ""])
        return state.OK

    if args.apply:
        check_log(inbox / STATE / LOG_NAME)               # before the first move
    files = inbox_files(inbox)
    results, counts = [], Counter()
    for i, f in enumerate(files, 1):
        migrate.progress(f"Sorting {i}/{len(files)}: {f.name}")
        r = decide(f, root, cfg, index, args.lang, today)
        r["file"] = f
        if r["target"]:
            folder = root / r["target"]
            dup = same_file_in(folder, f)
            if dup:
                r.update(target=None, copies=[], reason=f"already filed: {dup.relative_to(root)}")
        results.append(r)
    migrate.progress_done()

    reserved = set()
    for r in results:
        f = r["file"]
        r["copied_to"], copies = [], []
        if r["target"]:
            dst = free_name(root / r["target"], f.name, reserved)
            r["to"] = str(dst.relative_to(root))
            status = "filed"
            for k in r["copies"]:
                there = same_file_in(root / k, f)
                if there:
                    r["copied_to"].append(f"{there.relative_to(root)} {ALREADY}")
                    continue
                cdst = free_name(root / k, f.name, reserved)
                copies.append(cdst)
                r["copied_to"].append(str(cdst.relative_to(root)))
        else:
            dst = free_name(inbox / review, f.name, reserved)
            r["to"] = f"{review}/{dst.name}"
            status = "review"
        if args.apply:
            try:
                dst.parent.mkdir(parents=r["target"] is None, exist_ok=True)   # only year folders are new
                if status == "filed":
                    r["sha256"] = sha256(f)  # check recognises rule copies by content, also after a rename
                move_safely(f, dst)
            except OSError as e:
                status, r["reason"] = "error", f"{type(e).__name__}: {e}"
            else:
                for cdst in copies:          # the original is safe; a missing copy is an error of its own
                    try:
                        cdst.parent.mkdir(exist_ok=True)
                        copy_safely(dst, cdst)
                    except OSError as e:
                        r["reason"] += f"copy missing ({cdst.relative_to(root)}): {type(e).__name__}: {e}; "
                        counts["error"] += 1
        r["status"] = status
        counts[status] += 1
        if status == "filed" and copies:
            counts["copied"] += 1

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if args.apply:
        append_log(inbox / STATE / LOG_NAME, results, stamp)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with open(args.report.with_suffix(".csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "status", "to", "copies", "source", "reason", "suggestion", "time"])
            for r in results:
                w.writerow([r["file"].name, r["status"], r["to"], " | ".join(r["copied_to"]),
                            r["source"], r["reason"], r["suggestion"], stamp])
    verb = "filed" if args.apply else "would be filed"
    table = [("In the inbox", len(files)), (f"  {verb}", counts["filed"]),
             ("    of these also copied", counts["copied"]), (f"  to {review}", counts["review"])] + \
            ([("  Errors", counts["error"])] if counts["error"] else []) + [None] + learned
    notes = [cfg_note, "" if args.apply else "Preview – nothing moved. To file: ordnig sort --apply"]
    write_sort_report(args.report, root, inbox, results, table, notes, args.apply)
    migrate.console_table("Inbox" + ("" if args.apply else " (preview)"), table,
                          notes + [f"Details: {args.report}" if args.report else ""])
    if counts["error"]:
        return state.ERROR
    waiting = len(inbox_files(inbox / review)) if (inbox / review).is_dir() else 0
    if not args.apply:
        waiting += counts["review"]
    return state.REVIEW if waiting else state.OK

LOG_NAME = "sort-log.csv"
LOG_HEADER = ["time", "file", "status", "to", "copies", "source", "reason", "sha256"]
ALREADY = "(already there)"

def check_log(path: Path):
    """A log with other columns is neither rewritten (no migrations before production) nor
    appended to (the rows would not match the header): stop."""
    if not path.is_file():
        return
    with open(path, newline="", encoding="utf-8", errors="surrogateescape") as fh:
        head = next(csv.reader(fh), [])
    if head != LOG_HEADER:
        state.stop(f"{path}: columns of another ordnig version ({', '.join(head)}). "
                   "Move it away; sort starts a new one.")

def append_log(path: Path, results, stamp):
    """Lasting log in the inbox: what was filed where (also to undo it)."""
    path.parent.mkdir(exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(LOG_HEADER)
        for r in results:
            w.writerow([stamp, r["file"].name, r["status"], r["to"], " | ".join(r["copied_to"]),
                        r["source"], r["reason"], r.get("sha256", "")])

def write_sort_report(path, root, inbox, results, table, notes, applied):
    if not path:
        return
    rel_inbox = str(inbox.relative_to(root)) if inbox.is_relative_to(root) else inbox.name
    tree = {}
    for r in results:
        if r["status"] == "filed":
            tree[r["to"]] = f"{'✓' if applied else '→'} from the inbox ({r['source']})" + \
                (f" ✗ {r['reason']}" if r["reason"] else "")
            for k in r["copied_to"]:
                if not k.endswith(ALREADY):
                    tree[k] = f"{'✓' if applied else '→'} copy of {r['to']}"
        else:
            mark = "✗" if r["status"] == "error" else "⊘"
            key = r["file"].name if r["status"] == "error" else r["to"]
            tree[f"{rel_inbox}/{key}"] = f"{mark} {r['reason']}" + \
                (f"; suggestion: {r['suggestion']}" if r["suggestion"] else "")
    title = "Inbox sorted" if applied else "Inbox (preview)"
    migrate.write_report(path, title, inbox, migrate.render_tree(root.name, tree), table, notes)

def write_eval(path, root, table, notes, sections, labels):
    if not path:
        return
    sections = list(sections) + ["", "## Examples per folder", "", "| Folder | Examples |", "|---|---:|"] + \
        [f"| {l} | {c} |" for l, c in sorted(labels.items())]
    migrate.write_report(path, "Inbox: evaluation", root, [f"{root.name}/  —  see the tables below"], table, notes, sections)


if __name__ == "__main__":
    state.run(main)
