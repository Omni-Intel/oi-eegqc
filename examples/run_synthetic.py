"""Minimal API example with synthetic EEG in microvolts."""

from __future__ import annotations

import json

from oi_eegqc import RecordingInput, evaluate_recording
from oi_eegqc.datasets import synth_clean


def main() -> None:
    sfreq, n_ch, duration_s = 250.0, 16, 18.0
    data = synth_clean(n_ch, sfreq, duration_s, seed=7)
    report = evaluate_recording(
        RecordingInput(
            data=data,
            sfreq=sfreq,
            ch_names=[f"EEG{i:02d}" for i in range(n_ch)],
            unit="uV",
            subject_id="sub-demo",
            session_id="ses-01",
            clip_id="clip-018s",
            expected_n_channels=n_ch,
            event_ok=True,
        )
    )
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
