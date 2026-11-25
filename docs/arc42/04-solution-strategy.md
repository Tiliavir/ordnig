# 4 Solution strategy

| Goal | Approach |
| --- | --- |
| No data loss | Copies next to the original, never in place; moves only after a SHA-256 check; `preview` is the default. |
| Long-lasting formats | ODF for office files, PDF/A-2u with OCR text layer for everything readable as a page. |
| Honest results | veraPDF decides between 2u and 2b; `sort` files only above thresholds, else the review folder with a reason. |
| Filing without a taxonomy | The archive's folders are the classes; rules for what similarity cannot know. |
| Adaptable | Every installation-specific choice is configuration: mounts, inbox, review folder, `rules.toml`, languages, thresholds. |
| Unattended runs | State on the NAS, lock, format version, mount guard, exit codes. |
| Low maintenance | Stdlib Python, distribution packages, one image, CI smoke test on both architectures. |

Order of decisions for incoming documents: **rules → similarity → review folder**.

An LLM fallback is out of scope for now ([#15](https://github.com/Tiliavir/ordnig/issues/15)).
