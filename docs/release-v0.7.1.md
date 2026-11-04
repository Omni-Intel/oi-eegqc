# EEGQC 0.7.1

- Add RustFS NAS uploads through S3 presigned URLs, path-style addressing and resumable multipart uploads. Storage credentials remain on the signing server.
- Preserve all 0.7.0 scoring evidence, segment verdicts and mixed-paradigm routing. The scoring algorithm remains `oi-eegqc-score-v8`.
- Keep COS receipts separate from NAS receipts, including mixed-paradigm queues. Normal and mixed views share the canonical task ID and lock for each destination.
- Check for updates after a random 30–180 second startup delay, then every 30–60 minutes. Automatically install after 30–120 seconds of local QC inactivity; preserve paused upload queues.
- Prevent manual installation during directory import, and wait for the upload dialog to close in the UI regression test.

Deployment: installing this client does not change production storage. Publish both intranet update manifests separately, verify client adoption, then validate RustFS writes and personnel-platform ingestion before switching paradigm destinations. Historical COS objects remain in COS.

Validation: 331 tests passed, including direct S3 uploads, restart recovery, mixed-destination isolation, COS compatibility and update scheduling. Injected-fault scoring calibration passed. These checks use simulated S3 storage and do not replace production NAS and platform integration acceptance.
