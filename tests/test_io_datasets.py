from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from oi_eegqc.cli import build_parser
from oi_eegqc.datasets import list_datasets, open_dataset, score_adapter, synth_clean
from oi_eegqc.io.array import load_npy, orient_channels_first
from oi_eegqc.io.edf import infer_unit
from oi_eegqc.io.segment import centered_clips, concat_epochs, epoch_duration_plan
from oi_eegqc.pipeline import evaluate_recording
from oi_eegqc.types import LetterGrade, RecordingInput


def test_orient_channels_first_uses_name_count():
    arr = np.arange(50).reshape(10, 5)
    out = orient_channels_first(arr, n_channels=5)
    assert out.shape == (5, 10)


def test_load_npy_roundtrip(tmp_path: Path):
    data = synth_clean(8, 250.0, 2.0, seed=0)
    path = tmp_path / "clip.npy"
    np.save(path, data)
    rec = load_npy(path, 250.0, unit="uV", subject_id="s1")
    assert rec.data.shape == (8, 500)
    assert rec.subject_id == "s1"
    assert rec.clip_id == "clip"
    assert rec.meta["source_path"].endswith("clip.npy")


def test_centered_clips_and_epoch_plan():
    data = np.zeros((4, 250 * 40))
    clips = centered_clips(data, 250.0)
    labels = [name for name, _ in clips]
    assert "6s" in labels and "30s" in labels
    plan = epoch_duration_plan(250, 250.0)
    assert plan[0] == ("1ep_ultra_short", 1)
    concat = concat_epochs(np.ones((10, 2, 5)), 2, 3)
    assert concat.shape == (2, 15)


def test_infer_unit_adc_vs_phys():
    unit, adc = infer_unit(["ADC counts", "ADC counts"])
    assert unit == "adc"
    unit, adc = infer_unit(["uV", "uV"])
    assert unit == "V" and adc is None


def test_dataset_registry_contains_expected_adapters():
    names = {s.name for s in list_datasets()}
    assert names == {"npy", "hw", "nod", "things", "synthetic"}
    things = next(s for s in list_datasets() if s.name == "things")
    assert things.unit_is_nominal is True
    assert next(s for s in list_datasets() if s.name == "hw").requires_mne is True


def test_unknown_dataset_raises():
    from oi_eegqc.protocol import ProtocolError

    with pytest.raises(ProtocolError) as exc:
        open_dataset("not-a-dataset")
    assert exc.value.code == "unknown_dataset"


def test_npy_dir_adapter(tmp_path: Path):
    data = synth_clean(8, 250.0, 2.0, seed=1)
    np.save(tmp_path / "a.npy", data)
    np.save(tmp_path / "b.npy", data)
    adapter = open_dataset("npy", tmp_path, 250.0, unit="uV")
    recs = adapter.recordings()
    assert len(recs) == 2
    assert {r.clip_id for r in recs} == {"a", "b"}
    assert recs[0].meta["dataset"] == "npy"


def test_synthetic_adapter_scores():
    adapter = open_dataset("synthetic", n_channels=16, duration_s=8.0)
    rows, summary = score_adapter(adapter)
    assert summary["n_total"] == 4
    assert summary["cancelled"] is False
    by_id = {r["clip_id"]: r for r in rows}
    assert by_id["synthetic_clean"]["letter_grade"] == "A"
    assert by_id["synthetic_saturated"]["letter_grade"] == "D"
    assert "schema_version" in rows[0]
    assert rows[0]["extras"]["dataset"] == "synthetic"
    assert "device" not in rows[0]
    assert "plan" not in rows[0]


def test_cli_lists_datasets():
    parser = build_parser()
    args = parser.parse_args(["datasets"])
    assert args.func.__name__ == "cmd_datasets"


def test_evaluate_recording_still_the_only_scoring_entry():
    rec = RecordingInput(
        data=synth_clean(8, 250.0, 6.0, seed=3),
        sfreq=250.0,
        ch_names=[f"E{i}" for i in range(8)],
        unit="uV",
        expected_n_channels=8,
    )
    report = evaluate_recording(rec)
    assert report.letter_grade == LetterGrade.A
