# EEGQC 0.6.16

- Folder imports automatically score new recordings and restore unchanged results from `<recording stem>.eegqc-report.json`. Sampling, mains and channel selection changes invalidate the current result.
- Completed, unchanged folders reuse the local upload manifest without hashing or scoring again. Remaining files retain their individual upload receipts.
- The signing service resolves established participant/Session recording identities when a collection is moved or its local upload state is absent. Explicit new-acquisition uploads retain a separate collection.
- Public-list reads and updates run in a background worker. Upload folder summaries scan each child collection once; transfer checkpoints write the active child instead of the full queue.
- Small-file completion receipts are grouped, and server-side cloud promotion runs with four workers. Confirmation remains separate from foreground interaction.
- Telemetry and upload summaries distinguish newly scored/uploaded files from reused results and completed files. Technical scoring failures leave the final score and usable duration empty while preserving raw upload.
- Scoring rules and the passing threshold of 60 remain unchanged. Portable and installed builds use the company update channel and retain pending queues across updates.
