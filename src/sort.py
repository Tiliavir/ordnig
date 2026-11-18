#!/usr/bin/env python3
"""
sort.py – sortiert die Eingangspost in die Ablage.

Für jede Datei im Eingang (Standard: <ablage>/00_Eingang):
  1. Text lesen: PDF (ohne Text: OCR), Bild (OCR), Office/ODF, E-Mail (.eml), Text.
  2. Regeln aus <eingang>/.ordnig/regeln.toml: die erste passende gewinnt.
  3. Sonst: die ähnlichsten Dokumente der Ablage stimmen ab (k-nächste Nachbarn, TF-IDF).
     Gelernt wird aus der Ablage selbst: jeder Ordner ist eine Klasse, Jahresordner
     (2019, 2020 …) werden zu {jahr}; das Jahr kommt aus dem jüngsten Datum im Dokument.
  4. Unsicher, unlesbar, ohne Datum für {jahr} oder schon abgelegt: <eingang>/_Prüfen/.
  Regeln können zusätzlich Kopien anlegen (kopie = "…/0_Steuer/{jahr}"), z. B. für die Steuer.

Ohne --apply nur Vorschau. --eval misst an der Ablage selbst, wie gut das klappt.
Alles, was zwischen Läufen bleibt, liegt in <eingang>/.ordnig/ (der Container kann auf
jedem Rechner laufen): regeln.toml, texte.json.gz (gelesene Texte der Ablage = das Gelernte),
protokoll.csv (jede Ablage mit --apply).
Dateien werden verschoben, nie überschrieben oder gelöscht; über Laufwerksgrenzen
erst kopiert und per SHA-256 verglichen, dann das Original entfernt.

Aufruf:
  python3 sort.py /ablage                       # Vorschau
  python3 sort.py /ablage --apply               # ablegen
  python3 sort.py /ablage --eval                # Treffsicherheit an der Ablage messen
"""
import argparse, csv, email, multiprocessing, gzip, hashlib, heapq, html, importlib.util, json, math, os, random, re, \
    shutil, subprocess, sys, tempfile, time, tomllib, unicodedata, zipfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from email import policy
from pathlib import Path

_spec = importlib.util.spec_from_file_location("migrate", Path(__file__).resolve().parent / "migrate.py")
migrate = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(migrate)

REVIEW = "_Prüfen"
STATE = ".ordnig"
SKIP_DIRS = migrate.SKIP_DIRS | {"OneNote_RecycleBin"}
YEAR_DIR = re.compile(r"(19|20)\d\d")
JAHR = "{jahr}"
MIN_WORDS = 8            # weniger Wörter: kein brauchbarer Text
MAX_CHARS = 8000         # pro Dokument gelesen und gespeichert (Briefkopf, Betreff, erste Seiten)
PAGES = 3                # aus PDFs gelesene Seiten
TOP_TERMS = 60           # stärkste Begriffe je Dokument im Index
DEFAULTS = {"k": 7, "anteil": 0.7, "aehnlichkeit": 0.2}

IMAGES = {"jpg", "jpeg", "png", "tif", "tiff", "bmp", "gif", "webp", "jp2", "pnm"}
PLAIN = {"txt", "md", "csv", "tsv", "xml", "json"}
HTML_EXT = {"html", "htm"}
ODF_EXT = {"odt", "ods", "odp", "fodt", "fods", "fodp"}
OOXML = {"docx": ("word/document.xml",), "docm": ("word/document.xml",),
         "xlsx": ("xl/sharedStrings.xml",), "xlsm": ("xl/sharedStrings.xml",),
         "pptx": ("ppt/slides/",)}
LEGACY = set(migrate.ODF_FOR) - set(OOXML)          # doc, xls, rtf, wpd … -> LibreOffice
# Bevorzugte Quelle je Dokumentgruppe: ordnigs Archivkopien haben OCR-Text.
PREFER = ["_pdfa.pdf", ".pdf", ".odt", ".ods", ".odp", ".docx", ".xlsx", ".pptx", ".eml", ".txt"]


class Unlesbar(Exception):
    """Datei kann nicht gelesen werden; Grund steht in der Nachricht."""


# ----------------------------------------------------------------- Konfiguration
def load_config(path: Path, root: Path):
    """Liest regeln.toml streng: unbekannte Schlüssel, kaputte Muster, fehlende Ziele sind Fehler."""
    cfg = {"ignorieren": [], "schwellen": dict(DEFAULTS), "jahr": {}, "regeln": []}
    if not path.exists():
        return cfg
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise SystemExit(f"{path}: {e}")
    errors = []
    unknown = set(raw) - {"ignorieren", "schwellen", "jahr", "regel"}
    if unknown:
        errors.append(f"unbekannte Schlüssel: {', '.join(sorted(unknown))}")
    ign = raw.get("ignorieren", [])
    if not (isinstance(ign, list) and all(isinstance(x, str) for x in ign)):
        errors.append("ignorieren: Liste von Ordnern erwartet")
    else:
        cfg["ignorieren"] = [x.strip("/") for x in ign]
        errors += [f"ignorieren: Ordner fehlt: {x}" for x in cfg["ignorieren"] if not (root / x).is_dir()]
    sw = raw.get("schwellen", {})
    for key, val in sw.items():
        if key not in DEFAULTS:
            errors.append(f"schwellen: unbekannter Schlüssel {key}")
        elif key == "k" and not (isinstance(val, int) and val >= 1):
            errors.append("schwellen.k: ganze Zahl ≥ 1 erwartet")
        elif key != "k" and not (isinstance(val, (int, float)) and 0 <= val <= 1):
            errors.append(f"schwellen.{key}: Zahl zwischen 0 und 1 erwartet")
        else:
            cfg["schwellen"][key] = val
    for ziel, muster in raw.get("jahr", {}).items():
        if JAHR not in ziel:
            errors.append(f"jahr: {ziel} enthält kein {JAHR}")
        rx = _compile(muster, f"jahr.{ziel}", errors, need_group=True)
        if rx:
            cfg["jahr"][ziel.strip("/")] = rx
        errors += _check_target(root, ziel, f"jahr.{ziel}")
    regeln = raw.get("regel", [])
    if not isinstance(regeln, list):
        errors.append("regel: [[regel]]-Einträge erwartet")
        regeln = []
    for i, r in enumerate(regeln, 1):
        where = f"regel {i}" + (f" ({r['name']})" if isinstance(r.get("name"), str) else "")
        unknown = set(r) - {"name", "ziel", "muster", "alle", "nicht", "jahr", "kopie", "kopie_jahr"}
        if unknown:
            errors.append(f"{where}: unbekannte Schlüssel: {', '.join(sorted(unknown))}")
        ziel = r.get("ziel")
        if not isinstance(ziel, str) or not ziel.strip("/"):
            errors.append(f"{where}: ziel fehlt"); continue
        errors += _check_target(root, ziel, where)
        rule = {"name": r.get("name") or ziel, "ziel": ziel.strip("/"), "jahr": None}
        for key in ("muster", "alle", "nicht"):
            vals = r.get(key, [])
            vals = [vals] if isinstance(vals, str) else vals
            if not (isinstance(vals, list) and all(isinstance(v, str) for v in vals)):
                errors.append(f"{where}: {key} muss Text oder Liste von Texten sein"); vals = []
            rule[key] = [rx for v in vals if (rx := _compile(v, f"{where}.{key}", errors))]
        if not rule["muster"] and not rule["alle"]:
            errors.append(f"{where}: muster oder alle nötig")
        if "jahr" in r:
            if JAHR not in rule["ziel"]:
                errors.append(f"{where}: jahr gesetzt, aber ziel enthält kein {JAHR}")
            rule["jahr"] = _compile(r["jahr"], f"{where}.jahr", errors, need_group=True)
        kopie = r.get("kopie", [])
        kopie = [kopie] if isinstance(kopie, str) else kopie
        if not (isinstance(kopie, list) and all(isinstance(k, str) and k.strip("/") for k in kopie)):
            errors.append(f"{where}: kopie muss Ordner oder Liste von Ordnern sein"); kopie = []
        for k in kopie:
            errors += _check_target(root, k, f"{where}.kopie")
        rule["kopie"] = [k.strip("/") for k in kopie if k.strip("/") != rule["ziel"]]
        rule["kopie_jahr"] = None
        if "kopie_jahr" in r:
            if not any(JAHR in k for k in rule["kopie"]):
                errors.append(f"{where}: kopie_jahr gesetzt, aber keine kopie mit {JAHR}")
            rule["kopie_jahr"] = _compile(r["kopie_jahr"], f"{where}.kopie_jahr", errors, need_group=True)
        cfg["regeln"].append(rule)
    if errors:
        raise SystemExit(f"{path}:\n  " + "\n  ".join(errors))
    return cfg

def _compile(pattern, where, errors, need_group=False):
    if not isinstance(pattern, str):
        errors.append(f"{where}: Muster muss Text sein"); return None
    try:
        rx = re.compile(pattern, re.I | re.M)
    except re.error as e:
        errors.append(f"{where}: ungültiges Muster {pattern!r}: {e}"); return None
    if need_group and rx.groups < 1:
        errors.append(f"{where}: Muster braucht eine Gruppe für das Jahr, z. B. (\\d{{4}})"); return None
    return rx

def _check_target(root: Path, ziel: str, where: str):
    """Ziel muss existieren; bei {jahr} der Ordner darüber (das Jahr wird angelegt)."""
    ziel = ziel.strip("/")
    base = ziel.split("/" + JAHR)[0] if JAHR in ziel else ziel
    if JAHR in ziel and not ziel.endswith("/" + JAHR):
        return [f"{where}: {JAHR} nur als letzter Ordner möglich: {ziel}"]
    return [] if (root / base).is_dir() else [f"{where}: Ordner fehlt: {base}"]


# ----------------------------------------------------------------- Text lesen
def _run(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise Unlesbar(f"{cmd[0]}: {(r.stderr or r.stdout).strip()[-200:] or f'Code {r.returncode}'}")
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
        raise Unlesbar(f"defekte .{ext}-Datei: {e}")

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
            raise Unlesbar(str(e))
        return pdf_text(pdf)

def extract(p: Path, lang: str, ocr=True):
    """Text einer Datei. ocr=False beim Lernen (zu langsam für die ganze Ablage)."""
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
            return ""        # beim Lernen liefert ordnigs ODF/PDF-Kopie den Text
        text = legacy_text(p)
    else:
        raise Unlesbar(f"Format .{ext or '(ohne Endung)'} wird nicht gelesen")
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]


# ----------------------------------------------------------------- Merkmale
TOKEN = re.compile(r"[^\W_]+")

def tokens(text):
    """Wörter ab 3 Zeichen; Zahlen nur ab 5 Stellen (Kunden-/Vertragsnummern, keine Beträge/Daten)."""
    out = []
    for w in TOKEN.findall(unicodedata.normalize("NFKC", text).casefold()):
        if w.isdigit():
            if len(w) >= 5:
                out.append(w)
        elif 3 <= len(w) <= 40:
            out.append(w)
    return out

MONATE = {"januar": 1, "jänner": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3,
          "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "august": 8, "aug": 8,
          "september": 9, "sep": 9, "sept": 9, "oktober": 10, "okt": 10, "november": 11, "nov": 11,
          "dezember": 12, "dez": 12}
D_DOT = re.compile(r"(?<![\d.])(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})(?![\d.])")
D_DOT2 = re.compile(r"(?<![\d.])(\d{2})\.(\d{2})\.(\d{2})(?![\d.])")
D_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
D_WORD = re.compile(r"(?<!\d)(\d{1,2})\.?\s+(" + "|".join(sorted(MONATE, key=len, reverse=True)) +
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
        add(int(y), MONATE[mon.casefold()], int(d))
    return found

def year_of(text, today: date, pattern=None):
    """Jahr fürs Ablegen: Gruppe 1 des Musters, sonst das jüngste Datum bis heute."""
    if pattern is not None:
        m = pattern.search(text)
        if not m:
            return None
        g = m.group(1)
        y = int(g) if g and g.isdigit() else None
        return y if y and 1950 <= y <= today.year else None
    ds = dates(text, today)
    return max(ds).year if ds else None


# ----------------------------------------------------------------- Ablage lernen
def label_of(rel_dir: str):
    """Ordner relativ zur Ablage -> Klasse; Jahresordner werden {jahr}."""
    return "/".join(JAHR if YEAR_DIR.fullmatch(part) else part for part in rel_dir.split("/"))

def group_key(name: str):
    """x.doc, x.odt, x.pdf, x_pdfa.pdf, x.doc.odt -> 'x' (ein Dokument, mehrere Fassungen)."""
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

def walk_archive(root: Path, eingang: Path, ignore):
    """{(Ordner, Gruppe): [Pfade]} aller Dokumente, die als Beispiel taugen."""
    ignore_abs = [root / x for x in ignore]
    groups = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames if n not in SKIP_DIRS and not n.startswith((".", "#", "@"))
                             and d / n != eingang and d / n not in ignore_abs)
        if d == root:
            continue                                # lose Dateien oben: keine Klasse
        for n in filenames:
            if n.startswith((".", "~$", "._")) or (d / n).is_symlink():
                continue
            groups[(str(d.relative_to(root)), group_key(n))].append(n)
    return groups

class _Inline:
    """Ersatz für den Prozesspool bei -j 1 (und in Tests)."""
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def map(self, fn, items, chunksize=1): return map(fn, items)

def _extract_group(args):
    """Fassungen eines Dokuments der Reihe nach, bis eine Text liefert -> {Pfad: Text}."""
    paths, lang = args
    out = {}
    for path in paths:
        try:
            out[path] = extract(Path(path), lang, ocr=False)
        except (Unlesbar, OSError, subprocess.TimeoutExpired, ValueError):
            out[path] = ""
        if words(out[path]) >= MIN_WORDS:
            break
    return out

def learn(root: Path, eingang: Path, cfg, cache_path: Path, lang: str, jobs: int):
    """Liest die Ablage (mit Cache) -> (Dokumente [{rel, label, text}], Anzahl Dokumente)."""
    cache = {}
    if cache_path and cache_path.exists():
        with gzip.open(cache_path, "rt", encoding="utf-8") as f:
            cache = json.load(f)
    groups = walk_archive(root, eingang, cfg["ignorieren"])
    plan = []                                        # (Ordner, [(rel, sig)] in Vorzugsreihenfolge)
    for (rel_dir, _), names in sorted(groups.items()):
        # ordnigs PDF/A-Kopie zuerst (hat den OCR-Text), dann das Original
        usable = sorted((n for n in names if preference(n) < len(PREFER) or Path(n).suffix.lower()[1:] in OOXML),
                        key=lambda x: (preference(x), x))
        cands = []
        for n in usable:
            st = (root / rel_dir / n).stat()
            cands.append((f"{rel_dir}/{n}", f"{st.st_size}:{st.st_mtime_ns}"))
        if cands:
            plan.append((rel_dir, cands))

    def known(cands):
        """Text aus dem Cache, falls die Reihenfolge ohne neues Lesen entscheidbar ist."""
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
                    migrate.progress(f"Lese Ablage … {i}/{len(pending)}")
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


# ----------------------------------------------------------------- Ähnlichkeit (k-nächste Nachbarn)
class Index:
    def __init__(self, docs):
        self.docs = docs
        toks = [tokens(d["text"]) for d in docs]
        df = Counter(t for ts in toks for t in set(ts))
        n = len(docs)
        self.idf = {t: math.log((n + 1) / c) for t, c in df.items() if c < n}   # überall vorkommend: wertlos
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
        """-> (Klasse, Anteil der Stimmen, beste Ähnlichkeit, ähnlichstes Dokument) oder None."""
        nb = self.neighbors(toks, k, exclude)
        if not nb:
            return None
        votes = defaultdict(float)
        for i, s in nb:
            votes[self.docs[i]["label"]] += s
        label, score = max(votes.items(), key=lambda x: x[1])
        best_i, best_s = max(((i, s) for i, s in nb if self.docs[i]["label"] == label), key=lambda x: x[1])
        return label, score / sum(votes.values()), best_s, self.docs[best_i]["rel"]


# ----------------------------------------------------------------- Entscheiden
def match_rule(rules, text):
    for r in rules:
        if (not r["muster"] or any(rx.search(text) for rx in r["muster"])) \
                and all(rx.search(text) for rx in r["alle"]) and not any(rx.search(text) for rx in r["nicht"]):
            return r
    return None

def decide(path: Path, root: Path, cfg, index, lang, today):
    """-> dict(ziel=relativer Zielordner oder None, kopien=[Ordner], quelle, grund, vorschlag)."""
    res = {"ziel": None, "kopien": [], "quelle": "", "grund": "", "vorschlag": ""}
    try:
        text = path.name + " " + extract(path, lang)
    except (Unlesbar, OSError, subprocess.TimeoutExpired, ValueError) as e:
        res["grund"] = f"nicht lesbar: {e}"; return res
    rule = match_rule(cfg["regeln"], text)
    if rule:
        label, year_rx = rule["ziel"], rule["jahr"] or cfg["jahr"].get(rule["ziel"])
        res["quelle"] = f"Regel {rule['name']}"
    else:
        if words(text) < MIN_WORDS:
            res["grund"] = "kein Text erkannt"; return res
        if index is None:
            res["grund"] = "keine Regel passt, Ablage ohne Beispiele"; return res
        sw = cfg["schwellen"]
        found = index.classify(tokens(text), sw["k"])
        if not found:
            res["grund"] = "keine ähnlichen Dokumente"; return res
        label, share, sim, near = found
        res["vorschlag"] = f"{label} ({share:.0%}, ähnlich {sim:.2f}: {near})"
        if share < sw["anteil"] or sim < sw["aehnlichkeit"]:
            res["grund"] = "unsicher"; return res
        res["quelle"] = f"ähnlich {share:.0%} / {sim:.2f}"
        year_rx = cfg["jahr"].get(label)
    if JAHR in label:
        y = year_of(text, today, year_rx)
        if y is None:
            res["grund"] = f"kein Jahr erkannt für {label}"; res["vorschlag"] = res["vorschlag"] or label
            return res
        label = label.replace(JAHR, str(y))
    if rule:
        # Kopien: Jahr aus kopie_jahr, sonst jüngstes Datum (nicht das [jahr]-Muster des Ordners:
        # das beschreibt Briefe *in* diesem Ordner, nicht die kopierten)
        for k in rule["kopie"]:
            if JAHR in k:
                y = year_of(text, today, rule["kopie_jahr"])
                if y is None:
                    res["grund"] = f"kein Jahr erkannt für Kopie nach {k}"; res["vorschlag"] = label
                    return res
                k = k.replace(JAHR, str(y))
            res["kopien"].append(k)
    res["ziel"] = label
    return res

def sha256(p: Path):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def free_name(folder: Path, name: str, reserved: set):
    """name, sonst name_2, name_3 … (nie überschreiben; auch nicht innerhalb eines Laufs)."""
    cand = folder / name
    stem, suf = Path(name).stem, Path(name).suffix
    i = 2
    while cand.exists() or str(cand).casefold() in reserved:
        cand = folder / f"{stem}_{i}{suf}"; i += 1
    reserved.add(str(cand).casefold())
    return cand

def same_file_in(folder: Path, src: Path):
    """Gleicher Inhalt schon im Zielordner? -> Pfad oder None."""
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
    """Kopiert über eine Zwischendatei, prüft per SHA-256 und überschreibt nie."""
    if dst.exists():
        raise FileExistsError(dst)
    tmp = dst.with_name(f".{dst.name}.ordnig-tmp")
    shutil.copy2(src, tmp)
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    if sha256(tmp) != sha256(src):
        tmp.unlink()
        raise OSError(f"Kopie weicht ab: {dst}")
    if dst.exists():
        tmp.unlink(); raise FileExistsError(dst)
    os.rename(tmp, dst)

def move_safely(src: Path, dst: Path):
    """Verschiebt ohne Datenverlust: gleiches Laufwerk umbenennen, sonst kopieren + prüfen + löschen."""
    if dst.exists():
        raise FileExistsError(dst)
    if src.stat().st_dev == dst.parent.stat().st_dev:
        os.rename(src, dst); return
    copy_safely(src, dst)
    src.unlink()


# ----------------------------------------------------------------- Ablauf
def inbox_files(eingang: Path):
    return sorted(p for p in eingang.iterdir()
                  if p.is_file() and not p.name.startswith((".", "~$", "_")) and not p.is_symlink())

def evaluate(index, cfg, sample, today, rng):
    """Jedes Dokument der Ablage so einsortieren, als käme es neu an (ohne sich selbst)."""
    docs = index.docs
    ids = list(range(len(docs)))
    if len(ids) > sample:
        ids = rng.sample(ids, sample)
    preds = []
    k = cfg["schwellen"]["k"]
    for n, i in enumerate(ids, 1):
        found = index.classify(tokens(docs[i]["text"]), k, exclude=i)
        preds.append((i, found))
        if n % 100 == 0:
            migrate.progress(f"Bewerte … {n}/{len(ids)}")
    migrate.progress_done()
    rows = []
    for share in (0.5, 0.6, 0.7, 0.8, 0.9):
        auto = [(i, f) for i, f in preds if f and f[1] >= share and f[2] >= cfg["schwellen"]["aehnlichkeit"]]
        right = sum(1 for i, f in auto if f[0] == docs[i]["label"])
        rows.append((share, len(auto), right))
    confusions = Counter((docs[i]["label"], f[0]) for i, f in preds if f and f[0] != docs[i]["label"])
    years = [(docs[i], year_of(docs[i]["text"], today, cfg["jahr"].get(docs[i]["label"])))
             for i in ids if JAHR in docs[i]["label"]]
    year_ok = sum(1 for d, y in years if y is not None and str(y) in d["rel"].split("/"))
    year_none = sum(1 for _, y in years if y is None)
    return len(ids), rows, confusions, (len(years), year_ok, year_none)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--eingang", type=Path, default=None, help="Eingangsordner (Standard: <ablage>/00_Eingang)")
    ap.add_argument("--apply", action="store_true", help="wirklich ablegen (sonst Vorschau)")
    ap.add_argument("--eval", action="store_true", help="Treffsicherheit an der Ablage messen")
    ap.add_argument("--stichprobe", type=int, default=2000, help="Dokumente für --eval (Standard 2000)")
    ap.add_argument("--lang", default=migrate.DEFAULT_LANG, help="Tesseract-Sprachen (Standard deu+eng)")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1, help="parallele Leser")
    ap.add_argument("--report", type=Path, default=None, help="Bericht als Markdown schreiben")
    args = ap.parse_args()
    root = args.root.resolve()
    eingang = (args.eingang or root / "00_Eingang").resolve()
    if not root.is_dir():
        sys.exit(f"Ablage fehlt: {root}")
    if not eingang.is_dir():
        sys.exit(f"Eingang fehlt: {eingang} (--eingang /pfad)")
    if not os.access(eingang, os.W_OK):
        sys.exit(f"Eingang ist schreibgeschützt: {eingang} (dort liegt .ordnig/ mit dem Gelernten)")
    missing = [t for t in ("gs", "tesseract", "soffice") if not shutil.which(t)]
    if missing:
        sys.exit(f"Fehlende Programme: {', '.join(missing)}")
    cfg_path = eingang / STATE / "regeln.toml"
    cfg = load_config(cfg_path, root)
    today = date.today()
    t0 = time.monotonic()

    migrate.progress("Lese Ablage …")
    docs, n_groups = learn(root, eingang, cfg, eingang / STATE / "texte.json.gz", args.lang, args.jobs)
    index = Index(docs) if docs else None
    labels = Counter(d["label"] for d in docs)
    learned = [("Beispiele aus der Ablage", len(docs)), ("Ordner (Klassen)", len(labels)),
               ("Ohne Text (Fotos, Scans vor ordnig run …)", n_groups - len(docs)), ("Regeln", len(cfg["regeln"]))]
    cfg_note = f"Regeln: {cfg_path}" if cfg_path.exists() else f"Keine Regeln ({cfg_path} fehlt)"

    if args.eval:
        if not index:
            sys.exit("Keine Dokumente mit Text in der Ablage – erst `ordnig run`?")
        n, rows, conf, (ny, yok, ynone) = evaluate(index, cfg, args.stichprobe, today, random.Random(1))
        table = learned + [None, ("Geprüft (Stichprobe)", n)]
        for share, auto, right in rows:
            mark = " ←" if share == cfg["schwellen"]["anteil"] else ""
            table.append((f"Anteil ≥ {share:.0%}: automatisch / davon richtig{mark}",
                          f"{auto / n:.0%} / {right / auto:.1%}" if auto else "0% / –"))
        if ny:
            table += [None, (f"Jahr richtig ({ny} in Jahresordnern)", f"{yok / ny:.1%}"),
                      ("  kein Jahr erkannt", ynone)]
        sec = ["", "## Häufigste Verwechslungen", "", "| richtig | vorgeschlagen | Anzahl |", "|---|---|---:|"]
        sec += [f"| {a} | {b} | {c} |" for (a, b), c in conf.most_common(20)]
        notes = [cfg_note, f"Laufzeit {migrate.fmt_dur(time.monotonic() - t0)}"]
        write_eval(args.report, root, table, notes, sec, labels)
        migrate.console_table("Eingang: Bewertung", table, notes + [f"Details: {args.report}" if args.report else ""])
        return

    files = inbox_files(eingang)
    results, counts = [], Counter()
    for i, f in enumerate(files, 1):
        migrate.progress(f"Ordne {i}/{len(files)}: {f.name}")
        r = decide(f, root, cfg, index, args.lang, today)
        r["datei"] = f
        if r["ziel"]:
            folder = root / r["ziel"]
            dup = same_file_in(folder, f)
            if dup:
                r.update(ziel=None, kopien=[], grund=f"schon abgelegt: {dup.relative_to(root)}")
        results.append(r)
    migrate.progress_done()

    reserved = set()
    for r in results:
        f = r["datei"]
        r["kopien_nach"], copies = [], []
        if r["ziel"]:
            dst = free_name(root / r["ziel"], f.name, reserved)
            r["nach"] = str(dst.relative_to(root))
            status = "abgelegt"
            for k in r["kopien"]:
                there = same_file_in(root / k, f)
                if there:
                    r["kopien_nach"].append(f"{there.relative_to(root)} (schon vorhanden)")
                    continue
                cdst = free_name(root / k, f.name, reserved)
                copies.append(cdst)
                r["kopien_nach"].append(str(cdst.relative_to(root)))
        else:
            dst = free_name(eingang / REVIEW, f.name, reserved)
            r["nach"] = f"{REVIEW}/{dst.name}"
            status = "prüfen"
        if args.apply:
            try:
                dst.parent.mkdir(parents=r["ziel"] is None, exist_ok=True)   # nur Jahresordner neu
                move_safely(f, dst)
            except OSError as e:
                status, r["grund"] = "fehler", f"{type(e).__name__}: {e}"
            else:
                for cdst in copies:          # Original liegt sicher; eine fehlende Kopie ist ein Fehler für sich
                    try:
                        cdst.parent.mkdir(exist_ok=True)
                        copy_safely(dst, cdst)
                    except OSError as e:
                        r["grund"] += f"Kopie fehlt ({cdst.relative_to(root)}): {type(e).__name__}: {e}; "
                        counts["fehler"] += 1
        r["status"] = status
        counts[status] += 1
        if status == "abgelegt" and copies:
            counts["kopiert"] += 1

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if args.apply:
        append_log(eingang / STATE / "protokoll.csv", results, stamp)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with open(args.report.with_suffix(".csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["datei", "status", "nach", "kopien", "quelle", "grund", "vorschlag", "zeit"])
            for r in results:
                w.writerow([r["datei"].name, r["status"], r["nach"], " | ".join(r["kopien_nach"]),
                            r["quelle"], r["grund"], r["vorschlag"], stamp])
    verb = "abgelegt" if args.apply else "würde abgelegt"
    table = [("Im Eingang", len(files)), (f"  {verb}", counts["abgelegt"]),
             ("    davon zusätzlich kopiert", counts["kopiert"]), (f"  nach {REVIEW}", counts["prüfen"])] + ([("  Fehler", counts["fehler"])] if counts["fehler"] else []) + \
            [None] + learned
    notes = [cfg_note, "" if args.apply else "Vorschau – nichts verschoben. Ablegen: ordnig sort --apply"]
    write_sort_report(args.report, root, eingang, results, table, notes, args.apply)
    migrate.console_table("Eingang" + ("" if args.apply else " (Vorschau)"), table,
                          notes + [f"Details: {args.report}" if args.report else ""])
    if counts["fehler"]:
        sys.exit(1)

LOG_HEADER = ["zeit", "datei", "status", "nach", "kopien", "quelle", "grund"]

def append_log(path: Path, results, stamp):
    """Dauerhaftes Protokoll im Eingang: was wohin abgelegt wurde (auch zum Rückgängigmachen)."""
    path.parent.mkdir(exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(LOG_HEADER)
        for r in results:
            w.writerow([stamp, r["datei"].name, r["status"], r["nach"], " | ".join(r["kopien_nach"]),
                        r["quelle"], r["grund"]])

def write_sort_report(path, root, eingang, results, table, notes, applied):
    if not path:
        return
    inbox = str(eingang.relative_to(root)) if eingang.is_relative_to(root) else eingang.name
    tree = {}
    for r in results:
        if r["status"] == "abgelegt":
            tree[r["nach"]] = f"{'✓' if applied else '→'} aus dem Eingang ({r['quelle']})" + \
                (f" ✗ {r['grund']}" if r["grund"] else "")
            for k in r["kopien_nach"]:
                if not k.endswith("(schon vorhanden)"):
                    tree[k] = f"{'✓' if applied else '→'} Kopie von {r['nach']}"
        else:
            mark = "✗" if r["status"] == "fehler" else "⊘"
            key = r["datei"].name if r["status"] == "fehler" else r["nach"]
            tree[f"{inbox}/{key}"] = f"{mark} {r['grund']}" + \
                (f"; Vorschlag: {r['vorschlag']}" if r["vorschlag"] else "")
    title = "Eingang sortiert" if applied else "Eingang (Vorschau)"
    migrate.write_report(path, title, eingang, migrate.render_tree(root.name, tree), table, notes)

def write_eval(path, root, table, notes, sections, labels):
    if not path:
        return
    sections = list(sections) + ["", "## Beispiele je Ordner", "", "| Ordner | Beispiele |", "|---|---:|"] + \
        [f"| {l} | {c} |" for l, c in sorted(labels.items())]
    migrate.write_report(path, "Eingang: Bewertung", root, [f"{root.name}/  —  siehe Tabellen unten"], table, notes, sections)


if __name__ == "__main__":
    main()
