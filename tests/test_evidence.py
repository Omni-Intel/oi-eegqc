"""A QC verdict must describe what was measured, on which file, by which build."""
import json
from types import SimpleNamespace

import pytest

from oi_eegqc import evidence as ev


def test_file_identity_is_content_based(tmp_path):
    first = tmp_path / 'a.edf'
    second = tmp_path / 'b.edf'
    first.write_bytes(b'x' * 512)
    second.write_bytes(b'x' * 512)
    assert ev.file_identity(first) == ev.file_identity(second)
    second.write_bytes(b'y' * 512)
    assert ev.file_identity(first) != ev.file_identity(second)


def test_segment_evidence_uses_the_measured_window():
    report = SimpleNamespace(usable_ratio=0.5, duration_profile='short', odq=50.0,
                             letter_grade=SimpleNamespace(value='B'),
                             window_qa=SimpleNamespace(n_windows=10, usable_windows=5))
    item = ev.segment_evidence(report, 10.0, 30.0)
    assert item['duration_s'] == 20.0
    assert item['usable_duration_s'] == 10.0
    assert item['windows'] == {'total': 10, 'usable': 5}
    assert item['basis'] == 'measured_segment_window'


def test_segment_evidence_without_windows_still_reports_duration():
    report = SimpleNamespace(usable_ratio=0.0, duration_profile=None, odq=0.0,
                             letter_grade=None, window_qa=None)
    item = ev.segment_evidence(report, 0.0, 5.0)
    assert item['duration_s'] == 5.0
    assert item['usable_duration_s'] == 0.0
    assert 'windows' not in item


def test_boundary_check_reports_the_nearest_marker():
    raw = SimpleNamespace(annotations=SimpleNamespace(onset=[1.0, 10.2, 40.0]))
    close = ev.boundary_check(raw, 10.0)
    assert close['checked'] and close['within_tolerance']
    assert close['nearest_offset_s'] == pytest.approx(0.2)
    far = ev.boundary_check(raw, 20.0)
    assert far['checked'] and not far['within_tolerance']


def test_boundary_check_without_annotations_is_explicit():
    raw = SimpleNamespace(annotations=SimpleNamespace(onset=[]))
    result = ev.boundary_check(raw, 5.0)
    assert result['checked'] is False and 'no annotations' in result['reason']


def test_recording_identity_counts_measured_duration():
    info = {'sfreq': 250.0, 'ch_names': ['A', 'B']}
    raw = SimpleNamespace(info=info, n_times=2500, _raw_extras=[{}],
                          annotations=SimpleNamespace(onset=[0.0, 9.0]))
    identity = ev.recording_identity(raw)
    assert identity['measured_duration_s'] == 10.0
    assert identity['n_annotations'] == 2
    assert identity['n_channels'] == 2
    json.dumps(identity)  # must stay serialisable
