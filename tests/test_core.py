from __future__ import annotations

import numpy as np
import pytest

from oi_eegqc.adapters import detect_clipped_channels, pick_eeg_channels, sliding_windows
from oi_eegqc.config import default_config
from oi_eegqc.pipeline import evaluate_recording
from oi_eegqc.scoring.grades import letter_from_odq
from oi_eegqc.types import AvailabilityFlag, LetterGrade, RecordingInput

SFREQ = 250.0


def synth(
    n_ch: int,
    duration_s: float,
    sfreq: float = SFREQ,
    noise_uv: float = 3.0,
    seed: int = 0,
) -> np.ndarray:
    """Synthetic resting EEG in microvolts with realistic spatial coupling."""
    rng = np.random.default_rng(seed)
    n_times = int(sfreq * duration_s)
    t = np.arange(n_times) / sfreq
    shared = 8.0 * np.sin(2 * np.pi * 10.0 * t) + 4.0 * rng.standard_normal(n_times)
    data = np.stack([shared * (0.6 + 0.5 * rng.random()) for _ in range(n_ch)], axis=0)
    return data + noise_uv * rng.standard_normal(data.shape)


def make_rec(data: np.ndarray, sfreq: float = SFREQ, **kwargs) -> RecordingInput:
    kwargs.setdefault("unit", "uV")
    return RecordingInput(
        data=data,
        sfreq=sfreq,
        ch_names=[f"E{i:02d}" for i in range(data.shape[0])],
        **kwargs,
    )


# --- Profiles and cutoffs -----------------------------------------------------


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


def test_duration_profile_tightens_short_clip_cutoffs():
    cfg = default_config()
    ultra = cfg.select_duration(4.0)
    long_p = cfg.select_duration(60.0)
    assert ultra.odq_for_a > long_p.odq_for_a


def test_noise_band_clamped_to_nyquist():
    cfg = default_config()
    assert cfg.resolved_noise_band(1000.0) == (55.0, 95.0)
    # At 128 Hz the band is truncated but still usable.
    lo, hi = cfg.resolved_noise_band(128.0)
    assert (lo, hi) == (55.0, 63.0)
    # At 100 Hz there is no room left above the guard gap.
    assert cfg.resolved_noise_band(100.0) is None


# --- Channel handling ---------------------------------------------------------


def test_pick_eeg_channels_drops_aux_but_keeps_flat():
    data = np.zeros((4, 100))
    data[0] = np.linspace(0, 1, 100)
    data[2] = np.linspace(0, 2, 100)
    data[3] = np.linspace(0, 1, 100)
    kept, names, dropped = pick_eeg_channels(
        data, ["Fz", "FLAT", "Cz", "ECG"], aux_names=("ECG",)
    )
    # The flat channel must survive selection: dropping it would remove the
    # worst acquisition failure from the denominator.
    assert names == ["Fz", "FLAT", "Cz"]
    assert dropped == ["ECG"]
    assert kept.shape[0] == 3


def test_dead_channels_are_penalised_not_hidden():
    """Regression: flat channels used to be silently dropped, yielding grade A."""
    data = synth(32, 20.0, seed=1)
    data[:8] = 0.0
    report = evaluate_recording(make_rec(data, expected_n_channels=32))
    assert report.n_channels_used == 32, "dead channels must stay in the denominator"
    assert len(report.window_qa.dead_channels) == 8
    assert report.letter_grade in {LetterGrade.C, LetterGrade.D}
    assert report.availability is not AvailabilityFlag.AVAILABLE
    assert report.gqi < 85


def test_attenuated_channels_are_flagged_by_absolute_floor():
    """Regression: channels scaled to near-zero used to score a perfect ODQ."""
    data = synth(32, 20.0, seed=2)
    data[:8] *= 1e-9
    report = evaluate_recording(make_rec(data))
    assert len(report.window_qa.dead_channels) == 8
    assert report.clean_ratio < 1.0


def test_detect_clipped_channels():
    rng = np.random.default_rng(0)
    data = rng.standard_normal((3, 2000)) * 20.0
    data[1] = np.clip(data[1] * 50.0, -100.0, 100.0)
    assert detect_clipped_channels(data, 0.01) == [1]


def test_missing_channels_hard_fail():
    data = synth(10, 20.0, seed=3)
    report = evaluate_recording(make_rec(data, expected_n_channels=64))
    assert report.hard_failed
    assert report.letter_grade == LetterGrade.D
    assert report.gqi == 0.0


# --- Units --------------------------------------------------------------------


def test_unit_conversion_makes_volts_and_microvolts_agree():
    data_uv = synth(32, 20.0, seed=4)
    a = evaluate_recording(make_rec(data_uv, unit="uV"))
    b = evaluate_recording(make_rec(data_uv * 1e-6, unit="V"))
    assert a.letter_grade == b.letter_grade
    assert a.gqi == pytest.approx(b.gqi, abs=1e-6)


def test_absolute_scale_is_no_longer_invisible():
    """Regression: a railed amplifier used to grade A because all metrics were ratios."""
    data = synth(32, 20.0, seed=5)
    clean = evaluate_recording(make_rec(data, unit="uV"))
    saturated = evaluate_recording(make_rec(np.clip(data * 40.0, -400.0, 400.0), unit="uV"))
    assert clean.letter_grade == LetterGrade.A
    assert saturated.letter_grade == LetterGrade.D
    assert saturated.availability is AvailabilityFlag.UNAVAILABLE


def test_unknown_unit_rejected():
    with pytest.raises(ValueError):
        evaluate_recording(make_rec(synth(8, 6.0), unit="furlongs"))


def test_adc_unit_requires_conversion_factor():
    with pytest.raises(ValueError):
        evaluate_recording(make_rec(synth(8, 6.0), unit="adc"))
    report = evaluate_recording(make_rec(synth(8, 6.0), unit="adc", adc_to_uv=1.0))
    assert report.gqi > 0


# --- Metric independence ------------------------------------------------------


def test_clean_ratio_and_usable_ratio_are_distinct_quantities():
    """Regression: usable_ratio used to equal ODQ/100 exactly, double-counting it.

    A fixed subset of dead electrodes lowers contamination density while every
    window still stays inside the bad-channel budget, so the two must diverge.
    """
    data = synth(32, 20.0, seed=6)
    data[:5] = 0.0  # 15.6% of the montage, under the 25% per-window budget
    report = evaluate_recording(make_rec(data))
    assert report.usable_ratio == pytest.approx(1.0)
    assert report.clean_ratio < 0.9
    assert report.clean_ratio != pytest.approx(report.odq / 100.0)


def test_gqi_reaches_zero_for_unusable_data():
    """Regression: pure noise used to bottom out at GQI 26 because untested
    dimensions handed out their weight for free."""
    rng = np.random.default_rng(7)
    data = rng.standard_normal((32, 5000)) * 20.0
    report = evaluate_recording(make_rec(data))
    assert report.letter_grade == LetterGrade.D
    assert report.gqi < 5.0


def test_unassessed_dimensions_get_no_free_credit():
    data = synth(32, 20.0, seed=8)
    bare = evaluate_recording(make_rec(data))
    assert bare.extras["assessed_dimensions"] == ["cleanliness", "contact", "usable_time"]
    assert bare.extras["effective_weights"]["stimulus_sync"] == 0.0
    assert sum(bare.extras["effective_weights"].values()) == pytest.approx(1.0)

    full = evaluate_recording(
        make_rec(data, sync_error_ms=5.0, stimulus_duration_s=20.0, expected_n_channels=32)
    )
    assert "stimulus_sync" in full.extras["assessed_dimensions"]
    assert full.extras["effective_weights"]["stimulus_sync"] > 0.0


def test_sync_failure_costs_points_only_when_measured():
    data = synth(32, 20.0, seed=9)
    unmeasured = evaluate_recording(make_rec(data))
    bad_sync = evaluate_recording(make_rec(data, sync_error_ms=500.0))
    assert bad_sync.gqi < unmeasured.gqi
    assert bad_sync.penalties.stimulus_sync > 0


# --- End-to-end grading -------------------------------------------------------


def test_sliding_windows_short_clip():
    assert len(sliding_windows(50, 100.0, 1.0, 0.5)) >= 1


def test_clean_short_clip_gets_high_grade():
    report = evaluate_recording(make_rec(synth(32, 12.0, seed=10)))
    assert report.duration_profile == "short"
    assert report.montage_profile == "mid_density"
    assert report.letter_grade == LetterGrade.A
    assert report.gqi >= 95


def test_noisy_clip_is_demoted():
    data = synth(32, 12.0, seed=11) + 25.0 * np.random.default_rng(11).standard_normal(
        (32, int(SFREQ * 12.0))
    )
    report = evaluate_recording(make_rec(data))
    assert report.odq < 95
    assert report.letter_grade in {LetterGrade.C, LetterGrade.D}


def test_drift_is_absorbed_by_highpass():
    n_times = int(SFREQ * 20.0)
    t = np.arange(n_times) / SFREQ
    data = synth(32, 20.0, seed=12) + 150.0 * np.sin(2 * np.pi * 0.05 * t)[None, :]
    report = evaluate_recording(make_rec(data))
    assert report.letter_grade == LetterGrade.A


def test_event_failure_hard_fails():
    report = evaluate_recording(make_rec(synth(8, 6.0, seed=13), event_ok=False))
    assert report.hard_failed
    assert report.letter_grade == LetterGrade.D
    assert report.gqi == 0.0
    assert report.availability is AvailabilityFlag.UNAVAILABLE
    assert report.duration_profile == "ultra_short"
    assert report.montage_profile == "low_density"


def test_letter_d_never_reports_available():
    """Regression: every D-grade clip used to come back as Caution."""
    rng = np.random.default_rng(14)
    data = rng.standard_normal((32, 5000)) * 30.0
    report = evaluate_recording(make_rec(data))
    assert report.letter_grade == LetterGrade.D
    assert report.availability is AvailabilityFlag.UNAVAILABLE


def test_long_duration_profile_selected():
    report = evaluate_recording(make_rec(synth(64, 55.0, seed=15)))
    assert report.duration_profile == "long"
    assert report.montage_profile == "mid_density"
