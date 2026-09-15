# Desktop product architecture

The existing repository remains the product repository. This refactor preserves Git history and the Windows v0.5.1 packaging work; it does not rewrite published commits. Linxi informed the separation of task execution, configuration and result evidence; no Linxi source was incorporated.

## Ownership

| Layer | Modules | Responsibility |
| --- | --- | --- |
| Presentation | `desktop.py`, `quick.py`, `qml/`, `upload_ui.py` | One QML entry, user actions, progress and reports |
| Application tasks | `application/qt_workers.py`, `application/scoring_process.py` | Cache reuse, batch execution, cancellation, subprocess timeout/restart |
| Acquisition input | `intake.py`, `io/`, `layouts/` | Units, sampling rate, channel identities and supported files |
| Numerical engine | `pipeline.py`, `qa/`, `scoring/`, `config.py`, `types.py` | Window evidence and one report; no Qt/network/upload dependencies |
| Persistence and transport | `score_cache.py`, `desktop_upload.py`, `upload_session.py`, `upload_http.py` | Score cache, collection records, resumable direct uploads |
| Server | `upload_signer/` | Manifests, round state, signatures and publication; separate deployment |
| Research/automation | `cli.py`, `serve.py`, `datasets/`, `examples/` | Callers of the same engine |
| Retired UI | `legacy/widgets.py` | Source-only regression harness; excluded from setuptools/PyInstaller distributions |

The QML app imports task workers directly, never the old QWidget window. Numerical modules load in the spawned scorer. A regression check verifies that importing the production application does not load NumPy, SciPy, MNE, the retired window or the upload server.

Existing subprocess crash recovery, scoring caches and upload-round semantics are retained. A general-purpose plugin framework is not needed for the currently supported inputs.

## Versioned results

Application v0.6.0, algorithm `oi-eegqc-score-v3`, thresholds `oi-eegqc-v0.6.0`, report `oi-eegqc-report-v3`. The stdio envelope remains `oi-eegqc-protocol-v1`. The algorithm change invalidates old score caches but not upload identities or upload records. Plateau evidence is diagnostic only; missing samples have a separate window cause.

Reports retain effective configuration, channel names, window rules, algorithm version and window-level causes. Unknown hardware rails remain unknown: extrema plateaus are labelled suspected clipping. See [scoring rules](scoring.md).

## Local verification is the CI contract

`python scripts/check.py` runs product regression tests and requires GUI/EDF dependencies rather than silently skipping desktop coverage. `--calibrate` also runs the seven existing injected-fault scenarios. Outputs go under local `build/`.

`scripts/build-desktop.ps1` installs dependencies, invokes the same checks, packages the QML app, checks the frozen app's startup and scoring process, and makes the Inno installer. GitHub Actions calls this script. PR/main checks use the same cross-platform check entry point.

`scripts/verify-windows.ps1 -BaselineInstaller <old-installer>` uses the current source version for upgrade checks, not fixed historical versions. It refuses to disturb an existing installed copy. Mirror connectivity is an optional `-CheckUpdate` check so offline install validation remains possible.

## Validation boundary

Synthetic cases establish regression behavior, not validity on every device or the cause of a particular subject's artifact. Before releasing v0.6, review affected real recordings and run installer checks on Windows. This refactor does not deploy the upload server or reprocess existing data.
