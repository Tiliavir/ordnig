# k nearest neighbours in the standard library

Status: Proposed (2026-11-16)

## Decision

Similarity is TF-IDF with cosine and k = 7 nearest neighbours, written with the Python standard library.

## Why

No new dependency (scikit-learn), and every decision names the most similar archived document.
