"""Minimal API example with synthetic EEG."""

from __future__ import annotations

import json

import numpy as np

from oi_eegqc import RecordingInput, evaluate_recording


def main() -> None:
    rng = np.random.default_rng(7)
    sfreq = 250.0
    n_ch, duration_s = 16, 18.0
    n_times = int(sfreq * duration_s)
    t = np.arange(n_times) / sfreq
    shared = 0.8e-5 * np.sin(2 * np.pi * 10.0 * t)
    data = np.stack(
        [shared + 0.04e-5 * np.sin(2 * np.pi * (0.2 * i + 1.0) * t) for i in range(n_ch)],
        axis=0,
    )
    data += 0.03e-5 * rng.standard_normal(data.shape)

    report = evaluate_recording(
        RecordingInput(
            data=data,
            sfreq=sfreq,
            ch_names=[f"EEG{i:02d}" for i in range(n_ch)],
            subject_id="sub-demo",
            session_id="ses-01",
            clip_id="clip-018s",
            stimulus_duration_s=duration_s,
            event_ok=True,
            sync_error_ms=12.0,
            impedance_kohm={f"EEG{i:02d}": 4.5 for i in range(n_ch)},
        )
    )
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
