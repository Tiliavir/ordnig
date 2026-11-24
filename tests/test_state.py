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
            self.assertEqual(r.returncode, state.LOCKED)
            self.assertIn("another ordnig run", r.stderr)
        finally:
            holder.stdin.close(); holder.wait(10); holder.stdout.close()
        self.assertEqual(self.open_in_child().returncode, 0)     # released when the holder ends

    def test_newer_or_broken_format_stops_with_setup_code(self):
        self.folder.mkdir()
        for content in (json.dumps({"format": state.FORMAT + 1}), "broken"):
            (self.folder / "version.json").write_text(content)
            r = self.open_in_child()
            self.assertEqual(r.returncode, state.SETUP, r.stderr)
        self.assertIn("unreadable", r.stderr)

    def test_older_format_is_migrated_step_by_step(self):
        self.folder.mkdir()
        old = state.FORMAT - 1
        (self.folder / "version.json").write_text(json.dumps({"format": old}))
        done = []
        state.MIGRATIONS[old] = lambda f, root: done.append(f)
        try:
            state.check_format(self.folder)
        finally:
            del state.MIGRATIONS[old]
        self.assertEqual(done, [self.folder])
        self.assertEqual(json.loads((self.folder / "version.json").read_text())["format"], state.FORMAT)

    def test_run_maps_messages_to_setup_code(self):
        for main, code in ((lambda: None, 0), (lambda: state.REVIEW, 4), (lambda: sys.exit("Archive missing"), 2)):
            with self.assertRaises(SystemExit) as e:
                state.run(main)
            self.assertEqual(e.exception.code, code)


class LogPaths(unittest.TestCase):
    """The conversion log stores paths relative to the archive, not container paths."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.root = Path(self.dir.name) / "archive"

    def tearDown(self):
        self.dir.cleanup()

    def test_rel_and_abs(self):
        self.assertEqual(state.to_rel(str(self.root / "a" / "b.pdf"), self.root), "a/b.pdf")
        self.assertEqual(state.to_rel("/elsewhere/b.pdf", self.root), "/elsewhere/b.pdf")
        self.assertEqual(state.to_rel("", self.root), "")
        self.assertEqual(state.to_abs("a/b.pdf", self.root), str(self.root / "a" / "b.pdf"))
        self.assertEqual(state.to_abs("/old/b.pdf", self.root), "/old/b.pdf")


class NoMigration(unittest.TestCase):
    """Before production nothing is migrated: old state folders are refused, not rewritten."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.folder = Path(self.dir.name) / ".ordnig"
        self.folder.mkdir()

    def tearDown(self):
        self.dir.cleanup()

    def test_older_format_without_migration_stops(self):
        (self.folder / "version.json").write_text(json.dumps({"format": 1}))
        with self.assertRaises(SystemExit) as e:
            state.check_format(self.folder)
        self.assertEqual(e.exception.code, state.SETUP)

    def test_german_state_files_stop(self):
        (self.folder / "regeln.toml").write_text("")
        with self.assertRaises(SystemExit) as e:
            state.open_state(self.folder)
        self.assertEqual(e.exception.code, state.SETUP)


class Mounts(unittest.TestCase):
    """A lock on SMB with nobrl only works on this machine: warn."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.m = Path(self.dir.name) / "mounts"

    def tearDown(self):
        self.dir.cleanup()

    def warns(self, line, path="/archive/home/Inbox/.ordnig/lock"):
        self.m.write_text("overlay / overlay rw,relatime 0 0\n" + line + "\n")
        return state.warn_local_lock(Path(path), str(self.m))

    def test_nobrl_warns(self):
        self.assertTrue(self.warns("//nas/home /archive/home cifs rw,vers=3.1.1,nobrl,cache=strict 0 0"))

    def test_cifs_with_locks_local_disk_and_other_mounts_do_not(self):
        self.assertFalse(self.warns("//nas/home /archive/home cifs rw,vers=3.1.1,cache=strict 0 0"))
        self.assertFalse(self.warns("/dev/sda1 /archive/home ext4 rw,nobrl 0 0"))
        self.assertFalse(self.warns("//nas/home /archive/homes cifs rw,nobrl 0 0"))   # prefix, not parent

    def test_longest_mount_point_wins_and_escapes(self):
        self.m.write_text("//nas/a /archive cifs rw,nobrl 0 0\n/dev/sdb /archive/my\\040home ext4 rw 0 0\n")
        self.assertEqual(state.mount_of(Path("/archive/my home/x"), str(self.m))[0], "ext4")
        self.assertEqual(state.mount_of(Path("/archive/other"), str(self.m))[0], "cifs")
        self.m.write_text("/dev/sdb /archive/Dokumente\\040Ü ext4 rw 0 0\n")
        self.assertEqual(state.mount_of(Path("/archive/Dokumente Ü/x"), str(self.m))[0], "ext4")


class Location(unittest.TestCase):
    """One .ordnig folder for everything: in the inbox, also when shares are mounted below /archive."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_default_inbox_or_none(self):
        self.assertEqual(state.state_dir(self.root), self.root / ".ordnig")          # no inbox
        (self.root / "Inbox").mkdir()
        self.assertEqual(state.state_dir(self.root), (self.root / "Inbox/.ordnig").resolve())
        self.assertEqual(state.find_inbox(self.root, self.root / "x"), (self.root / "x").resolve())

    def test_any_folder_holding_ordnig_is_the_inbox(self):
        (self.root / "Inbox").mkdir()
        (self.root / "home/00_Eingang/.ordnig").mkdir(parents=True)                # own name, in a share
        self.assertEqual(state.find_inbox(self.root), (self.root / "home/00_Eingang").resolve())

    def test_two_inboxes_need_a_choice(self):
        for share in ("home", "documents"):
            (self.root / share / "Post" / ".ordnig").mkdir(parents=True)
        with self.assertRaises(SystemExit) as e:
            state.find_inbox(self.root)
        self.assertIn("--inbox", str(e.exception))


if __name__ == "__main__":
    unittest.main()
