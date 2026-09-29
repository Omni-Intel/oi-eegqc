import numpy as np

from oi_eegqc.pipeline import evaluate_recording, prepare_eeg
from oi_eegqc.config import default_config
from oi_eegqc.types import RecordingInput


def test_stationary_noise_has_comparable_scores_at_different_durations():
    fs = 250
    t = np.arange(60 * fs) / fs
    rng = np.random.default_rng(42)
    data = np.array([20 * np.sin(2 * np.pi * 10 * t + i * .05)
                     + 14 * np.sin(2 * np.pi * 70 * t + i * .04)
                     + rng.normal(0, .5, len(t)) for i in range(32)])
    cfg = default_config()
    parent = RecordingInput(data=data, sfreq=fs, ch_names=[f'E{i}' for i in range(32)], unit='uV')
    prepared = prepare_eeg(parent, cfg)
    scores = []
    for seconds in (3, 10, 60):
        first, last = 0, seconds * fs
        recording = RecordingInput(data=data[:, first:last], sfreq=fs, ch_names=parent.ch_names,
                                   unit='uV', stimulus_duration_s=seconds)
        report = evaluate_recording(recording, cfg, prepared=prepared.crop(first, last))
        scores.append(report.gqi)
    assert max(scores) - min(scores) < 2
    assert all(60 < score < 100 for score in scores)
