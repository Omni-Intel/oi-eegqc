# EEGQC 0.7.2

- New clients advertise NAS support and identify their prepared upload destination on every signing request. A resumed COS task retains its COS target after the paradigm changes to NAS.
- The signing server can use `UPLOAD_LEGACY_PARADIGMS_FILE` for older clients while new clients receive current NAS targets. This supports independent, staggered upgrades without requiring every acquisition computer to be idle at the same time.
- Keep all existing scoring behavior (`oi-eegqc-score-v8`), COS receipts, NAS destination isolation and automatic update scheduling from 0.7.1.

The operator approved these legacy-paradigm NAS unique names: `AVEEG-20260923`, `RSVP-20260924`, `DoRSVP-20261011`. New paradigms continue to use their own `metadata.toml` unique name. Historical COS objects are retained and are not automatically migrated.
