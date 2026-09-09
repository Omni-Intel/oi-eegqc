from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from oi_eegqc.adapters import pick_eeg_channels
from oi_eegqc.config import default_config
from oi_eegqc.datasets import open_dataset, synth_clean
from oi_eegqc.intake import score_file
from oi_eegqc.io.array import load_npy
from oi_eegqc.layouts import get_layout, resolve_channel_names


def test_bcigo_layout_matches_sdk_row_order():
    layout = get_layout("bcigo_sdk_1.0.2")
    assert layout.n_channels == 32
    assert layout.names[0] == "P8"
    assert layout.names[15] == "Fp1"
    assert layout.names[28] == "IO"
    assert layout.names[31] == "Fz"
    assert layout.channels[28].edf_label == "EOG IO"
    assert "brainco" in layout.device_aliases


def test_device_alias_only_when_count_matches():
    names, layout_id = resolve_channel_names(32, device_type="brainco")
    assert layout_id == "bcigo_sdk_1.0.2"
    assert names[0] == "P8" and names[28] == "IO"
    names, layout_id = resolve_channel_names(8, device_type="brainco")
    assert layout_id is None
    assert names == [f"EEG{i:02d}" for i in range(8)]


def test_explicit_names_win_over_layout():
    names, layout_id = resolve_channel_names(
        2,
        names=["A", "B"],
        layout_id="bcigo_sdk_1.0.2",
        device_type="brainco",
    )
    assert names == ["A", "B"]
    assert layout_id is None


def test_layout_count_mismatch_is_an_error():
    with pytest.raises(ValueError, match="has 32 channels"):
        resolve_channel_names(8, layout_id="bcigo_sdk_1.0.2")


def test_io_is_not_dropped_as_aux():
    layout = get_layout("bcigo_sdk_1.0.2")
    data = synth_clean(32, 250.0, 2.0, seed=2)
    cfg = default_config()
    eeg, names, dropped = pick_eeg_channels(data, layout.names, cfg.default_aux_names)
    assert "IO" in names
    assert "IO" not in dropped
    assert eeg.shape[0] == 32


def _write_avsession(stamp: Path, n_ch: int, *, device_type: str = "brainco") -> None:
    sfreq = 250.0
    data = synth_clean(n_ch, sfreq, 6.0, seed=4)
    np.save(stamp / "continuous_eeg.npy", data)
    (stamp / "metadata.json").write_text(
        json.dumps(
            {
                "subject_id": "demo",
                "sfreq": sfreq,
                "n_channels": n_ch,
                "device_type": device_type,
                "eeg_file": "continuous_eeg.npy",
                "marker_mode": "noop",
                "completed": False,
                "task_mode": "video",
            }
        ),
        encoding="utf-8",
    )
    (stamp / "events.json").write_text(
        json.dumps(
            [
                {"name": "video_on", "sample_index": 50},
                {"name": "video_off", "sample_index": 50 + int(2.5 * sfreq)},
            ]
        ),
        encoding="utf-8",
    )


def test_avsession_brainco_32_uses_bcigo_layout(tmp_path: Path):
    stamp = tmp_path / "20260101_000000"
    stamp.mkdir()
    _write_avsession(stamp, 32)
    recs = open_dataset("avsession", tmp_path).recordings()
    assert recs[0].ch_names[0] == "P8"
    assert recs[0].ch_names[28] == "IO"
    assert recs[0].meta["channel_layout"] == "bcigo_sdk_1.0.2"


def test_avsession_brainco_8_stays_positional(tmp_path: Path):
    stamp = tmp_path / "20260101_000000"
    stamp.mkdir()
    _write_avsession(stamp, 8)
    recs = open_dataset("avsession", tmp_path).recordings()
    assert recs[0].ch_names == [f"EEG{i:02d}" for i in range(8)]
    assert "channel_layout" not in recs[0].meta


def test_inspect_channel_names_uses_placeholders_without_layout(tmp_path: Path):
    from oi_eegqc.intake import inspect_channel_names

    data = synth_clean(4, 250.0, 1.0, seed=3)
    path = tmp_path / "clip.npy"
    np.save(path, data)
    path.with_suffix(".json").write_text(json.dumps({"sfreq": 250, "unit": "uV"}), encoding="utf-8")
    assert inspect_channel_names(path) == ["ch000", "ch001", "ch002", "ch003"]


def test_keep_channels_scores_named_subset(tmp_path: Path):
    data = synth_clean(8, 250.0, 2.0, seed=6)
    path = tmp_path / "clip.npy"
    np.save(path, data)
    path.with_suffix(".json").write_text(json.dumps({"sfreq": 250, "unit": "uV"}), encoding="utf-8")
    report = score_file(path, keep_channels=["ch000", "ch003"])
    assert report.n_channels_used == 2
    with pytest.raises(ValueError, match="没有已选通道"):
        score_file(path, keep_channels=["missing"])


def test_npy_sidecar_channel_layout(tmp_path: Path):
    data = synth_clean(32, 250.0, 2.0, seed=5)
    path = tmp_path / "clip.npy"
    np.save(path, data)
    path.with_suffix(".json").write_text(
        json.dumps({"sfreq": 250, "unit": "uV", "channel_layout": "bcigo_sdk_1.0.2"}),
        encoding="utf-8",
    )
    rec = load_npy(path, 250.0, unit="uV", channel_layout="bcigo_sdk_1.0.2")
    assert rec.ch_names[0] == "P8"
    assert rec.ch_names[28] == "IO"
    report = score_file(path)
    assert report.n_channels_used == 32
    assert report.extras.get("channel_layout") == "bcigo_sdk_1.0.2"
    assert "IO" not in report.extras.get("dropped_channels", [])
    subset = score_file(path, keep_channels=["P8", "IO"])
    assert subset.n_channels_used == 2


def test_session_stamp_metadata_names_zero_channel(tmp_path: Path):
    from oi_eegqc.intake import inspect_channel_names, npy_metadata, npy_ready

    stamp = tmp_path / "20260101_000000"
    stamp.mkdir()
    data = synth_clean(32, 250.0, 5.0, seed=8)
    data[0] = 0.0
    np.save(stamp / "continuous_eeg.npy", data)
    (stamp / "metadata.json").write_text(
        json.dumps(
            {
                "subject_id": "demo",
                "sfreq": 250.0,
                "n_channels": 32,
                "device_type": "brainco",
                "eeg_file": "continuous_eeg.npy",
                "task_mode": "video",
            }
        ),
        encoding="utf-8",
    )
    (stamp / "events.json").write_text("[]", encoding="utf-8")
    path = stamp / "continuous_eeg.npy"
    declared = npy_metadata(path)
    assert npy_ready(declared)
    assert declared["device_type"] == "brainco"
    assert inspect_channel_names(path)[0] == "P8"
    report = score_file(path)
    notes = " ".join(item["text"] for item in report.extras["operator"]["notes"])
    assert "P8" in notes
    assert "全程为 0" in notes
    assert "已对上强脑" in report.extras["operator"]["layout"]
    assert any(item["name"] == "接触" for item in report.extras["operator"]["dimensions"])


def test_unrelated_metadata_json_is_ignored(tmp_path: Path):
    from oi_eegqc.intake import npy_metadata

    np.save(tmp_path / "clip.npy", synth_clean(4, 250.0, 2.0, seed=1))
    (tmp_path / "metadata.json").write_text(
        json.dumps({"sfreq": 250, "eeg_file": "continuous_eeg.npy", "device_type": "brainco"}),
        encoding="utf-8",
    )
    assert npy_metadata(tmp_path / "clip.npy") == {}
