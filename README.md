# OI-EEGQC

[简体中文](README.zh-CN.md) · [Windows installer](https://github.com/Omni-Intel/oi-eegqc/releases/latest/download/OI-EEGQC-Setup-Windows-x64.exe)

A desktop application for local EEG quality scoring and resumable collection uploads. The supported interface is Qt Quick / PySide6. NPY, EDF and BDF share one scoring engine; photos, videos and other files in the selected collection travel with the EEG.

## Operator workflow

Select a collection folder, resolve any missing acquisition parameters, score, then upload. Unchanged scoring results are reused. Uploads retain the collection identity across days and transfer new or changed files. After adding recordings to an unfinished collection, pause, select the folder again, score and continue.

Updates use the green-hk HTTPS mirror. Raw recordings upload directly to TOS; the signer service does not receive the file bytes. See [operation](docs/desktop.md) and [upload behavior](docs/folder-upload.md).

## Development

Use Python 3.12 for the Windows build. From the repository root:

```sh
python -m venv .venv
# Activate the virtual environment for your shell.
python -m pip install -e '.[desktop,dev,packaging]'
oi-eegqc-desktop
python scripts/check.py --calibrate
```

Build on Windows with `scripts/build-desktop.ps1` and Inno Setup 6. The same script is used by CI: product tests, injected-fault calibration, packaging, frozen-app smoke checks, then the installer. Build failures stop publication. No build logic exists only inside GitHub Actions.

## Scoring and maintenance

The v0.6 development line uses report schema v3, scoring algorithm v2 and threshold version v0.6.0. Suspected clipping is evaluated per window on unfiltered signals. Brief plateaus no longer invalidate an entire channel. Reports include window evidence, channel names and the effective configuration; the UI separates the usable-window percentage from its weighted score. Previous cached scores are recomputed under the new algorithm. These changes require Windows validation and real-recording review before release.

- [Architecture and module ownership](docs/architecture.md)
- [Scoring rules and limits](docs/scoring.md)
- [Windows build](docs/windows-app.md)
- [Channel layouts](docs/channel-layouts.md)
- [Update mirror](docs/update-mirror.md)
- [Historical research and CLI reference](docs/archive/research-v0.5.md)

CLI, dataset adapters and the Python API remain available for automation and calibration. They call the same scoring engine. The retired QWidget interface is retained only as a source-level regression harness and is excluded from distributions.
