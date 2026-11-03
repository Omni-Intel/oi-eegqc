# EEGQC 0.6.23

- Every scored segment now carries an `evidence` block produced from the recording itself:
  measured window length, window-level usable time (`usable_duration_s`), usable ratio,
  duration profile, letter grade and ODQ. Results can be audited without the platform.
- The result reports `recording_identity`: sha256 of the scored file, byte length, sampling
  rate, channel count, measured duration and the EDF+ header fields that identify the
  amplifier (equipment / device serial / recording id) when the file provides them.
- A `boundary` check compares each claimed segment start with the markers actually present
  in the recording. When the file carries annotations, a mismatch greater than 1 s is
  reported instead of silently trusted; files without annotations say so explicitly.
- Session summaries add `usable_duration_s` (sum of window-level usable time) and
  `scored_duration_s` (sum of measured segment windows), so effective acquisition time no
  longer has to be inferred from the plan.
- No change to the scoring algorithm (`oi-eegqc-score-v7`) or to the pass threshold (60).

Tests: 310 passed.
