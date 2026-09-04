# OI-EEGQC

Adaptive **EEG quality validation bench** for commercial multi-clip acquisition.

Named **`oi-eegqc`** (not `oi-vali`) so the purpose is obvious in registries and CLIs: *OI EEG Quality Control*. Package import: `oi_eegqc`. CLI: `oi-eegqc`.

Designed for **many stimulus lengths (≈5–60s+)** and **many channel montages** (consumer 8ch → research 128ch+), with **three grade tracks**:

| Track | Scale | Inspired by |
|-------|--------|-------------|
| Letter | A / B / C / D | WeBrain ODQ |
| GQI | 0–100 + penalty breakdown | MEEGqc Global Quality Index |
| Availability | Available / Caution / Unavailable | HBN-EEG flags |

QA (continuous metrics) and QC (threshold decisions) are separated; thresholds are versioned (`threshold_version`).

## Why adaptive?

- **Duration profiles** change window/hop length and usable-time floors (a 6s clip cannot use the same window stats as a 60s clip).
- **Montage profiles** change neighbor-correlation / bad-channel tolerances (low-density arrays should not use high-density PREP-style corr=0.75 blindly — see HAPPILEE).

## Install

```bash
cd oi-eegqc
pip install -e ".[dev]"
```

Optional MNE extras are reserved for future EDF/BDF loaders: `pip install -e ".[mne]"`.

## Quick start

```bash
# synthetic smoke demo
oi-eegqc demo --channels 32 --duration 12

# write editable YAML
oi-eegqc init-config -o my_qc.yaml

# score one continuous clip saved as (n_channels, n_times) .npy
oi-eegqc eval-npy -i clip.npy --sfreq 250 --ch-names ch_names.npy -o report.json

# batch a folder of clips
oi-eegqc eval-dir -i ./clips --sfreq 250 -o batch_report.json
```

Python API:

```python
from oi_eegqc import evaluate_recording, RecordingInput
import numpy as np

data = np.load("clip.npy")  # (n_ch, n_times)
report = evaluate_recording(
    RecordingInput(
        data=data,
        sfreq=250.0,
        ch_names=[f"E{i}" for i in range(data.shape[0])],
        clip_id="vid_001",
        stimulus_duration_s=15.0,
        event_ok=True,
        sync_error_ms=8.0,
    )
)
print(report.letter_grade, report.gqi, report.availability)
print(report.to_dict())
```

## Pipeline (v0.1)

1. Drop aux / flat channels  
2. High-pass (>1 Hz)  
3. Select duration + montage profiles  
4. Window QA (WeBrain-style): constant / high-amp / NSR / low-corr → **ODQ**  
5. Usable-time floor (duration-adaptive) → letter demotion  
6. Penalty GQI: contact · cleanliness · usable_time · integrity · task_validity  
7. HBN-style availability flag  

## Config

See [`configs/default.yaml`](configs/default.yaml). Override profiles without code changes. Keep `threshold_version` bump when cutoffs change so historical grades stay auditable.

## Relation to `data_validation/`

Workspace `data_validation/` extracts rich epoch features and Neuroskill-like state scores. Those are **state heuristics**, not acceptance metrics. OI-EEGQC is the **acceptance / rating** layer. Downstream training can still consume features; settlement / re-record decisions should use letter + GQI + availability.

## Roadmap

- [ ] EDF/BDF / BIDS reader via MNE  
- [ ] Impedance map + capping-photo rubric hooks (HBCD-style)  
- [ ] ISC / ASSR task-validity proxies for movie watching  
- [ ] HTML report + batch dashboard  
- [ ] Calibration suite against expert labels  

## References

- Bigdely-Shamlo et al., PREP, *Front. Neuroinform.* 2015  
- Jas et al., Autoreject, *NeuroImage* 2017  
- Dong et al., Quantitative EEG QA / WeBrain, *Physiol. Meas.* 2021  
- Fox et al., HBCD EEG QC, *Dev. Cogn. Neurosci.* 2024  
- HBN-EEG availability flags; NATVIEW movie-EEG QC; MEEGqc GQI; IFCN/ILAE impedance guidance  

## License

MIT
