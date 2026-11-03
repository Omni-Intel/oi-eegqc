# EEGQC 0.6.22

- Each scored segment now reports `duration_s` (the actual scored window length, rounded to
  1 ms). The capture client forwards it to the platform, which uses it to compute the
  effective acquisition time from the real window instead of the catalog's nominal clip
  length.
- Segment state semantics are unchanged: `passed` / `retry` (below threshold but fully
  recorded) / `needs_review` (window could not be evaluated). The distinction now matters
  downstream: only `needs_review` is deducted from effective time.

This release carries no change to the scoring algorithm or the pass threshold (60).
