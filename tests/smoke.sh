#!/usr/bin/env bash
# End-to-end test: builds a small fake archive, runs `ordnig run`, asserts the results.
# In CI it runs inside the image; locally: ORDNIG_LIB=src PATH=src:$PATH tests/smoke.sh
set -euo pipefail

W=$(mktemp -d)
export ORDNIG_SRC=$W/ablage ORDNIG_OUT=$W/report
mkdir -p "$ORDNIG_SRC/Briefe" "$ORDNIG_SRC/Scans" "$W/gen"
cd "$W/gen"

printf 'Sehr geehrte Damen und Herren,\n\nhiermit kündige ich zum 31. März.\nGrüße\n' > brief.txt
printf 'Jahr,Betrag\n2004,123.45\n2005,99\n' > konto.csv
printf 'Rechnung Nr. 4711\nGesamtbetrag 1234,56 DM\n' > scan.txt
soffice --headless --convert-to doc brief.txt >/dev/null
soffice --headless --convert-to rtf brief.txt >/dev/null
soffice --headless --convert-to xls konto.csv >/dev/null
soffice --headless --convert-to pdf scan.txt >/dev/null
soffice --headless --convert-to 'pdf:writer_pdf_Export:{"SelectPdfVersion":{"type":"long","value":"2"}}' --outdir pdfa brief.txt >/dev/null
# image-only "scan": render text PDF to a 300 dpi bitmap, wrap it again
gs -q -dNOPAUSE -dBATCH -sDEVICE=pnggray -r300 -o scan.png scan.pdf
img2pdf scan.png -o "$ORDNIG_SRC/Scans/scan_ohne_text.pdf"
cp scan.png "$ORDNIG_SRC/Scans/rechnung.png"
cp brief.doc "$ORDNIG_SRC/Briefe/kuendigung.doc"
cp pdfa/brief.pdf "$ORDNIG_SRC/Briefe/schon_pdfa.pdf"      # already PDF/A with text -> must stay alone
cp brief.doc "$ORDNIG_SRC/Briefe/doppelt.doc"
cp brief.rtf "$ORDNIG_SRC/Briefe/doppelt.rtf"
cp konto.xls "$ORDNIG_SRC/Briefe/konto.xls"
sha_before=$(sha256sum "$ORDNIG_SRC/Briefe/kuendigung.doc")

ordnig version >/dev/null
ordnig preview > "$W/preview.txt"
grep -q 'Vorschlag: ordnig run' "$W/preview.txt" || { cat "$W/preview.txt"; echo "FAIL: no run suggestion"; exit 1; }
grep -q 'Falls Scans: --images png' "$W/preview.txt" || { cat "$W/preview.txt"; echo "FAIL: png not hinted"; exit 1; }
[[ -z $(find "$ORDNIG_SRC" -newer "$ORDNIG_SRC/Briefe/konto.xls" -type f) ]] || { echo "FAIL: preview wrote files"; exit 1; }

ordnig run --images png

fail() { echo "FAIL: $*"; exit 1; }
for f in Briefe/kuendigung.odt Briefe/kuendigung.pdf Briefe/konto.ods Briefe/konto.pdf \
         Briefe/doppelt.odt Briefe/doppelt.rtf.odt Scans/scan_ohne_text_pdfa.pdf Scans/rechnung.pdf; do
  [[ -s $ORDNIG_SRC/$f ]] || fail "missing $f"
done
[[ $(sha256sum "$ORDNIG_SRC/Briefe/kuendigung.doc") == "$sha_before" ]] || fail "original changed"
[[ ! -e $ORDNIG_SRC/Briefe/schon_pdfa_pdfa.pdf ]] || fail "existing PDF/A was converted again"
python3 -I -c 'import pikepdf,sys; p = pikepdf.open(sys.argv[1]); sys.exit(0 if "/StructTreeRoot" in p.Root else 1)' \
  "$ORDNIG_SRC/Briefe/kuendigung.pdf" || fail "office PDF lost its tagging"
grep -q 'Langsamste Dateien' "$ORDNIG_OUT"/*-migration.md || fail "timing missing in report"

ocr=$(gs -q -dNOPAUSE -dBATCH -sDEVICE=txtwrite -o - "$ORDNIG_SRC/Scans/scan_ohne_text_pdfa.pdf")
grep -q 4711 <<<"$ocr" || fail "OCR text missing"
gs -q -dNOPAUSE -dBATCH -sDEVICE=txtwrite -o - "$ORDNIG_SRC/Briefe/kuendigung.pdf" | grep -q 'kündige' \
  || fail "umlauts lost in office PDF"

log=$ORDNIG_SRC/.ordnig/migration_log.csv
grep -q ',fehler,' "$log" && { cat "$log"; fail "conversion errors"; }
grep -q 'Einzelprüfung' "$log" && { cat "$log"; fail "veraPDF batch output not parsed"; }
if command -v verapdf >/dev/null; then
  # newest row per target wins (pending row first, verdict appended after the veraPDF batch)
  bad=$(awk -F, '$2=="pdfa" {last[$3]=$0; v[$3]=$5} END {for (k in v) if (v[k] != "2u") print last[k]}' "$log")
  [[ -z $bad ]] || { echo "$bad"; fail "not all PDFs are PDF/A-2u"; }
fi

# second run must not touch anything
before=$(find "$ORDNIG_SRC" -type f | wc -l)
ordnig run --images png >/dev/null
[[ $(find "$ORDNIG_SRC" -type f | wc -l) == "$before" ]] || fail "second run created files"

ordnig verify >/dev/null
grep -q 'Inhalt geändert: 0' "$ORDNIG_OUT"/*-verify/report.md || fail "verify reports changes"
[[ -s $ORDNIG_SRC/.ordnig/version.json ]] || fail "run: missing .ordnig/version.json"
[[ -s $ORDNIG_SRC/.ordnig/manifest.sha256 ]] || fail "run: checksums not stored in .ordnig"
[[ ! -e $ORDNIG_OUT/manifest.sha256 ]] || fail "run: checksums stored in /report"
grep -q '├── Briefe/' "$ORDNIG_OUT"/*-after/report.md || fail "tree missing in report"
grep -q '✓ kuendigung.odt' "$ORDNIG_OUT"/*-migration.md || fail "tree missing in migration report"

# ---------------------------------------------------------------- sort: inbox -> archive
S=$W/sort/ablage
mkdir -p "$S/Bank/2025" "$S/Versicherung" "$S/Steuer" "$S/00_Eingang/.ordnig" "$W/sortgen"
cd "$W/sortgen"
bank="Deutsche Kreditbank AG Kontoauszug Girokonto Buchungen Lastschrift Gutschrift Saldo Kontonummer 1234567"
vers="Hausratversicherung Versicherungsschein Beitragsrechnung Versicherungsnummer 7766554 Ihre Police Schaden"
for i in 1 2 3 4; do
  printf '%s\nDatum 0%s.03.2025\nBrief %s\n' "$bank" "$i" "$i" > "konto$i.txt"
  printf '%s\nSchreiben %s mit freundlichen Grüßen\n' "$vers" "$i" > "vers$i.txt"
done
printf '%s\nDatum 03.11.2026\n' "$bank" > neu.txt
printf '%s\nJahresbeitrag Datum 05.01.2026\n' "$vers" > jahr.txt
soffice --headless --convert-to pdf konto*.txt vers*.txt neu.txt jahr.txt >/dev/null
mv konto*.pdf "$S/Bank/2025/"; mv vers*.pdf "$S/Versicherung/"
cp neu.pdf "$S/00_Eingang/auszug.pdf"
cp jahr.pdf "$S/00_Eingang/beitrag.pdf"
cat > "$S/00_Eingang/.ordnig/regeln.toml" <<'EOF'
[[regel]]
name = "Jahresbeitrag"
ziel = "Versicherung"
alle = ['Hausratversicherung', 'Jahresbeitrag']
kopie = "Steuer/{jahr}"
EOF
gs -q -dNOPAUSE -dBATCH -sDEVICE=pnggray -r300 -o "$S/00_Eingang/scan.png" neu.pdf
cp "$S/Bank/2025/konto1.pdf" "$S/00_Eingang/doppelt.pdf"
printf 'PK\005\006%018d' 0 > "$S/00_Eingang/archiv.zip"

# exit code 4: done, but files wait in _Prüfen (here: the duplicate and the broken zip)
rc=0; ORDNIG_SRC=$S ordnig sort > "$W/sort-preview.txt" || rc=$?
[[ $rc == 4 ]] || { cat "$W/sort-preview.txt"; fail "sort preview: exit $rc, expected 4"; }
[[ -e $S/00_Eingang/auszug.pdf && ! -e $S/Bank/2026 ]] || fail "sort preview moved files"

# exit code 3: another run holds the lock
python3 -I -c 'import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT); fcntl.lockf(fd, fcntl.LOCK_EX)
print("locked", flush=True); time.sleep(120)' "$S/00_Eingang/.ordnig/lock" > "$W/holder" &
holder=$!
for _ in $(seq 100); do [[ -s $W/holder ]] && break; sleep 0.1; done
rc=0; ORDNIG_SRC=$S ordnig sort > "$W/sort-locked.txt" 2>&1 || rc=$?
kill "$holder"; wait "$holder" 2>/dev/null || true
[[ $rc == 3 ]] || { cat "$W/sort-locked.txt"; fail "sort while locked: exit $rc, expected 3"; }

rc=0; ORDNIG_SRC=$S ordnig sort --apply > "$W/sort.txt" || rc=$?
[[ $rc == 4 ]] || { cat "$W/sort.txt"; fail "sort --apply: exit $rc, expected 4"; }
for f in Versicherung/beitrag.pdf Steuer/2026/beitrag.pdf Bank/2026/auszug.pdf Bank/2026/scan.png 00_Eingang/_Prüfen/doppelt.pdf 00_Eingang/_Prüfen/archiv.zip; do
  [[ -s $S/$f ]] || { cat "$W/sort.txt" "$ORDNIG_OUT"/*-sort.csv; fail "sort: missing $f"; }
done
[[ -z $(find "$S/00_Eingang" -maxdepth 1 -type f) ]] || fail "sort left files in the inbox"
for f in texte.json.gz protokoll.csv version.json; do
  [[ -s $S/00_Eingang/.ordnig/$f ]] || fail "sort: missing 00_Eingang/.ordnig/$f"
done
ORDNIG_SRC=$S ordnig sort --eval | grep -q 'Jahr richtig' || fail "sort --eval"

# check: the copy made by the rule is intended, not a duplicate finding
mkdir -p "$W/check-sort"
ORDNIG_SRC=$S ORDNIG_OUT=$W/check-sort ordnig check > /dev/null
rep=$(echo "$W"/check-sort/*-check/report.md)
grep 'beitrag.pdf' "$rep" | grep -q 'gewollte Kopie (Regel)' || fail "check: rule copy not marked as intended"
! grep 'beitrag.pdf' "$rep" | grep -q 'Duplikat (Gruppe' || fail "check: rule copy reported as duplicate"

# verify takes over a baseline from /report (up to 0.8)
mv "$ORDNIG_SRC/.ordnig/manifest.sha256" "$ORDNIG_OUT/manifest.sha256"
ordnig verify > /dev/null 2> "$W/adopt.txt" || { cat "$W/adopt.txt"; fail "verify with the old baseline"; }
[[ -s $ORDNIG_SRC/.ordnig/manifest.sha256 ]] || fail "old baseline not taken over"
grep -q übernommen "$W/adopt.txt" || fail "taking over the old baseline not reported"

# verify: a changed file is exit code 1 (for cron/systemd)
echo x >> "$ORDNIG_SRC/Briefe/konto.xls"
rc=0; ordnig verify > /dev/null || rc=$?
[[ $rc == 1 ]] || fail "verify with a changed file: exit $rc, expected 1"

echo "--- migration log (newest row per target)"
awk -F, 'NR>1 {k=$2 FS $3; row[k]=$2 FS $4 FS $5 FS $6 FS $8} END {for (k in row) print row[k]}' "$log" | sort
echo "SMOKE TEST OK"
