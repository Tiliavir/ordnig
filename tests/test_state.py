"""Unit tests for src/state.py: lock, format version, exit codes."""
import importlib.util, json, subprocess, sys, tempfile, unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
spec = importlib.util.spec_from_file_location("state", SRC / "state.py")
state = importlib.util.module_from_spec(spec); spec.loader.exec_module(state)

OPEN = """import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("state", sys.argv[1])
state = importlib.util.module_from_spec(spec); spec.loader.exec_module(state)
state.open_state(pathlib.Path(sys.argv[2]))
"""
HOLD = OPEN + 'print("locked", flush=True); sys.stdin.read()\n'


class State(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.folder = Path(self.dir.name) / ".ordnig"

    def tearDown(self):
        self.dir.cleanup()

    def open_in_child(self):
        """open_state in a second process: POSIX locks belong to the process."""
        return subprocess.run([sys.executable, "-I", "-c", OPEN,
                               str(SRC / "state.py"), str(self.folder)], capture_output=True, text=True)

    def test_writes_format_and_holder(self):
        state.open_state(self.folder)
        self.assertEqual(json.loads((self.folder / "version.json").read_text())["format"], state.FORMAT)
        self.assertIn("PID", (self.folder / "lock").read_text())

    def test_second_process_is_refused_while_locked(self):
        holder = subprocess.Popen([sys.executable, "-I", "-c", HOLD, str(SRC / "state.py"), str(self.folder)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            r = self.open_in_child()
            self.assertEqual(r.returncode, state.GESPERRT)
            self.assertIn("anderen ordnig-Lauf", r.stderr)
        finally:
            holder.stdin.close(); holder.wait(10); holder.stdout.close()
        self.assertEqual(self.open_in_child().returncode, 0)     # released when the holder ends

    def test_newer_or_broken_format_stops_with_setup_code(self):
        self.folder.mkdir()
        for content in (json.dumps({"format": state.FORMAT + 1}), "kaputt"):
            (self.folder / "version.json").write_text(content)
            r = self.open_in_child()
            self.assertEqual(r.returncode, state.SETUP, r.stderr)
        self.assertIn("unlesbar", r.stderr)

    def test_older_format_is_migrated_step_by_step(self):
        self.folder.mkdir()
        (self.folder / "version.json").write_text(json.dumps({"format": 0}))
        done = []
        state.MIGRATIONS[0] = lambda f: done.append(f)
        try:
            state.check_format(self.folder)
        finally:
            del state.MIGRATIONS[0]
        self.assertEqual(done, [self.folder])
        self.assertEqual(json.loads((self.folder / "version.json").read_text())["format"], state.FORMAT)

    def test_run_maps_messages_to_setup_code(self):
        for main, code in ((lambda: None, 0), (lambda: state.PRUEFEN, 4), (lambda: sys.exit("Ablage fehlt"), 2)):
            with self.assertRaises(SystemExit) as e:
                state.run(main)
            self.assertEqual(e.exception.code, code)


class Location(unittest.TestCase):
    """One .ordnig folder for everything: in the inbox, also when shares are mounted below /ablage."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_inbox_in_root_or_one_share_or_none(self):
        self.assertEqual(state.state_dir(self.root), self.root / ".ordnig")          # no inbox
        (self.root / "home/00_Eingang").mkdir(parents=True); (self.root / "documents").mkdir()
        self.assertEqual(state.state_dir(self.root), (self.root / "home/00_Eingang/.ordnig").resolve())
        (self.root / "00_Eingang").mkdir()
        self.assertEqual(state.find_inbox(self.root), (self.root / "00_Eingang").resolve())
        self.assertEqual(state.find_inbox(self.root, self.root / "x"), (self.root / "x").resolve())

    def test_two_shares_with_inbox_need_a_choice(self):
        for share in ("home", "documents"):
            (self.root / share / "00_Eingang").mkdir(parents=True)
        with self.assertRaises(SystemExit) as e:
            state.find_inbox(self.root)
        self.assertIn("--eingang", str(e.exception))

    def test_adopt_moves_or_appends_and_keeps_the_original(self):
        old_dir, new_dir = self.root / ".ordnig", self.root / "00_Eingang/.ordnig"
        old_dir.mkdir(); new_dir.mkdir(parents=True)
        (old_dir / "log.csv").write_text("h\nalt\n")
        state.adopt(self.root, new_dir, "log.csv")
        self.assertEqual((new_dir / "log.csv").read_text(), "h\nalt\n")
        self.assertTrue((old_dir / "log.csv.übernommen").exists())
        self.assertIsNone(state.legacy(self.root, new_dir, "log.csv"))
        (old_dir / "log.csv").write_text("h\nnoch älter\n")                  # both exist: append rows
        state.adopt(self.root, new_dir, "log.csv")
        self.assertEqual((new_dir / "log.csv").read_text(), "h\nalt\nnoch älter\n")
        state.adopt(self.root, old_dir, "log.csv")                             # same place: nothing to do


if __name__ == "__main__":
    unittest.main()
