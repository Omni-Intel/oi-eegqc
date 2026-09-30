# EEGQC 0.6.15

- Dedicated company update channel for portable and installed builds; previous company and GitHub update routes remain available.
- Explicit paradigm identification from external acquisition manifests, plus standard EEG-BIDS participant, Session and scan timestamps.
- Multi-run Sessions score every recording and submit one duration-weighted Session summary with per-run score provenance.
- Existing EDF/BDF, NPY, Neuracle float32 and ZIP imports remain supported.
- BIDS channel types and inherited sampling metadata are read; missing registration metadata is reported without treating raw upload as database success.
- Self-hosted update and upload API origins can be configured for other deployments.

See [external acquisition format](external-acquisition.md) for layouts, timing, identity and current format limits.
