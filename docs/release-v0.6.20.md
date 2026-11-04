# OI-EEGQC 0.6.20

- Automatically recognizes Dual-Image RSVP (`DoRSVP-20261011`) sessions from `collection.json` / `locked_config.json` (`paradigm: "dual_image"`, `strategy: "paired"`) and `task-thingspair`.
- Incremental archive intake reuses unchanged local extractions and completed local upload receipts. Remote session metadata alone does not suppress scanning changed local ZIP contents or mark them uploaded. Mixed paradigms require separate selections; empty aborted archives and placeholder rehearsal archives are excluded from multi-archive imports.
- Uploads independent signed objects with up to four workers; cancellation interrupts every active connection.
- Isolates per-session database commit errors in `sync_database` so a single unmatched participant never blocks the rest of a batch.
