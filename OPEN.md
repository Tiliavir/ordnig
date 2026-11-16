# Open questions

## sort (0.7.0)

1. Where does `00_Eingang` live, and are `home` and `documents` mounted together
   (`-v …/home:/ablage/home -v …/documents:/ablage/documents`)?
2. Both trees have `20_Finanzen/0_Steuer` and `2_Versicherungen/AllianzDirect`.
   Which one gets new mail? Until decided, similarity may pick either – a rule settles it.
3. File names: keep as delivered (current), or a scheme like `YYYY-MM-DD_<Absender>_<Betreff>`?
4. Thresholds (`anteil` 0.7, `aehnlichkeit` 0.2) are a first guess – set them from `sort --eval`.
5. LLM fallback for `_Prüfen` files: only if `--eval` and real use show it is needed.
