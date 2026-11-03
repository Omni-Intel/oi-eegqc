# EEGQC 0.7.0 — two verdicts, one number

- Every scored segment now ends in exactly one of two states: `usable` or `unusable`.
  `needs_review`, `retry` and `accepted_unscored` are gone.
- A segment is `usable` when at least 60 % of its 2-second windows are usable; when more than
  half are not, the segment is marked `void` and has to be recorded again.
- `usable_duration_s` per segment = measured duration × usable-window ratio (0 when void).
  The segment score is that same ratio × 100, so score and duration can never disagree.
- A segment that cannot be evaluated is reported as `unusable` with score 0 and a reason —
  never as “pending”, so uploads never wait for a second opinion.
- The result carries `rules` (`pass_ratio`, `void_ratio`) and a summary of
  `usable` / `unusable` / `void` counts plus `usable_duration_s`.
- Algorithm version: `oi-eegqc-score-v8`.

Tests: 310 collected, 308 passed, 1 flaky QML test (passes in isolation), 1 updated to the new
algorithm version.