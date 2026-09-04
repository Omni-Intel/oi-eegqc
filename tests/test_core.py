from __future__ import annotations

import numpy as np

from oi_eegqc.adapters import pick_eeg_channels, sliding_windows
from oi_eegqc.config import default_config
from oi_eegqc.pipeline import evaluate_recording
from oi_eegqc.scoring.grades import letter_from_odq
from oi_eegqc.types import LetterGrade, RecordingInput


def test_select_duration_and_montage_profiles():
    cfg = default_config()
    assert cfg.select_duration(6).name == "ultra_short"
    assert cfg.select_duration(12).name == "short"
    assert cfg.select_duration(30).name == "medium"
    assert cfg.select_duration(60).name == "long"
    assert cfg.select_montage(8).name == "low_density"
    assert cfg.select_montage(32).name == "mid_density"
    assert cfg.select_montage(128).name == "high_density"


def test_letter_cutoffs_webrain_like():
    cfg = default_config()
    assert letter_from_odq(95, cfg.letter) == LetterGrade.A
    assert letter_from_odq(85, cfg.letter) == LetterGrade.B
    assert letter_from_odq(70, cfg.letter) == LetterGrade.C
    assert letter_from_odq(40, cfg.letter) == LetterGrade.D


def test_pick_eeg_channels_drops_aux_and_flat():
    data = np.zeros((4, 100))
    data[0] = np.linspace(0, 1, 100)
    data[1] = 0
    data[2] = np.linspace(0, 2, 100)
    data[3] = np.linspace(0, 1, 100)
    kept, names = pick_eeg_channels(data, ["Fz", "FLAT", "Cz", "ECG"], aux_names=("ECG",))
    assert names == ["Fz", "Cz"]
    assert kept.shape[0] == 2


def test_sliding_windows_short_clip():
    wins = sliding_windows(50, 100.0, 1.0, 0.5)
    assert len(wins) >= 1


def _synth(n_ch: int, duration_s: float, sfreq: float = 250.0, noise: float = 0.03e-5, seed: int = 0):
    rng = np.random.default_rng(seed)
    n_times = int(sfreq * duration_s)
    t = np.arange(n_times) / sfreq
    shared = 0.8e-5 * np.sin(2 * np.pi * 10.0 * t)
    data = np.stack(
        [shared + 0.04e-5 * np.sin(2 * np.pi * (0.2 * i + 1.0) * t) for i in range(n_ch)],
        axis=0,
    )
    data += noise * rng.standard_normal(data.shape)
    return data, sfreq


def test_clean_short_clip_gets_high_grade():
    data, sfreq = _synth(32, 12.0, noise=0.02e-5, seed=1)
    rec = RecordingInput(
        data=data,
        sfreq=sfreq,
        ch_names=[f"E{i}" for i in range(32)],
        clip_id="clean",
        event_ok=True,
    )
    report = evaluate_recording(rec)
    assert report.duration_profile == "short"
    assert report.montage_profile == "mid_density"
    assert report.letter_grade in {LetterGrade.A, LetterGrade.B}
    assert report.gqi >= 70


def test_noisy_clip_is_demoted():
    data, sfreq = _synth(32, 12.0, noise=3e-5, seed=2)
    data[0] = 80e-5 * np.random.default_rng(3).standard_normal(data.shape[1])
    rec = RecordingInput(
        data=data,
        sfreq=sfreq,
        ch_names=[f"E{i}" for i in range(32)],
        clip_id="noisy",
    )
    report = evaluate_recording(rec)
    assert report.odq < 95
    assert report.letter_grade in {LetterGrade.B, LetterGrade.C, LetterGrade.D}


def test_event_failure_forces_unavailable_or_d():
    data, sfreq = _synth(8, 6.0, seed=4)
    rec = RecordingInput(
        data=data,
        sfreq=sfreq,
        ch_names=[f"E{i}" for i in range(8)],
        event_ok=False,
    )
    report = evaluate_recording(rec)
    assert report.letter_grade == LetterGrade.D
    assert report.duration_profile == "ultra_short"
    assert report.montage_profile == "low_density"


def test_long_duration_profile_selected():
    data, sfreq = _synth(64, 55.0, seed=5)
    rec = RecordingInput(data=data, sfreq=sfreq, ch_names=[f"E{i}" for i in range(64)])
    report = evaluate_recording(rec)
    assert report.duration_profile == "long"
    assert report.montage_profile == "mid_density"
