"""Unit tests for src/check.py (stdlib only): python3 -m unittest discover -s tests"""
import importlib.util, os, tempfile, unittest, zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

def load(name):
    spec = importlib.util.spec_from_file_location(name, SRC / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

check = load("check")


class Names(unittest.TestCase):
    def issues(self, name, raw=None):
        return " | ".join(check.check_name(name, name, raw if raw is not None else name.encode()))

    def test_clean_name(self):
        self.assertEqual(self.issues("Brief 2004-03-01.pdf"), "")

    def test_windows_characters_and_reserved(self):
        self.assertIn("invalid on Windows", self.issues("frage?.txt"))
        self.assertIn("Reserved Windows name", self.issues("CON.txt"))
        self.assertIn("Ends with a space/dot", self.issues("brief. "))

    def test_encodings(self):
        self.assertIn("Mojibake", self.issues("Ã¤nderung.txt"))
        self.assertIn("not valid UTF-8", self.issues("M?rz.txt", b"M\xe4rz.txt"))
        self.assertIn("NFD", self.issues("März.txt"))

    def test_long_path(self):
        self.assertIn("Path very long", " ".join(check.check_name("a/" * 130 + "x", "x", b"x")))


class Integrity(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.d = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def write(self, name, data):
        p = self.d / name; p.write_bytes(data); return p

    def test_pdf(self):
        self.assertEqual(check.check_integrity(self.write("ok.pdf", b"%PDF-1.4\nx\n%%EOF\n"), "pdf"), [])
        self.assertIn("PDF truncated (no %%EOF)", check.check_integrity(self.write("cut.pdf", b"%PDF-1.4\nx"), "pdf"))
        self.assertIn("PDF header missing", check.check_integrity(self.write("no.pdf", b"hello"), "pdf"))

    def test_pdfa_identifier(self):
        xmp = b'%PDF-1.7\n<pdfaid:part>2</pdfaid:part><pdfaid:conformance>U</pdfaid:conformance>\n%%EOF\n'
        self.assertEqual(check.check_integrity(self.write("a.pdf", xmp), "pdf"), ["info:PDF/A-2u"])
        attr = b'%PDF-1.7\npdfaid:part="1" pdfaid:conformance="B"\n%%EOF\n'
        self.assertEqual(check.check_integrity(self.write("b.pdf", attr), "pdf"), ["info:PDF/A-1b"])

    def test_images(self):
        self.assertEqual(check.check_integrity(self.write("ok.jpg", b"\xff\xd8\xff\xe0" + b"0" * 10 + b"\xff\xd9"), "jpg"), [])
        self.assertIn("JPEG truncated (no EOI)", check.check_integrity(self.write("cut.jpg", b"\xff\xd8\xff\xe0000"), "jpg"))
        self.assertIn("PNG truncated (no IEND)", check.check_integrity(self.write("cut.png", b"\x89PNG\r\n\x1a\n0000"), "png"))

    def test_zip(self):
        ok = self.d / "ok.docx"
        with zipfile.ZipFile(ok, "w") as z: z.writestr("word/document.xml", "<x/>")
        self.assertEqual(check.check_integrity(ok, "docx"), [])
        self.assertIn("ZIP structure broken", check.check_integrity(self.write("bad.zip", b"PK\x03\x04broken"), "zip"))


class Mime(unittest.TestCase):
    def test_mismatch(self):
        self.assertTrue(check.mime_mismatch("pdf", "text/html"))
        self.assertFalse(check.mime_mismatch("pdf", "application/pdf"))
        self.assertFalse(check.mime_mismatch("docx", "application/zip"))
        self.assertFalse(check.mime_mismatch("pdf", "application/octet-stream"))
        self.assertFalse(check.mime_mismatch("xyz", "text/plain"))


class Tree(unittest.TestCase):
    def row(self, path, risk="ok", issues="", kind="", size=10):
        return dict(path=path, size=size, mtime="2004-03-01", ext=path.rsplit(".", 1)[-1], mime="",
                    risk=risk, kind=kind, issues=issues)

    def test_tree_shape_and_totals(self):
        rows = [self.row("Briefe/alt.doc", "risk", kind="Word 97-2003"),
                self.row("Briefe/2004/kaputt.pdf", issues="PDF truncated (no %%EOF)"),
                self.row("a.txt")]
        out = check.render_tree("archive", rows, ["empty"], {"Briefe": ["a.txt"]}, [])
        text = "\n".join(out)
        self.assertTrue(out[0].startswith("archive/  —  3 files"))
        self.assertIn("🔴 1 · ⚠ 1", out[0])
        self.assertIn("├── Briefe/  —  2 files", text)
        self.assertIn("│   ├── 2004/  —  1 file", text)
        self.assertIn("alt.doc  10 B · 2004-03-01 · 🔴 Word 97-2003 → ODT + PDF/A", text)
        self.assertIn("⚠ PDF truncated", text)
        self.assertIn("name collision: a.txt", text)
        self.assertIn("empty/  —  empty", text)
        self.assertTrue(out[-1].startswith("└── a.txt"))


class Inbox(unittest.TestCase):
    """check and verify skip the inbox: unsorted mail is neither checked nor counted as new/moved."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); d = Path(self.dir.name)
        self.root, self.out = d / "archive", d / "report"
        (self.root / "home" / "Inbox" / ".ordnig").mkdir(parents=True)    # a share's inbox is found by its state
        (self.root / "docs").mkdir()
        (self.root / "docs" / "a.txt").write_text("a")
        (self.root / "home" / "Inbox" / "brief.txt").write_text("b")

    def tearDown(self):
        self.dir.cleanup()

    def check(self, *extra):
        import subprocess, sys
        return subprocess.run([sys.executable, str(SRC / "check.py"), str(self.root), "--out", str(self.out),
                               "--quiet", "--no-integrity", *extra], capture_output=True, text=True)

    def manifest(self):
        return (self.out / "manifest.sha256").read_text()

    def test_scan_skips_inbox(self):
        files, *_ = check.scan(self.root.resolve(), None, [(self.root / "home" / "Inbox").resolve()])
        self.assertEqual([p.name for p in files], ["a.txt"])

    def test_check_and_verify_ignore_inbox(self):
        self.assertEqual(self.check().returncode, 0)
        self.assertIn("docs/a.txt", self.manifest())
        self.assertNotIn("brief.txt", self.manifest())
        old = self.out / "old.sha256"; old.write_text(self.manifest() + "0" * 64 + "  home/Inbox/alt.txt\n")
        (self.root / "home" / "Inbox" / "neu.txt").write_text("n")
        r = self.check("--verify", str(old))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)   # inbox entries of an older manifest are not "missing"

    def test_rule_copies_are_intended_duplicates(self):
        (self.root / "steuer").mkdir()
        for rel in ("docs/rechnung.pdf", "steuer/rechnung.pdf"):
            (self.root / rel).write_text("same")
        (self.root / "docs" / "zufall.txt").write_text("a")            # same as docs/a.txt: not intended
        self.log_rule_copy("docs/rechnung.pdf", "steuer/rechnung.pdf", "same")
        self.assertEqual(self.check().returncode, 0)
        dups = (self.out / "duplicates.csv").read_text().splitlines()
        self.assertIn("docs/rechnung.pdf,yes", "\n".join(dups))
        self.assertTrue(any(l.endswith("docs/zufall.txt,") for l in dups))
        report = (self.out / "report.md").read_text()
        self.assertIn("1 groups, ", report.split("### Duplicates")[1].split("###")[0])
        self.assertIn("`docs/rechnung.pdf` = `steuer/rechnung.pdf`", report.split("### Intended duplicates")[1])

    def log_rule_copy(self, to, copy, content):
        import hashlib
        state = self.root / "home" / "Inbox" / ".ordnig"; state.mkdir(exist_ok=True)
        (state / "sort-log.csv").write_text(
            "time,file,status,to,copies,source,reason,sha256\n"
            f"2026-11-24,x.pdf,filed,{to},{copy},rule x,,{hashlib.sha256(content.encode()).hexdigest()}\n",
            encoding="utf-8")

    def intended(self):
        return [l for l in (self.out / "duplicates.csv").read_text().splitlines() if l.endswith(",yes")]

    def test_rule_copy_stays_intended_after_rename(self):
        (self.root / "steuer").mkdir()
        (self.root / "docs" / "umbenannt.pdf").write_text("same")      # filed as docs/rechnung.pdf, then renamed
        (self.root / "steuer" / "rechnung.pdf").write_text("same")
        self.log_rule_copy("docs/rechnung.pdf", "steuer/rechnung.pdf", "same")
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(len(self.intended()), 2)

    def test_one_more_identical_file_is_a_finding(self):
        (self.root / "steuer").mkdir()
        for rel in ("docs/rechnung.pdf", "steuer/rechnung.pdf", "docs/nochmal.pdf"):
            (self.root / rel).write_text("same")
        self.log_rule_copy("docs/rechnung.pdf", "steuer/rechnung.pdf", "same")
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.intended(), [])


class Format(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(check.hs(0), "0 B")
        self.assertEqual(check.hs(1536), "1.5 KB")
        self.assertEqual(check.num(1234567), "1,234,567")


if __name__ == "__main__":
    unittest.main()
