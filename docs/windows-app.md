# Windows QC shell

The desktop app is a **thin native viewer** over `oi-eegqc serve --stdio`.
It does not score EEG. It does not scrape the human CLI.

Full design (Chinese, authoritative for the OI Windows build):
[windows-app.zh-CN.md](windows-app.zh-CN.md).

## Stack

Use **WinUI 3 or WPF** (one window, one `DataGrid`). Do **not** use Electron.
Spawn one long-lived sidecar:

```text
python -m oi_eegqc serve --stdio
```

stdin/stdout are NDJSON. Reject envelopes whose `schema_version` is not
`oi-eegqc-protocol-v1`.

## One window, three states

1. **Idle** — drop a file or folder; optional `unit` / `sfreq`.
2. **Running** — progress `done/total` + Cancel.
3. **Done** — table of letter, GQI, ODQ, availability; export JSON.

## Route drops

| Drop | Request |
| --- | --- |
| Folder with `session.json` | `score_dataset` `hw` |
| Folder of `.npy` | `score_dataset` `npy` (ask `sfreq` + `unit`) |
| `.bdf` / `.edf` | `score_file` `unit=V` |
| `.npy` file | `score_file` + `sfreq` |

Reference client: [`examples/sidecar_session.py`](../examples/sidecar_session.py).
