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
        self.assertEqual(sort.label_of("home/20_Finanzen/1_DKB/2024"), "home/20_Finanzen/1_DKB/{jahr}")
        self.assertEqual(sort.label_of("60_Familie/Maren/Lebenslauf - 2014"), "60_Familie/Maren/Lebenslauf - 2014")
        for n in ("x.doc", "x.odt", "x.pdf", "x_pdfa.pdf", "X.doc.odt"):
            self.assertEqual(sort.group_key(n), "x")


class Config(Tmp):
    def cfg(self, toml):
        self.put("00_Eingang/.ordnig/regeln.toml", toml)
        return sort.load_config(self.root / "00_Eingang/.ordnig/regeln.toml", self.root)

    def test_missing_file_gives_defaults(self):
        cfg = sort.load_config(self.root / "nope.toml", self.root)
        self.assertEqual((cfg["regeln"], cfg["schwellen"]["anteil"]), ([], 0.7))

    def test_strict_errors_listed_together(self):
        with self.assertRaises(SystemExit) as e:
            self.cfg('tippfehler = 1\n[schwellen]\nanteil = 2\n'
                     '[[regel]]\nziel = "gibts/nicht"\nmuster = ["("]\n')
        msg = str(e.exception)
        for part in ("tippfehler", "schwellen.anteil", "Ordner fehlt: gibts/nicht", "ungültiges Muster"):
            self.assertIn(part, msg)

    def test_year_target_needs_parent_only(self):
        (self.root / "Bank").mkdir()
        cfg = self.cfg('[[regel]]\nziel = "Bank/{jahr}"\nmuster = "DKB"\njahr = "Jahr (\\\\d{4})"\n')
        self.assertEqual(cfg["regeln"][0]["ziel"], "Bank/{jahr}")
        with self.assertRaises(SystemExit):
            self.cfg('[[regel]]\nziel = "{jahr}/Bank"\nmuster = "DKB"\n')

    def test_first_matching_rule_wins(self):
        for d in ("Lohn", "Bank"): (self.root / d).mkdir()
        cfg = self.cfg('[[regel]]\nziel = "Lohn"\nalle = ["Baloise", "Verdienstabrechnung"]\n'
                       '[[regel]]\nziel = "Bank"\nmuster = ["Baloise"]\nnicht = ["Kündigung"]\n')
        pick = lambda t: (sort.match_rule(cfg["regeln"], t) or {}).get("ziel")
        self.assertEqual(pick("Baloise Verdienstabrechnung Oktober"), "Lohn")
        self.assertEqual(pick("Baloise Kontoauszug"), "Bank")
        self.assertIsNone(pick("Baloise Kündigung"))


class Copies(Tmp):
    def setUp(self):
        super().setUp()
        for d in ("Versicherung", "Steuer"): (self.root / d).mkdir()

    def cfg(self, toml):
        self.put("regeln.toml", toml)
        return sort.load_config(self.root / "regeln.toml", self.root)

    def test_rule_copies_into_tax_year(self):
        cfg = self.cfg('[[regel]]\nziel = "Versicherung"\nalle = ["R\\\\+V", "Beitragsrechnung"]\n'
                       'kopie = "Steuer/{jahr}"\n')
        f = self.put("in/a.txt", f"{RV} vom 05.01.2026")
        r = sort.decide(f, self.root, cfg, None, "deu", TODAY)
        self.assertEqual((r["ziel"], r["kopien"]), ("Versicherung", ["Steuer/2026"]))

    def test_kopie_jahr_pattern(self):
        cfg = self.cfg('[[regel]]\nziel = "Versicherung"\nmuster = "Beitragsrechnung"\n'
                       'kopie = ["Steuer/{jahr}"]\nkopie_jahr = "Beitragsjahr (\\\\d{4})"\n')
        r = sort.decide(self.put("in/a.txt", f"{RV} Beitragsjahr 2025 vom 05.01.2026"),
                        self.root, cfg, None, "deu", TODAY)
        self.assertEqual(r["kopien"], ["Steuer/2025"])
        r = sort.decide(self.put("in/b.txt", f"{RV} vom 05.01.2026"), self.root, cfg, None, "deu", TODAY)
        self.assertEqual((r["ziel"], r["grund"]), (None, "kein Jahr erkannt für Kopie nach Steuer/{jahr}"))

    def test_copy_config_errors(self):
        with self.assertRaises(SystemExit) as e:
            self.cfg('[[regel]]\nziel = "Versicherung"\nmuster = "x"\nkopie = "Fehlt/{jahr}"\n'
                     'kopie_jahr = "ohne Gruppe"\n')
        self.assertIn("Ordner fehlt: Fehlt", str(e.exception))
        self.assertIn("braucht eine Gruppe", str(e.exception))

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
        self.put("Bank/2024/a0_pdfa.pdf", "kein echtes PDF")      # Fassung desselben Dokuments
        self.put("Fotos/urlaub.jpg", "bild")
        self.inbox = self.root / "00_Eingang"; self.inbox.mkdir()
        self.cfg = sort.load_config(self.root / "nope.toml", self.root)

    def learn(self):
        docs, groups = sort.learn(self.root, self.inbox, self.cfg, self.root / "cache.json.gz", "deu", 1)
        return docs, groups

    def test_learns_one_text_per_document_skips_inbox_and_photos(self):
        self.put("00_Eingang/neu.txt", DKB)
        docs, _ = self.learn()
        self.assertEqual(sorted(d["label"] for d in docs), ["Bank/{jahr}"] * 4 + ["Versicherung"] * 4)

    def test_files_new_letter_with_year(self):
        f = self.put("00_Eingang/neu.txt", f"{DKB} Datum 03.11.2026")
        docs, _ = self.learn()
        r = sort.decide(f, self.root, self.cfg, sort.Index(docs), "deu", TODAY)
        self.assertEqual(r["ziel"], "Bank/2026")

    def test_unsure_and_unreadable_go_to_review(self):
        docs, _ = self.learn()
        index = sort.Index(docs)
        r = sort.decide(self.put("00_Eingang/x.txt", "Einladung Sommerfest Verein Grillen Musik Mitglieder Samstag Abend"),
                        self.root, self.cfg, index, "deu", TODAY)
        self.assertIsNone(r["ziel"])
        r = sort.decide(self.put("00_Eingang/x.zip", "PK"), self.root, self.cfg, index, "deu", TODAY)
        self.assertIn("nicht lesbar", r["grund"])
        r = sort.decide(self.put("00_Eingang/y.txt", f"{DKB} ohne Datum"), self.root, self.cfg, index, "deu", TODAY)
        self.assertEqual((r["ziel"], r["grund"]), (None, "kein Jahr erkannt für Bank/{jahr}"))


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

    def test_duplicate_detected_by_content(self):
        self.put("out/x.pdf", "same"); src = self.put("in/y.pdf", "same")
        self.assertEqual(sort.same_file_in(self.root / "out", src).name, "x.pdf")
        self.assertIsNone(sort.same_file_in(self.root / "missing", src))


if __name__ == "__main__":
    unittest.main()
