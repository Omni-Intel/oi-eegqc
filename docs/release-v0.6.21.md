# EEGQC 0.6.21

- A single folder/ZIP selection can contain both single-image RSVP and
  dual-image RSVP sessions. Preparation automatically groups sessions by their
  metadata, without asking the operator to split the source folder.
- Each group retains its registered paradigm code, COS destination, upload
  identity and platform statistics. This combines the operator workflow, not
  the two scientific paradigms or their storage prefixes.
- Mixed queues persist across restarts and reuse canonical per-paradigm
  receipts and pending upload IDs. A failed group does not prevent other
  groups from uploading and committing their successful sessions.
- The upload panel shows automatic routing and both destinations. Conflicting
  windows are locked before preparation; completed groups are skipped on retry.
- For recognized session archives, the upload worker checks completed local
  receipts before deciding which recordings still need scoring.

The original archives and session metadata are not rewritten. Unidentified or
overlapping sessions are rejected with a diagnostic instead of being silently
assigned to the currently selected paradigm.
