"""Unit tests for src/sort.py (no external tools: text files only)."""
import importlib.util, tempfile, unittest
from datetime import date
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
spec = importlib.util.spec_from_file_location("sort", SRC / "sort.py")
sort = importlib.util.module_from_spec(spec); spec.loader.exec_module(sort)

TODAY = date(2026, 11, 16)
DKB = "Deutsche Kreditbank AG Kontoauszug Girokonto Buchungen Lastschrift Gutschrift Saldo Kontonummer 1234567"
RV = "R+V Versicherung Hausratversicherung Versicherungsschein Beitragsrechnung Versicherungsnummer 7766554 Ihre Police Schaden"


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def put(self, rel, text="x"):
        p = self.root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding="utf-8")
        return p


class Text(unittest.TestCase):
    def test_tokens_keep_ids_drop_amounts(self):
        self.assertEqual(sort.tokens("DKB Konto 12345678 Betrag 12,50 € am 03.11.2026 Größe"),
                         ["dkb", "konto", "12345678", "betrag", "grösse"])   # casefold: ß -> ss

    def test_year_is_newest_date_not_in_future(self):
        text = "Geboren 1.2.1985, Brief vom 3. November 2026, fällig 01.12.2026, Zeitraum 2025-10-01"
        self.assertEqual(sort.year_of(text, TODAY), 2026)
        self.assertIsNone(sort.year_of("Rechnung Nr. 4711, Betrag 1.234,56", TODAY))
        self.assertEqual(sort.year_of("Stand 31.12.25", TODAY), 2025)

    def test_year_pattern_wins(self):
        rx = sort.re.compile(r"Veranlagungszeitraum\s+(\d{4})")
        self.assertEqual(sort.year_of("Bescheid vom 03.11.2026 Veranlagungszeitraum 2025", TODAY, rx), 2025)
        self.assertIsNone(sort.year_of("Bescheid vom 03.11.2026", TODAY, rx))

    def test_labels_and_groups(self):
        self.assertEqual(sort.label_of("home/20_Finanzen/1_DKB/2024"), "home/20_Finanzen/1_DKB/{year}")
        self.assertEqual(sort.label_of("60_Familie/Maren/Lebenslauf - 2014"), "60_Familie/Maren/Lebenslauf - 2014")
        for n in ("x.doc", "x.odt", "x.pdf", "x_pdfa.pdf", "X.doc.odt"):
            self.assertEqual(sort.group_key(n), "x")


class Config(Tmp):
    def cfg(self, toml):
        self.put("Inbox/.ordnig/rules.toml", toml)
        return sort.load_config(self.root / "Inbox/.ordnig/rules.toml", self.root)

    def test_missing_file_gives_defaults(self):
        cfg = sort.load_config(self.root / "nope.toml", self.root)
        self.assertEqual((cfg["rules"], cfg["thresholds"]["share"], cfg["review"]), ([], 0.7, "_Review"))

    def test_strict_errors_listed_together(self):
        with self.assertRaises(SystemExit) as e:
            self.cfg('typo = 1\n[thresholds]\nshare = 2\n[folders]\nreview = "a/b"\n'
                     '[[rule]]\ntarget = "not/there"\nany = ["("]\n')
        msg = str(e.exception)
        for part in ("typo", "thresholds.share", "folders.review", "folder missing: not/there", "invalid pattern"):
            self.assertIn(part, msg)

    def test_year_target_needs_parent_only(self):
        (self.root / "Bank").mkdir()
        cfg = self.cfg('[[rule]]\ntarget = "Bank/{year}"\nany = "DKB"\nyear = "Jahr (\\\\d{4})"\n')
        self.assertEqual(cfg["rules"][0]["target"], "Bank/{year}")
        with self.assertRaises(SystemExit):
            self.cfg('[[rule]]\ntarget = "{year}/Bank"\nany = "DKB"\n')

    def test_review_folder_is_configurable(self):
        self.assertEqual(self.cfg('[folders]\nreview = "_Prüfen"\n')["review"], "_Prüfen")

    def test_first_matching_rule_wins(self):
        for d in ("Lohn", "Bank"): (self.root / d).mkdir()
        cfg = self.cfg('[[rule]]\ntarget = "Lohn"\nall = ["Baloise", "Verdienstabrechnung"]\n'
                       '[[rule]]\ntarget = "Bank"\nany = ["Baloise"]\nnone = ["Kündigung"]\n')
        pick = lambda t: (sort.match_rule(cfg["rules"], t) or {}).get("target")
        self.assertEqual(pick("Baloise Verdienstabrechnung Oktober"), "Lohn")
        self.assertEqual(pick("Baloise Kontoauszug"), "Bank")
        self.assertIsNone(pick("Baloise Kündigung"))


class Copies(Tmp):
    def setUp(self):
        super().setUp()
        for d in ("Versicherung", "Steuer"): (self.root / d).mkdir()

    def cfg(self, toml):
        self.put("rules.toml", toml)
        return sort.load_config(self.root / "rules.toml", self.root)

    def test_rule_copies_into_tax_year(self):
        cfg = self.cfg('[[rule]]\ntarget = "Versicherung"\nall = ["R\\\\+V", "Beitragsrechnung"]\n'
                       'copy = "Steuer/{year}"\n')
        f = self.put("in/a.txt", f"{RV} vom 05.01.2026")
        r = sort.decide(f, self.root, cfg, None, "deu", TODAY)
        self.assertEqual((r["target"], r["copies"]), ("Versicherung", ["Steuer/2026"]))

    def test_copy_year_pattern(self):
        cfg = self.cfg('[[rule]]\ntarget = "Versicherung"\nany = "Beitragsrechnung"\n'
                       'copy = ["Steuer/{year}"]\ncopy_year = "Beitragsjahr (\\\\d{4})"\n')
        r = sort.decide(self.put("in/a.txt", f"{RV} Beitragsjahr 2025 vom 05.01.2026"),
                        self.root, cfg, None, "deu", TODAY)
        self.assertEqual(r["copies"], ["Steuer/2025"])
        r = sort.decide(self.put("in/b.txt", f"{RV} vom 05.01.2026"), self.root, cfg, None, "deu", TODAY)
        self.assertEqual((r["target"], r["reason"]), (None, "no year found for the copy to Steuer/{year}"))

    def test_copy_config_errors(self):
        with self.assertRaises(SystemExit) as e:
            self.cfg('[[rule]]\ntarget = "Versicherung"\nany = "x"\ncopy = "Missing/{year}"\n'
                     'copy_year = "no group"\n')
        self.assertIn("folder missing: Missing", str(e.exception))
        self.assertIn("needs a group", str(e.exception))

    def test_copy_safely_keeps_original(self):
        a = self.put("Versicherung/a.txt", "inhalt")
        dst = self.root / "Steuer/a.txt"
        sort.copy_safely(a, dst)
        self.assertEqual((a.read_text(), dst.read_text()), ("inhalt", "inhalt"))
        with self.assertRaises(FileExistsError):
            sort.copy_safely(a, dst)


class Classify(Tmp):
    def setUp(self):
        super().setUp()
        for i in range(4):
            self.put(f"Bank/{2024 + i % 2}/a{i}.txt", f"{DKB} Datum 0{i + 1}.03.{2024 + i % 2} Brief {i}")
            self.put(f"Versicherung/b{i}.txt", f"{RV} Schreiben {i}")
        self.put("Bank/2024/a0_pdfa.pdf", "not a real PDF")      # version of the same document
        self.put("Fotos/urlaub.jpg", "bild")
        self.inbox = self.root / "Inbox"; self.inbox.mkdir()
        self.cfg = sort.load_config(self.root / "nope.toml", self.root)

    def learn(self):
        docs, groups = sort.learn(self.root, self.inbox, self.cfg, self.root / "cache.json.gz", "deu", 1)
        return docs, groups

    def test_learns_one_text_per_document_skips_inbox_and_photos(self):
        self.put("Inbox/neu.txt", DKB)
        docs, _ = self.learn()
        self.assertEqual(sorted(d["label"] for d in docs), ["Bank/{year}"] * 4 + ["Versicherung"] * 4)

    def test_files_new_letter_with_year(self):
        f = self.put("Inbox/neu.txt", f"{DKB} Datum 03.11.2026")
        docs, _ = self.learn()
        r = sort.decide(f, self.root, self.cfg, sort.Index(docs), "deu", TODAY)
        self.assertEqual(r["target"], "Bank/2026")

    def test_unsure_and_unreadable_go_to_review(self):
        docs, _ = self.learn()
        index = sort.Index(docs)
        r = sort.decide(self.put("Inbox/x.txt", "Einladung Sommerfest Verein Grillen Musik Mitglieder Samstag Abend"),
                        self.root, self.cfg, index, "deu", TODAY)
        self.assertIsNone(r["target"])
        r = sort.decide(self.put("Inbox/x.zip", "PK"), self.root, self.cfg, index, "deu", TODAY)
        self.assertIn("not readable", r["reason"])
        r = sort.decide(self.put("Inbox/y.txt", f"{DKB} ohne Datum"), self.root, self.cfg, index, "deu", TODAY)
        self.assertEqual((r["target"], r["reason"]), (None, "no year found for Bank/{year}"))


class MountGuard(Tmp):
    """An unmounted share must stop the run, not wipe what was learned (texts.json.gz)."""
    def setUp(self):
        super().setUp()
        for i in range(30):
            self.put(f"Bank/{i}.txt", f"{DKB} Brief {i}")
        self.inbox = self.root / "Inbox"; self.inbox.mkdir()
        self.cfg = sort.load_config(self.root / "nope.toml", self.root)
        self.cache = self.root / "Inbox/.ordnig/texts.json.gz"

    def learn(self, **kw):
        return sort.learn(self.root, self.inbox, self.cfg, self.cache, "deu", 1, **kw)

    def test_stops_when_most_known_documents_are_gone(self):
        self.learn()
        size = self.cache.stat().st_size
        for i in range(20):
            (self.root / f"Bank/{i}.txt").unlink()
        with self.assertRaises(SystemExit) as e:
            self.learn()
        self.assertIn("only 10 of 30", str(e.exception))
        self.assertEqual(self.cache.stat().st_size, size)          # cache untouched
        docs, _ = self.learn(changed_ok=True)                      # on purpose: learn anew
        self.assertEqual(len(docs), 10)

    def test_small_changes_and_first_run_pass(self):
        for i in range(25, 30):
            (self.root / f"Bank/{i}.txt").unlink()
        self.learn()                                               # first run: nothing known yet
        for i in range(15, 25):
            (self.root / f"Bank/{i}.txt").unlink()
        docs, _ = self.learn()                                     # 15 of 25 left: more than half
        self.assertEqual(len(docs), 15)

    def test_empty_mount_point_stops(self):
        (self.root / "documents").mkdir()
        real = sort.os.path.ismount
        sort.os.path.ismount = lambda p: Path(p) == self.root / "documents"
        try:
            with self.assertRaises(SystemExit) as e:
                sort.check_mounts(self.root)
            self.assertIn("mounted", str(e.exception))
            self.put("documents/a.txt")
            sort.check_mounts(self.root)
        finally:
            sort.os.path.ismount = real


class Move(Tmp):
    def test_never_overwrites(self):
        a = self.put("in/a.txt", "neu"); self.put("out/a.txt", "alt")
        dst = sort.free_name(self.root / "out", "a.txt", set())
        self.assertEqual(dst.name, "a_2.txt")
        reserved = {str(dst).casefold()}
        self.assertEqual(sort.free_name(self.root / "out", "a.txt", reserved).name, "a_3.txt")
        sort.move_safely(a, dst)
        self.assertEqual(((self.root / "out/a.txt").read_text(), dst.read_text(), a.exists()), ("alt", "neu", False))
        with self.assertRaises(FileExistsError):
            sort.move_safely(self.put("in/b.txt"), self.root / "out/a.txt")

    def test_log_appends_with_header_once(self):
        log = self.root / ".ordnig/sort-log.csv"
        r = {"file": Path("a.pdf"), "status": "filed", "to": "Bank/2026/a.pdf",
             "copied_to": ["Steuer/2026/a.pdf"], "source": "rule DKB", "reason": "", "sha256": "ab12"}
        sort.append_log(log, [r], "2026-11-17 08:00:00")
        sort.append_log(log, [r], "2026-11-18 08:00:00")
        lines = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], ",".join(sort.LOG_HEADER))
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[1].endswith(",ab12"))

    def test_log_of_an_older_version_stops(self):
        log = self.root / ".ordnig/sort-log.csv"; log.parent.mkdir()
        log.write_text("time,file,status,to,copies,source,reason\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as e:
            sort.check_log(log)
        self.assertEqual(e.exception.code, 2)
        sort.check_log(self.root / ".ordnig/missing.csv")                 # no log yet: fine

    def test_duplicate_detected_by_content(self):
        self.put("out/x.pdf", "same"); src = self.put("in/y.pdf", "same")
        self.assertEqual(sort.same_file_in(self.root / "out", src).name, "x.pdf")
        self.assertIsNone(sort.same_file_in(self.root / "missing", src))


if __name__ == "__main__":
    unittest.main()
