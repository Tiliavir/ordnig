#!/usr/bin/env bash
# End-to-end test: builds a small fake archive, runs `archive-validator run`, asserts the results.
# In CI it runs inside the image; locally: AV_LIB=src PATH=src:$PATH tests/smoke.sh
set -euo pipefail

W=$(mktemp -d)
export AV_SRC=$W/ablage AV_OUT=$W/report
mkdir -p "$AV_SRC/Briefe" "$AV_SRC/Scans" "$W/gen"
cd "$W/gen"

printf 'Sehr geehrte Damen und Herren,\n\nhiermit kündige ich zum 31. März.\nGrüße\n' > brief.txt
printf 'Jahr,Betrag\n2004,123.45\n2005,99\n' > konto.csv
printf 'Rechnung Nr. 4711\nGesamtbetrag 1234,56 DM\n' > scan.txt
soffice --headless --convert-to doc brief.txt >/dev/null
soffice --headless --convert-to rtf brief.txt >/dev/null
soffice --headless --convert-to xls konto.csv >/dev/null
soffice --headless --convert-to pdf scan.txt >/dev/null
# image-only "scan": render text PDF to a 300 dpi bitmap, wrap it again
gs -q -dNOPAUSE -dBATCH -sDEVICE=pnggray -r300 -o scan.png scan.pdf
img2pdf scan.png -o "$AV_SRC/Scans/scan_ohne_text.pdf"
cp scan.png "$AV_SRC/Scans/rechnung.png"
cp brief.doc "$AV_SRC/Briefe/kuendigung.doc"
cp brief.doc "$AV_SRC/Briefe/doppelt.doc"
cp brief.rtf "$AV_SRC/Briefe/doppelt.rtf"
cp konto.xls "$AV_SRC/Briefe/konto.xls"
sha_before=$(sha256sum "$AV_SRC/Briefe/kuendigung.doc")

archive-validator preview >/dev/null
[[ -z $(find "$AV_SRC" -newer "$AV_SRC/Briefe/konto.xls" -type f) ]] || { echo "FAIL: preview wrote files"; exit 1; }

archive-validator run --images png

fail() { echo "FAIL: $*"; exit 1; }
for f in Briefe/kuendigung.odt Briefe/kuendigung.pdf Briefe/konto.ods Briefe/konto.pdf \
         Briefe/doppelt.odt Briefe/doppelt.rtf.odt Scans/scan_ohne_text_pdfa.pdf Scans/rechnung.pdf; do
  [[ -s $AV_SRC/$f ]] || fail "missing $f"
done
[[ $(sha256sum "$AV_SRC/Briefe/kuendigung.doc") == "$sha_before" ]] || fail "original changed"

ocr=$(gs -q -dNOPAUSE -dBATCH -sDEVICE=txtwrite -o - "$AV_SRC/Scans/scan_ohne_text_pdfa.pdf")
grep -q 4711 <<<"$ocr" || fail "OCR text missing"
gs -q -dNOPAUSE -dBATCH -sDEVICE=txtwrite -o - "$AV_SRC/Briefe/kuendigung.pdf" | grep -q 'kündige' \
  || fail "umlauts lost in office PDF"

log=$AV_SRC/.archive-validator/migration_log.csv
grep -q ',fehler,' "$log" && { cat "$log"; fail "conversion errors"; }
if command -v verapdf >/dev/null; then
  bad=$(awk -F, '$2=="pdfa" && $5!="2u"' "$log")
  [[ -z $bad ]] || { echo "$bad"; fail "not all PDFs are PDF/A-2u"; }
fi

# second run must not touch anything
before=$(find "$AV_SRC" -type f | wc -l)
archive-validator run --images png >/dev/null
[[ $(find "$AV_SRC" -type f | wc -l) == "$before" ]] || fail "second run created files"

archive-validator verify >/dev/null
grep -q 'Geändert.*: 0' "$AV_OUT"/*-verify/report.md || fail "verify reports changes"

echo "--- migration log"; cut -d, -f2,4-6 "$log"
echo "SMOKE TEST OK"
