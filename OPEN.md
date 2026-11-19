# Open questions

## sort (0.7.0)

1. Thresholds (`anteil` 0.7, `aehnlichkeit` 0.2) are a first guess – set them from `sort --eval`.
2. LLM fallback for `_Prüfen` files: only if `--eval` and real use show it is needed.
3. `run` does not skip `00_Eingang`: it would put PDF/A and ODF copies next to unsorted mail,
   and `sort` would then file original and copy separately. Skip the inbox in `run`?

Settled 2026-11-16: inbox is `home/00_Eingang` (shares mounted as `/ablage/home` and
`/ablage/documents`; found automatically since 0.8); new tax mail goes to
`documents`; file names stay as delivered.
