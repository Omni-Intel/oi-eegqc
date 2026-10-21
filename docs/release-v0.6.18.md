# EEGQC 0.6.18

- Manually dragging or selecting a file or folder automatically restores any previously cleared/removed record in the shared list and attaches its Session folder for upload.
- Adds a one-click `恢复已清空` button in the toolbar when cleared records exist on the machine, and removes false startup removal notices.
- Reuses full-round scores from AV Capture's `file-quality.json` and default `ScoreCache` entries, and loads shared records in a single batch without blocking the upload button with background re-scoring of historical Sessions.
- Displays `已上传` status directly on file rows from local upload receipts.
