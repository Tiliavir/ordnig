"""Unit tests for src/migrate.py planning (no external tools needed)."""
import importlib.util, tempfile, unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
spec = importlib.util.spec_from_file_location("migrate", SRC / "migrate.py")
migrate = importlib.util.module_from_spec(spec); spec.loader.exec_module(migrate)


class Plan(unittest.TestCase):
    def setUp(self):
        migrate.RESERVED.clear()
        self.dir = tempfile.TemporaryDirectory(); self.root = Path(self.dir.name)
        for f in ["Briefe/x.doc", "Briefe/x.rtf", "Briefe/y.odt", "Briefe/y.pdf", "Scans/s.pdf", "Scans/s.tif",
                  "Briefe/~$x.doc", ".ordnig/migration_log.csv", "Fotos/bild.jpg"]:
            p = self.root / f; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x")

    def tearDown(self):
        self.dir.cleanup()

    def plan(self, images=()):
        jobs = migrate.plan(self.root, set(images), set(), set())
        return {str(j[1].relative_to(self.root)): j for j in jobs}

    def test_kinds_and_skips(self):
        jobs = self.plan()
        self.assertEqual({k: v[0] for k, v in jobs.items()},
                         {"Briefe/x.doc": "office", "Briefe/x.rtf": "office", "Briefe/y.odt": "odf",
                          "Briefe/y.pdf": "pdf", "Scans/s.pdf": "pdf"})

    def test_images_opt_in(self):
        self.assertEqual(self.plan(["tif"])["Scans/s.tif"][0], "image")

    def test_targets_never_collide_or_overwrite(self):
        jobs = self.plan(["tif"])
        targets = [t.name for j in jobs.values() for t in j[3:] if t]
        self.assertEqual(len(targets), len(set(str(t) for j in jobs.values() for t in j[3:] if t)))
        self.assertEqual(jobs["Briefe/x.doc"][3].name, "x.odt")
        self.assertEqual(jobs["Briefe/x.rtf"][3].name, "x.rtf.odt")
        self.assertEqual(jobs["Briefe/y.odt"][4].name, "y.odt.pdf")   # y.pdf exists
        self.assertEqual(jobs["Briefe/y.pdf"][4].name, "y_pdfa.pdf")
        self.assertEqual(jobs["Scans/s.tif"][4].name, "s.tif.pdf")    # s.pdf exists

    def test_done_and_generated_are_skipped(self):
        done = {str(self.root / "Briefe/x.doc")}
        generated = {str(self.root / "Briefe/y.pdf")}
        jobs = migrate.plan(self.root, set(), done, generated)
        names = {str(j[1].relative_to(self.root)) for j in jobs}
        self.assertNotIn("Briefe/x.doc", names)
        self.assertNotIn("Briefe/y.pdf", names)


class Suggest(unittest.TestCase):
    def test_tiff_is_suggested_photos_only_hinted(self):
        from collections import Counter
        cmd, hints = migrate.suggest(set(), "deu+eng", None, Counter(tif=3, jpg=40), planned=5)
        self.assertEqual(cmd, "ordnig run --images tif")
        self.assertIn("3 TIFF-Dateien", hints[0])
        self.assertIn("40 .jpg", hints[1]); self.assertIn("--images jpg,tif", hints[1])

    def test_keeps_given_options(self):
        from collections import Counter
        cmd, _ = migrate.suggest({"png"}, "deu+frk", 4, Counter(tiff=1), planned=1)
        self.assertEqual(cmd, "ordnig run --images png,tiff --lang deu+frk -j 4")

    def test_nothing_to_do(self):
        from collections import Counter
        cmd, hints = migrate.suggest(set(), "deu+eng", None, Counter(), planned=0)
        self.assertIsNone(cmd); self.assertIn("Nichts zu tun", hints[0])

    def test_count_images_skips_planned_and_state(self):
        root = Path(tempfile.mkdtemp())
        for f in ["a.tif", "b.TIFF", "c.jpg", "d.png", ".ordnig/x.tif"]:
            p = root / f; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x")
        self.assertEqual(dict(migrate.count_images(root, {"png"}, set(), set())), {"tif": 1, "tiff": 1, "jpg": 1})


try:
    import pikepdf
except ImportError:
    pikepdf = None


class Verapdf(unittest.TestCase):
    XML = """<?xml version="1.0" encoding="utf-8"?>
<report><buildInformation/><jobs>
<job><item size="10"><name>/tmp/v/0000.pdf</name></item>
  <validationReport jobEndStatus="normal" profileName="PDF/A-2U" isCompliant="true"/></job>
<job><item size="10"><name>/tmp/v/0001.pdf</name></item>
  <validationReport jobEndStatus="normal" profileName="PDF/A-2U" isCompliant="false"/></job>
<job><item size="10"><name>/tmp/v/0002.pdf</name></item><taskException/></job>
</jobs></report>"""

    def test_parse_batch(self):
        self.assertEqual(migrate.parse_verapdf("log noise\n" + self.XML),
                         {"0000.pdf": True, "0001.pdf": False, "0002.pdf": False})


@unittest.skipUnless(pikepdf, "pikepdf not installed")
class InspectPdf(unittest.TestCase):
    def make(self, text_pages, image_pages, pdfa=None):
        pdf = pikepdf.new()
        font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                    BaseFont=pikepdf.Name.Helvetica))
        for _ in range(text_pages):
            pdf.add_blank_page().obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        for _ in range(image_pages):
            pdf.add_blank_page()
        path = Path(tempfile.mkdtemp()) / "t.pdf"
        if pdfa:
            with pdf.open_metadata() as m:
                m["pdfaid:part"], m["pdfaid:conformance"] = pdfa
        pdf.save(path)
        return path

    def test_pages_without_text(self):
        self.assertEqual(migrate.inspect_pdf(self.make(2, 1)), (3, 1, ""))

    def test_pdfa_identifier(self):
        self.assertEqual(migrate.inspect_pdf(self.make(1, 0, ("2", "U"))), (1, 0, "2u"))

    def test_existing_pdfa_with_text_is_not_planned(self):
        root = Path(tempfile.mkdtemp())
        self.make(1, 0, ("1", "B")).rename(root / "fertig.pdf")
        self.make(1, 1, ("2", "B")).rename(root / "teilscan.pdf")
        info = {}
        jobs = migrate.plan(root, set(), set(), set(), info)
        self.assertEqual([j[1].name for j in jobs], ["teilscan.pdf"])
        self.assertEqual([p.name for p in info["already_pdfa"]], ["fertig.pdf"])
        self.assertEqual(info["scan_pdfs"], 1)


class Helpers(unittest.TestCase):
    def test_fix_scans_hint_and_flag(self):
        from collections import Counter
        _, hints = migrate.suggest(set(), "deu+eng", None, Counter(), planned=3, scan_pdfs=2)
        self.assertIn("--fix-scans", hints[-1]); self.assertIn("2 PDF mit Scan-Seiten", hints[-1])
        cmd, hints = migrate.suggest(set(), "deu+eng", None, Counter(tif=1), planned=1, fix_scans=True)
        self.assertEqual(cmd, "ordnig run --images tif --fix-scans")
        self.assertFalse(any("--fix-scans" in h for h in hints))

    def test_upgrade_old_log(self):
        log = Path(tempfile.mkdtemp()) / "migration_log.csv"
        log.write_text("quelle,aktion,ziel,status,pdfa,hinweis,zeit\n/a.doc,pdfa,/a.pdf,ok,2u,x,2026-11-14\n")
        migrate.upgrade_log(log)
        lines = log.read_text().splitlines()
        self.assertEqual(lines[0], ",".join(migrate.LOG_FIELDS))
        self.assertTrue(lines[1].startswith("/a.doc,pdfa,/a.pdf,ok,2u,x,2026-11-14"))

    def test_duration(self):
        self.assertEqual(migrate.fmt_dur(65), "1:05 min")
        self.assertEqual(migrate.fmt_dur(3725), "1:02:05")


class Tree(unittest.TestCase):
    def test_render(self):
        out = migrate.render_tree("ablage", {"A/x.doc": "→ x.odt", "b.pdf": "→ b_pdfa.pdf"})
        self.assertEqual(out, ["ablage/  —  2 Dateien", "├── A/  —  1 Datei", "│   └── x.doc  → x.odt",
                               "└── b.pdf  → b_pdfa.pdf"])


if __name__ == "__main__":
    unittest.main()
