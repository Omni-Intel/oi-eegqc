# OI-EEGQC

[简体中文](README.zh-CN.md) · [Windows installer](https://github.com/Omni-Intel/oi-eegqc/releases/latest/download/OI-EEGQC-Setup-Windows-x64.exe)

OI-EEGQC is a local-first quality-control and collection-ingest tool for EEG acquisition. The Windows application and command-line interface use the same scoring engine, input normalization and versioned report schema. A selected collection can contain EEG recordings, acquisition metadata, impedance screenshots, positioning photographs, videos and other supporting files.

The score describes recording quality for acquisition review and data intake. It does not infer cognition, attention, diagnosis or the physiological cause of an artifact.

## Data flow

```text
Collection folder
├── EDF / BDF ─────────────── file header: sampling rate, units, channels
├── NPY + optional sidecar ── declared sampling rate, units and channel layout
└── supporting files ──────── images, video, metadata and session documents
            │
            ▼
Input validation and normalization to physical units
            │
            ▼
Windowed signal checks ──► versioned GQI report + channel/window evidence
            │                         │
            │                         ├── Windows GUI
            │                         ├── CLI / Python API / stdio protocol
            │                         └── local score cache
            ▼
Collection snapshot and upload manifest
            │
            ▼
Incremental and resumable collection upload
```

Scoring and upload are separate operations. EEG samples are processed locally; upload transfers the selected collection and its supporting files to the configured project storage.

## Supported recordings

| Input | Acquisition metadata | Behavior |
| --- | --- | --- |
| EDF / BDF | Read from the file header | Uses the recorded sampling rate, physical units and channel names; corrupt headers fail validation |
| NPY | Sidecar metadata or explicit input | Requires a sampling rate and physical unit; array orientation and channel layout must be known or resolved |
| Collection folder | Recursive discovery | Scores supported EEG files and retains ordinary supporting files for upload |

NPY sidecars may declare `sfreq`, `unit`, `channels_first`, `channel_names` or a known `channel_layout`. Acquisition-specific metadata such as impedance or synchronization error is used only when present; missing evidence is not invented. See [channel layouts](docs/channel-layouts.md) and the [desktop input guide](docs/desktop.md).

## Quality model

The current report is schema v3, scoring algorithm `oi-eegqc-score-v3`, and threshold set `oi-eegqc-v0.6.0`. Every report records the effective configuration and algorithm version.

GQI is a 0–100 composite of contact quality, signal cleanliness and usable duration. Completeness and stimulus synchronization contribute only when the corresponding evidence is supplied. Reports retain per-window decisions, affected channels and detected signal shapes so that an operator can inspect why a score changed.

Usable-window ratio and duration score are distinct quantities. The ratio is the fraction of overlapping windows that pass the channel-level rules; the duration score maps that ratio against the configured target. It is not an estimate of exact usable seconds.

Local extrema plateaus in unfiltered windows are reported for review. Quantization can produce similar shapes, so plateaus alone do not lower the score or establish hardware saturation. Flat channels, amplitude excursions, line interference and missing values are evaluated by their own rules. See [scoring rules and limitations](docs/scoring.md).

## Desktop workflow

1. Select or drop a collection folder, EDF, BDF or NPY recording.
2. Resolve only the acquisition parameters that cannot be read from the data or sidecar.
3. Score locally and inspect GQI, usable windows and channel-level evidence.
4. Upload the collection when it is ready for intake.

The desktop application hashes a file when the digest can avoid a later full read. An unchanged file with the same scoring inputs and algorithm version reuses its cached result. Changed inputs, channel selection, configuration or algorithm version trigger a new score.

Scoring runs in a separate process. A failed or cancelled file does not discard completed results, and the queue can continue after a worker crash or timeout. The supported production interface is Qt Quick / PySide6; the retired QWidget implementation is excluded from distributions.

## Incremental and resumable upload

The application recognizes the same local collection across days and maintains resumable progress for each upload:

- unchanged paths are skipped after manifest comparison;
- new files are appended;
- changed files replace the same relative path only after a successful transfer;
- local deletion does not remove historical cloud objects;
- large files use resumable multipart transfer;
- a collection is marked complete only after every selected file succeeds.

Interrupted uploads can continue without retransmitting confirmed, unchanged files. Uploading the folder again adds new content and updates changed paths while retaining unrelated remote files.

## Interfaces

### Windows application

Use the [published installer](https://github.com/Omni-Intel/oi-eegqc/releases/latest/download/OI-EEGQC-Setup-Windows-x64.exe) or the [portable zip](https://github.com/Omni-Intel/oi-eegqc/releases/latest/download/OI-EEGQC-Windows-x64.zip). Silent install from GitHub Releases:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-desktop.ps1
```

### Command line

```sh
python -m pip install -e '.[mne]'

oi-eegqc score -i recording.bdf -o report.json
oi-eegqc score -i session_01 --sfreq 500 --unit uV -o batch-report.json
oi-eegqc --json datasets
oi-eegqc serve --stdio
```

The CLI, dataset adapters, Python API and stdio protocol call the same numerical engine as the desktop application. Machine consumers can use JSON or NDJSON output; interactive output is intended for operator inspection.

## Architecture

| Layer | Current modules | Responsibility |
| --- | --- | --- |
| Presentation | `desktop.py`, `quick.py`, `qml/`, `upload_ui.py` | Native GUI, operator actions, progress and report presentation |
| Application tasks | `application/`, `desktop_service.py` | Batch execution, cancellation, cache reuse and scorer-process recovery |
| Acquisition input | `intake.py`, `io/`, `layouts/` | File discovery, units, sampling rate, orientation and channel identity |
| Numerical engine | `pipeline.py`, `qa/`, `scoring/`, `config.py` | Window evidence, metric aggregation and versioned reports |
| Persistence and transport | `score_cache.py`, `desktop_upload.py`, `upload_session.py`, `upload_http.py` | Local cache, collection records and resumable direct upload |
| Automation | `cli.py`, `serve.py`, `datasets/`, `examples/` | CLI, machine protocol, dataset evaluation and calibration |

This is one product repository with two thin user interfaces and one shared core. The CLI and GUI are built as separate artifacts, but do not maintain independent scoring implementations.

## Development and verification

Use Python 3.12 for Windows builds:

```sh
python -m venv .venv
# Activate the environment for the current shell.
python -m pip install -e '.[desktop,dev,packaging]'
oi-eegqc-desktop
python scripts/check.py --calibrate
```

On Windows, `scripts/build-desktop.ps1` runs the product tests, injected-fault calibration, PyInstaller packaging, frozen-application smoke checks and Inno Setup generation. CI calls the same repository scripts; build behavior is not hidden in the workflow configuration.

Further documentation:

- [Architecture and module ownership](docs/architecture.md)
- [Scoring rules and limitations](docs/scoring.md)
- [Desktop operation](docs/desktop.md)
- [Windows build and release](docs/windows-app.md)
- [Historical research and benchmark notes](docs/archive/research-v0.5.md)
