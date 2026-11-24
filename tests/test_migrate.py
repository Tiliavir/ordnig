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
                  "Briefe/~$x.doc", ".ordnig/run-log.csv", "Fotos/bild.jpg"]:
            p = self.root / f; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x")

    def tearDown(self):
        self.dir.cleanup()

    def test_inbox_is_skipped(self):
        (self.root / "home/Inbox").mkdir(parents=True)
        (self.root / "home/Inbox/post.doc").write_text("x")
        jobs = migrate.plan(self.root, set(), set(), set(), skip=[self.root / "home/Inbox"])
        self.assertFalse(any("Inbox" in str(j) for j in jobs))
        self.assertTrue(any("post.doc" in str(j) for j in migrate.plan(self.root, set(), set(), set())))

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


    def test_relative_log_survives_another_mount_point(self):
        """Generated files are recognised wherever the archive is mounted (paths relative to it)."""
        import subprocess, sys
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "anywhere"; (root / "docs").mkdir(parents=True)
            (root / "docs" / "brief.docx").write_text("x"); (root / "docs" / "brief.odt").write_text("y")
            state = root / "Inbox" / ".ordnig"; state.mkdir(parents=True)
            (state / "run-log.csv").write_text(
                "source,action,target,status,pdfa,note,time,seconds\n"
                "docs/brief.docx,odf,docs/brief.odt,ok,,,2026-11-23 10:00:00,1\n", encoding="utf-8")
            report = Path(d) / "plan.md"
            subprocess.run([sys.executable, str(SRC / "migrate.py"), str(root), "--report", str(report)],
                           capture_output=True, text=True)
            self.assertIn("brief.docx", report.read_text())
            self.assertNotIn("brief.odt", report.read_text())   # generated, not planned again


class Suggest(unittest.TestCase):
    def test_tiff_is_suggested_photos_only_hinted(self):
        from collections import Counter
        cmd, hints = migrate.suggest(set(), "deu+eng", None, Counter(tif=3, jpg=40), planned=5)
        self.assertEqual(cmd, "ordnig run --images tif")
        self.assertIn("3 TIFF files", hints[0])
        self.assertIn("40 .jpg", hints[1]); self.assertIn("--images jpg,tif", hints[1])

    def test_keeps_given_options(self):
        from collections import Counter
        cmd, _ = migrate.suggest({"png"}, "deu+frk", 4, Counter(tiff=1), planned=1)
        self.assertEqual(cmd, "ordnig run --images png,tiff --lang deu+frk -j 4")

    def test_nothing_to_do(self):
        from collections import Counter
        cmd, hints = migrate.suggest(set(), "deu+eng", None, Counter(), planned=0)
        self.assertIsNone(cmd); self.assertIn("Nothing to do", hints[0])

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
        self.assertIn("--fix-scans", hints[-1]); self.assertIn("2 PDF with scanned pages", hints[-1])
        cmd, hints = migrate.suggest(set(), "deu+eng", None, Counter(tif=1), planned=1, fix_scans=True)
        self.assertEqual(cmd, "ordnig run --images tif --fix-scans")
        self.assertFalse(any("--fix-scans" in h for h in hints))

    def test_log_of_another_version_stops(self):
        log = Path(tempfile.mkdtemp()) / "run-log.csv"
        log.write_text("source,action,target,status,pdfa,note,time\n/a.doc,pdfa,/a.pdf,ok,2u,x,2026-11-14\n")
        with self.assertRaises(SystemExit) as e:
            migrate.check_log(log)
        self.assertEqual(e.exception.code, 2)

    def test_duration(self):
        self.assertEqual(migrate.fmt_dur(65), "1:05 min")
        self.assertEqual(migrate.fmt_dur(3725), "1:02:05")


class Tree(unittest.TestCase):
    def test_render(self):
        out = migrate.render_tree("archive", {"A/x.doc": "→ x.odt", "b.pdf": "→ b_pdfa.pdf"})
        self.assertEqual(out, ["archive/  —  2 files", "├── A/  —  1 file", "│   └── x.doc  → x.odt",
                               "└── b.pdf  → b_pdfa.pdf"])


if __name__ == "__main__":
    unittest.main()
