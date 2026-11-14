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


class Tree(unittest.TestCase):
    def test_render(self):
        out = migrate.render_tree("ablage", {"A/x.doc": "→ x.odt", "b.pdf": "→ b_pdfa.pdf"})
        self.assertEqual(out, ["ablage/  —  2 Dateien", "├── A/  —  1 Datei", "│   └── x.doc  → x.odt",
                               "└── b.pdf  → b_pdfa.pdf"])


if __name__ == "__main__":
    unittest.main()
