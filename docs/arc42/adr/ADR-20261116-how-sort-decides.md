# How `sort` decides

Status: Accepted (2026-11-24, proposed 2026-11-16)

## Decision

1. **Classes from the archive:** every folder of the archive is a class and its
   documents are examples; year folders become `{year}`.
2. **Rules first:** the first matching rule wins; otherwise similarity decides.
3. **Similarity:** TF-IDF with cosine and k = 7 nearest neighbours, written with the
   Python standard library.
4. **Unsure → `_Review`:** unsure, unreadable, without a year for `{year}`, or already
   filed → `<inbox>/_Review/` with the reason. A wrong configuration stops the run
   with all errors listed.

No LLM: a fallback comes only if `--eval` and real use show many files in `_Review`
([#15](https://github.com/Tiliavir/ordnig/issues/15)).

## Why

1. The archive already is the labelled data; no hand-made taxonomy to maintain, and it
   adapts to any archive's structure.
2. Rules are predictable like mail filters; an LLM adds cost and data flow without
   proven need.
3. No new dependency (scikit-learn), and every decision names the most similar
   archived document.
4. Nothing is guessed; a misfiled letter is worse than one to look at.
