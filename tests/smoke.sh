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
if command -v verapdf >/dev/null; then
  bad=$(awk -F, '$2=="pdfa" && $5!="2u"' "$log")
  [[ -z $bad ]] || { echo "$bad"; fail "not all PDFs are PDF/A-2u"; }
fi

# second run must not touch anything
before=$(find "$ORDNIG_SRC" -type f | wc -l)
ordnig run --images png >/dev/null
[[ $(find "$ORDNIG_SRC" -type f | wc -l) == "$before" ]] || fail "second run created files"

ordnig verify >/dev/null
grep -q 'Inhalt geändert: 0' "$ORDNIG_OUT"/*-verify/report.md || fail "verify reports changes"
grep -q '├── Briefe/' "$ORDNIG_OUT"/*-after/report.md || fail "tree missing in report"
grep -q '✓ kuendigung.odt' "$ORDNIG_OUT"/*-migration.md || fail "tree missing in migration report"

echo "--- migration log"; cut -d, -f2,4-6 "$log"
echo "SMOKE TEST OK"
