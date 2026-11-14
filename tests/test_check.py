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
        self.assertIn("ungültig unter Windows", self.issues("frage?.txt"))
        self.assertIn("Reservierter Windows-Name", self.issues("CON.txt"))
        self.assertIn("Endet mit Leerzeichen/Punkt", self.issues("brief. "))

    def test_encodings(self):
        self.assertIn("Mojibake", self.issues("Ã¤nderung.txt"))
        self.assertIn("kein gültiges UTF-8", self.issues("M?rz.txt", b"M\xe4rz.txt"))
        self.assertIn("NFD", self.issues("März.txt"))

    def test_long_path(self):
        self.assertIn("Pfad sehr lang", " ".join(check.check_name("a/" * 130 + "x", "x", b"x")))


class Integrity(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.d = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def write(self, name, data):
        p = self.d / name; p.write_bytes(data); return p

    def test_pdf(self):
        self.assertEqual(check.check_integrity(self.write("ok.pdf", b"%PDF-1.4\nx\n%%EOF\n"), "pdf"), [])
        self.assertIn("PDF abgeschnitten (kein %%EOF)", check.check_integrity(self.write("cut.pdf", b"%PDF-1.4\nx"), "pdf"))
        self.assertIn("PDF-Header fehlt", check.check_integrity(self.write("no.pdf", b"hello"), "pdf"))

    def test_pdfa_identifier(self):
        xmp = b'%PDF-1.7\n<pdfaid:part>2</pdfaid:part><pdfaid:conformance>U</pdfaid:conformance>\n%%EOF\n'
        self.assertEqual(check.check_integrity(self.write("a.pdf", xmp), "pdf"), ["info:PDF/A-2u"])
        attr = b'%PDF-1.7\npdfaid:part="1" pdfaid:conformance="B"\n%%EOF\n'
        self.assertEqual(check.check_integrity(self.write("b.pdf", attr), "pdf"), ["info:PDF/A-1b"])

    def test_images(self):
        self.assertEqual(check.check_integrity(self.write("ok.jpg", b"\xff\xd8\xff\xe0" + b"0" * 10 + b"\xff\xd9"), "jpg"), [])
        self.assertIn("JPEG abgeschnitten (kein EOI)", check.check_integrity(self.write("cut.jpg", b"\xff\xd8\xff\xe0000"), "jpg"))
        self.assertIn("PNG abgeschnitten (kein IEND)", check.check_integrity(self.write("cut.png", b"\x89PNG\r\n\x1a\n0000"), "png"))

    def test_zip(self):
        ok = self.d / "ok.docx"
        with zipfile.ZipFile(ok, "w") as z: z.writestr("word/document.xml", "<x/>")
        self.assertEqual(check.check_integrity(ok, "docx"), [])
        self.assertIn("ZIP-Struktur defekt", check.check_integrity(self.write("bad.zip", b"PK\x03\x04broken"), "zip"))


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
                self.row("Briefe/2004/kaputt.pdf", issues="PDF abgeschnitten (kein %%EOF)"),
                self.row("a.txt")]
        out = check.render_tree("ablage", rows, ["leer"], {"Briefe": ["a.txt"]}, [])
        text = "\n".join(out)
        self.assertTrue(out[0].startswith("ablage/  —  3 Dateien"))
        self.assertIn("🔴 1 · ⚠ 1", out[0])
        self.assertIn("├── Briefe/  —  2 Dateien", text)
        self.assertIn("│   ├── 2004/  —  1 Datei", text)
        self.assertIn("alt.doc  10 B · 2004-03-01 · 🔴 Word 97-2003 → ODT + PDF/A", text)
        self.assertIn("⚠ PDF abgeschnitten", text)
        self.assertIn("Namenskollision: a.txt", text)
        self.assertIn("leer/  —  leer", text)
        self.assertTrue(out[-1].startswith("└── a.txt"))


class Format(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(check.hs(0), "0 B")
        self.assertEqual(check.hs(1536), "1.5 KB")
        self.assertEqual(check.num(1234567), "1.234.567")


if __name__ == "__main__":
    unittest.main()
