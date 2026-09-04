<h1 align="center">oi-eegqc</h1>

<p align="center">
  <a href="README.zh-CN.md">简体中文</a> · <strong>English</strong>
</p>

<p align="center">
  <strong>Adaptive EEG quality control for clean commercial acquisition.</strong><br>
  Grade multi-duration, multi-montage clips for intake — not for task performance.
</p>

<p align="center">
  <img alt="python" src="https://img.shields.io/badge/python-3.9%2B-111111?style=flat-square">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-111111?style=flat-square">
  <img alt="qa/qc" src="https://img.shields.io/badge/QA%2FQC-separated-FF5A01?style=flat-square">
  <img alt="grades" src="https://img.shields.io/badge/grades-A–D%20·%20GQI%20·%20Availability-555555?style=flat-square">
</p>

<p align="center">
  <a href="#quick-start"><strong>Install</strong></a> ·
  <a href="#what-it-does">What it does</a> ·
  <a href="#design-principles">Principles</a> ·
  <a href="#grade-tracks">Grade tracks</a> ·
  <a href="#configuration">Config</a>
</p>

<p align="center">
  <img src="assets/oi-eegqc-hero.png" alt="Omni-Intelligence mark with oi-eegqc wordmark" width="100%">
</p>

`oi-eegqc` is the intake bench for audiovisual-watching EEG that arrives **protocol-clean**: known montage, known sampling rate, known clip timing, and event streams you trust enough to score.

It answers one question only:

> **Is this recording acceptable as data?**

It does **not** answer whether the subject passed ASSR, N-back, caption decoding, or any downstream model eval. Harder tasks produce noisier scientific outcomes; that variance must never become a quality penalty.

## Quick Start

```bash
pip install -e ".[dev]"
oi-eegqc demo --channels 32 --duration 12
```

Score one continuous clip:

```bash
oi-eegqc eval-npy \
  -i clip.npy \
  --sfreq 250 \
  --ch-names ch_names.npy \
  -o report.json
```

Batch a folder:

```bash
oi-eegqc eval-dir -i ./clips --sfreq 250 -o batch_report.json
```

Python API:

```python
from oi_eegqc import RecordingInput, evaluate_recording
import numpy as np

data = np.load("clip.npy")  # (n_channels, n_times)
report = evaluate_recording(
    RecordingInput(
        data=data,
        sfreq=250.0,
        ch_names=[f"E{i}" for i in range(data.shape[0])],
        clip_id="vid_001",
        stimulus_duration_s=18.0,
        event_ok=True,
        sync_error_ms=8.0,
    )
)
print(report.letter_grade, report.gqi, report.availability)
```

## What It Does

| Workflow | Result |
| --- | --- |
| Duration-adaptive QA | Picks window/hop and usable-time floors for ~5–60s+ clips |
| Montage-adaptive QA | Adjusts correlation / bad-channel tolerances for 8ch → 128ch+ |
| Window signal QA | Constant / high-amp / NSR / low-corr → **ODQ%** |
| Letter grading | WeBrain-style **A / B / C / D** for settlement decisions |
| Decomposable GQI | **0–100** with contact · cleanliness · usable time · integrity · stimulus sync penalties |
| Availability flag | HBN-style **Available / Caution / Unavailable** for catalog filters |
| Versioned thresholds | Every score carries `threshold_version` for auditability |

## Design Principles

- **QC ≠ task eval.** Intake grades measure signal and acquisition integrity. Downstream scientific or model metrics live elsewhere.
- **Assume pure intake.** Events, montage, units, and clip boundaries are part of the protocol — not recovered archaeology.
- **Adapt, don’t hard-code one window.** A 6s clip and a 60s clip need different statistics.
- **Adapt, don’t hard-code one montage.** Low-density arrays must not inherit high-density correlation thresholds.
- **QA then QC.** Continuous metrics first; letter / GQI / availability are criterion layers on top.
- **One canonical report body.** `report.to_dict()` is the machine-readable contract; HTML dashboards are derived views.
- **Never score cognition.** Band ratios, “focus”, “engagement”, or difficulty-dependent ERPs are out of scope for acceptance.

## Grade Tracks

| Track | Scale | Use |
| --- | --- | --- |
| Letter | A / B / C / D | Settlement, re-record, release gates |
| GQI | 0–100 + penalty breakdown | Ranking, dashboards, continuous monitoring |
| Availability | Available / Caution / Unavailable | Dataset filters and catalog flags |

Suggested commercial reading:

| Letter | Meaning | Typical action |
| --- | --- | --- |
| **A** | Clean enough to ship | Primary training / delivery |
| **B** | Good with mild defects | Keep; light cleaning OK |
| **C** | Marginal | Down-weight or human review |
| **D** | Bad | Reject / re-acquire |

## Pipeline (v0.1)

1. Drop aux / flat channels  
2. High-pass (>1 Hz)  
3. Select **duration profile** + **montage profile**  
4. Window QA → ODQ  
5. Duration-adaptive usable-time floor → letter demotion  
6. Penalty GQI (contact · cleanliness · usable time · integrity · stimulus sync)  
7. Availability flag  

## Configuration

Write a starting YAML:

```bash
oi-eegqc init-config -o my_qc.yaml
```

Or edit [`configs/default.yaml`](configs/default.yaml). Bump `threshold_version` whenever cutoffs change so historical grades stay comparable.

## How It Is Organized

```text
.
├── assets/                 # hero + wordmark
├── configs/default.yaml    # duration + montage profiles
├── examples/               # API smoke example
├── src/oi_eegqc/
│   ├── adapters.py         # channel pick, windows, high-pass
│   ├── config.py           # adaptive profiles
│   ├── qa/windows.py       # signal QA
│   ├── scoring/grades.py   # letter / GQI / availability
│   ├── pipeline.py         # evaluate_recording
│   └── cli.py              # oi-eegqc entrypoint
└── tests/
```

## Requirements

- Python 3.9+
- `numpy`, `scipy`, `pyyaml`
- Optional: `mne` for future EDF/BDF loaders (`pip install -e ".[mne]"`)

## References

- [PREP pipeline](https://doi.org/10.3389/fninf.2015.00016) — early-stage noisy-channel / referencing discipline  
- [Autoreject](https://doi.org/10.1016/j.neuroimage.2017.06.030) — adaptive trial rejection mindset  
- [WeBrain quantitative EEG QA](https://doi.org/10.1088/1361-6579/ac890d) — ODQ → A/B/C/D  
- [HBCD EEG QC](https://doi.org/10.1016/j.dcn.2024.101447) — intake gates and dashboards  
- HBN-EEG availability flags · MEEGqc GQI · IFCN/ILAE impedance guidance  

## Status

Research/engineering bench for OI audiovisual EEG intake. Thresholds must be calibrated on your device and pilot batches before production settlement.

## License

[MIT](LICENSE)
