import json

from oi_eegqc.desktop_service import inspect_edf_header, inspect_file, npy_metadata, npy_ready


def write_header(path, sfreq=256, n=2, record_s=1):
    def field(value, size):
        return str(value).encode("ascii").ljust(size)
    header = b"".join(field(v, s) for v, s in [
        ("0", 8), ("X", 80), ("X", 80), ("01.01.24", 8), ("00.00.00", 8),
        (256 + n * 256, 8), ("", 44), (1, 8), (record_s, 8), (n, 4)])
    for values, size in [(["Fp1", "Fp2"][:n], 16), ([""] * n, 80),
                         (["uV"] * n, 8), ([-200] * n, 8), ([200] * n, 8),
                         ([-32768] * n, 8), ([32767] * n, 8), ([""] * n, 80),
                         ([sfreq] * n, 8), ([""] * n, 32)]:
        header += b"".join(field(v, size) for v in values)
    if path.suffix.lower() == ".bdf":
        header = b"\xffBIOSEMI" + header[8:]
    path.write_bytes(header)


def test_inspect_edf_header_reads_declared_rate(tmp_path):
    for suffix, sfreq in ((".edf", 256), (".bdf", 500), (".edf+", 128)):
        path = tmp_path / ("recording" + suffix)
        write_header(path, sfreq)
        assert inspect_edf_header(path) == {"sfreq": float(sfreq)}
        assert inspect_file(path) == {"sfreq": float(sfreq)}


def test_npy_ready_requires_rate_and_unit_only(tmp_path):
    path = tmp_path / "sample.npy"
    path.write_bytes(b"placeholder")
    path.with_suffix(".json").write_text(json.dumps({"sfreq": 500, "unit": "uV"}))
    declared = inspect_file(path)
    assert npy_ready(declared)
    assert "channels_first" not in declared
    path.with_suffix(".json").write_text(json.dumps({"sfreq": 250}))
    assert not npy_ready(npy_metadata(path))


def test_sidecar_optional_fields_and_invalid_rates(tmp_path):
    path = tmp_path / "sample.npy"
    for metadata in ({"sfreq": 0}, {"sfreq": True}, {"sfreq": 250, "SamplingFrequency": 500}):
        path.with_suffix(".json").write_text(json.dumps(metadata))
        try:
            npy_metadata(path)
        except ValueError:
            pass
        else:
            raise AssertionError("expected invalid sidecar to fail")
    path.with_suffix(".json").write_text(json.dumps({
        "sfreq": 250, "unit": "uV", "impedance_kohm": {"Fp1": 4.2}, "sync_error_ms": 12
    }))
    parsed = npy_metadata(path)
    assert parsed["impedance_kohm"] == {"Fp1": 4.2}
    assert parsed["sync_error_ms"] == 12


def test_line_hz_shifts_noise_band(tmp_path):
    import json
    import numpy as np
    from oi_eegqc.config import default_config, load_config
    from oi_eegqc.datasets.synthetic import synth_clean
    from oi_eegqc.intake import score_file

    cfg = default_config()
    cfg.apply_line_hz(60)
    assert cfg.line_hz == 60.0
    assert cfg.noise_band_hz == (65.0, 95.0)
    cfg.apply_line_hz(50)
    assert cfg.line_hz == 50.0
    assert cfg.noise_band_hz == (55.0, 95.0)

    path = tmp_path / "sample.npy"
    np.save(path, synth_clean(4, 250, 5))
    report = score_file(path, sfreq=250, line_hz=60)
    assert report.extras["line_hz"] == 60.0
    path.with_suffix(".json").write_text(json.dumps({"sfreq": 256, "unit": "uV"}))
    sidecar = score_file(path)
    assert sidecar.extras["sfreq_hz"] == 256.0
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text("line_hz: 60\n")
    loaded = load_config(yaml_path)
    assert loaded.line_hz == 60.0
    assert loaded.noise_band_hz == (65.0, 95.0)


def test_time_weighted_usable_weights_by_duration():
    from types import SimpleNamespace
    import pytest
    from oi_eegqc.desktop_service import time_weighted_usable

    short = SimpleNamespace(duration_s=10, usable_ratio=1.0)
    long = SimpleNamespace(duration_s=90, usable_ratio=0.0)
    assert time_weighted_usable([short, long]) == pytest.approx(0.1)
    assert time_weighted_usable([]) is None
    assert time_weighted_usable([SimpleNamespace(duration_s=0, usable_ratio=1.0)]) is None