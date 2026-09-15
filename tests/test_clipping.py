"""Regression cases for the operator-reported whole-record clipping failure."""
import numpy as np
import pytest

from oi_eegqc import RecordingInput, evaluate_recording
from oi_eegqc.datasets.synthetic import synth_clean
from oi_eegqc.qa.clipping import plateau_fractions


def recording(data):
    return RecordingInput(data=data, sfreq=250, ch_names=[f"E{i}" for i in range(len(data))], unit="uV")


def test_smooth_repeated_peaks_are_not_clipping_even_with_dc_offset():
    t = np.arange(15000) / 250
    signal = 30 * np.sin(2 * np.pi * 10 * t)
    # The old 99.9%-of-own-maximum rule mistakes these smooth peaks for rails.
    assert np.mean(np.abs(signal) >= .999 * np.max(np.abs(signal))) > .01
    assert not plateau_fractions(np.array([signal, signal + 5000]), 3, 1).any()
    report = evaluate_recording(recording(np.tile(signal, (8, 1))))
    assert report.window_qa.clipped_channels == []
    assert report.usable_ratio > .9


@pytest.mark.parametrize("offset", [0, 5000])
def test_one_sided_clipping_and_nonfinite_gaps(offset):
    t = np.arange(625) / 250
    signal = np.minimum(100 * np.sin(2 * np.pi * 10 * t), 25) + offset
    assert plateau_fractions(signal[None], 3, 1)[0] > .01
    separated = np.array([[100, 100, np.nan, 100, 100, 0, -5]], dtype=float)
    assert plateau_fractions(separated, 3, 1)[0] == 0
    assert plateau_fractions(np.ones((1, 625)) * 5000, 3, 1)[0] == 0


def test_brief_whole_head_clipping_only_marks_affected_windows():
    clean = synth_clean(8, 250, 60, seed=13)
    damaged = clean.copy()
    damaged[:, 20 * 250:22 * 250] = np.clip(damaged[:, 20 * 250:22 * 250] * 100, -400, 400)
    report = evaluate_recording(recording(damaged))
    qa = report.window_qa
    assert qa.clipped_channels
    assert qa.persistent_clipped_channels == []
    assert not report.hard_failed
    assert report.usable_ratio > .7
    clipped_windows = [w for w in qa.window_evidence if "clipped" in w["causes"]]
    assert clipped_windows
    assert all(w["start_s"] < 22 and w["end_s"] > 20 for w in clipped_windows)
    assert any(w["usable"] and w["start_s"] > 30 for w in qa.window_evidence)
    assert qa.usable_windows == sum(w["usable"] for w in qa.window_evidence)
    assert report.usable_ratio == pytest.approx(qa.usable_windows / qa.n_windows)


def test_sustained_platforms_retain_evidence_but_do_not_force_zero():
    data = np.clip(synth_clean(8, 250, 30, seed=3) * 100, -400, 400)
    report = evaluate_recording(recording(data))
    assert not report.hard_failed
    assert 0 < report.gqi < 40  # amplitude/noise still penalize this recording
    assert len(report.window_qa.persistent_clipped_channels) >= 4
    assert report.extras["scoring_config"]["clip_frac_threshold"] == .01
    assert report.extras["algorithm_version"] == "oi-eegqc-score-v3"
    assert report.extras["usable_window_rule"]["max_bad_channels"] == 2
    assert report.window_qa.window_evidence[0]["clipping_plateau_ratio"]
    notes = " ".join(x["text"] for x in report.extras["operator"]["notes"])
    assert "可用窗口" in notes and "未通过窗口示例" in notes and "疑似削顶" in notes


def test_spike_does_not_hide_local_upper_plateau():
    t = np.arange(625) / 250
    x = np.minimum(30 * np.sin(2 * np.pi * 10 * t), 10)
    x[100] = 100
    assert plateau_fractions(x[None], 3, 1)[0] > .01


def test_quantized_sine_platforms_are_diagnostic_only():
    t = np.arange(15000) / 250
    x = np.tile(np.round(30 * np.sin(2 * np.pi * t)), (8, 1))
    result = evaluate_recording(recording(x))
    assert result.window_qa.clipped_channels
    assert not result.hard_failed
    for w in result.window_qa.window_evidence:
        if set(w['causes']) == {'clipped'}:
            assert w['usable']
            assert not w['bad_channels']
    assert any(set(w['causes']) == {'clipped'} for w in result.window_qa.window_evidence)


def test_nonfinite_sample_is_missing_not_constant():
    x = synth_clean(8, 250, 10, seed=2)
    x[0, 750] = np.nan
    result = evaluate_recording(recording(x))
    for w in result.window_qa.window_evidence:
        if w['start_s'] <= 3 < w['end_s']:
            assert w['causes']['missing'] == [0]
            assert 0 not in w['causes'].get('constant', [])
            assert w['measurements']['0']['missing_samples'] == 1


def test_exceeding_channel_budget_does_not_spread_to_other_windows():
    from dataclasses import replace
    from oi_eegqc.config import default_config
    from oi_eegqc.qa.windows import assess_windows
    cfg = default_config()
    profile = replace(cfg.select_duration(10), window_s=2, hop_s=2)
    data = synth_clean(8, 250, 10, seed=2)
    data[:3, :500] = 0
    qa, _ = assess_windows(data, [str(i) for i in range(8)], 250, profile,
                           cfg.select_montage(8), cfg, raw_data_uv=data)
    assert not qa.window_evidence[0]["usable"]
    assert all(w["usable"] for w in qa.window_evidence[1:])
    assert qa.usable_window_ratio == .8
