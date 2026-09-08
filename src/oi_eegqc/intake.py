"""Single file scoring entry for CLI, sidecar, and the desktop app.

Never infer a sampling rate from array length. NPY sidecar fills missing
``sfreq`` / ``unit`` / layout; explicit arguments win.
"""
from pathlib import Path
import json
import math

EDF_SUFFIXES = {".edf", ".edf+", ".bdf"}
NPY_REQUIRED = ("sfreq", "unit")
_OPTIONAL_INPUT = (
    "impedance_kohm",
    "sync_error_ms",
    "event_ok",
    "stimulus_duration_s",
    "expected_n_channels",
    "adc_to_uv",
    "subject_id",
    "session_id",
    "clip_id",
)


def npy_metadata(path):
    """Optional same-stem JSON; never infer a sampling rate from array length."""
    sidecar = Path(path).with_suffix(".json")
    if not sidecar.is_file():
        return {}
    with sidecar.open("r", encoding="utf-8-sig") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError("采样参数文件过大。")
    raw = json.loads(content)
    if not isinstance(raw, dict):
        raise ValueError("采样参数文件必须是对象。")
    result = {}
    rates = [raw[k] for k in ("sfreq", "sampling_rate", "SamplingFrequency") if k in raw]
    if rates:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in rates):
            raise ValueError("采样参数文件中的采样率无效。")
        if len(set(rates)) != 1:
            raise ValueError("采样参数文件中的采样率冲突。")
        result["sfreq"] = float(rates[0])
    if "unit" in raw:
        units = {"uv": "uV", "µv": "uV", "mv": "mV", "v": "V"}
        value = units.get(str(raw["unit"]).lower())
        if value is None:
            raise ValueError("采样参数文件中的单位无效。")
        result["unit"] = value
    if "channels_first" in raw:
        if not isinstance(raw["channels_first"], bool):
            raise ValueError("采样参数文件中的排列无效。")
        result["channels_first"] = raw["channels_first"]
    if "impedance_kohm" in raw:
        values = raw["impedance_kohm"]
        if not isinstance(values, dict) or not values:
            raise ValueError("采样参数文件中的阻抗无效。")
        parsed = {}
        for name, value in values.items():
            if not isinstance(name, str) or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("采样参数文件中的阻抗无效。")
            parsed[name] = float(value)
        result["impedance_kohm"] = parsed
    if "sync_error_ms" in raw:
        value = raw["sync_error_ms"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("采样参数文件中的同步误差无效。")
        result["sync_error_ms"] = float(value)
    return result


def inspect_edf_header(path):
    """Read sampling rate from the EDF/BDF header without NumPy or MNE."""
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(256)
        if len(header) < 256:
            return {}
        try:
            n_signals = int(header[252:256].decode("ascii").strip())
            record_s = float(header[244:252].decode("ascii").strip())
        except ValueError:
            return {}
        if n_signals <= 0 or not math.isfinite(record_s) or record_s <= 0:
            return {}
        ns_header = stream.read(n_signals * 256)
    if len(ns_header) != n_signals * 256:
        return {}
    offset = n_signals * (16 + 80 + 8 + 8 + 8 + 8 + 8 + 80)
    samples = []
    for index in range(n_signals):
        field = ns_header[offset + index * 8:offset + (index + 1) * 8]
        try:
            count = int(field.decode("ascii").strip())
        except ValueError:
            return {}
        if count <= 0:
            return {}
        samples.append(count)
    sfreq = samples[0] / record_s
    if not math.isfinite(sfreq) or sfreq <= 0:
        return {}
    return {"sfreq": float(sfreq)}


def inspect_file(path):
    """Read declared sampling metadata. Never invent a sampling rate."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".npy":
        return npy_metadata(path)
    if suffix in EDF_SUFFIXES:
        return inspect_edf_header(path)
    return {}


def npy_ready(metadata):
    return all(key in metadata for key in NPY_REQUIRED)


def merge_npy_kwargs(path, sfreq=None, unit=None, channels_first=None, extra=None):
    """Sidecar fills gaps; explicit values win."""
    declared = npy_metadata(path)
    extra = dict(extra or {})
    if sfreq is None:
        sfreq = declared.get("sfreq")
    if unit is None:
        unit = declared.get("unit")
    if channels_first is None:
        channels_first = declared.get("channels_first")
    for key in _OPTIONAL_INPUT:
        if key not in extra and key in declared:
            extra[key] = declared[key]
    return sfreq, unit, channels_first, extra


def score_file(
    path,
    sfreq=None,
    unit=None,
    channels_first=None,
    progress=None,
    line_hz=None,
    config=None,
    ch_names_path=None,
    **extra,
):
    progress = progress or (lambda phase: None)
    progress("准备")
    import numpy as np
    from .config import BenchConfig, default_config, load_config
    from .io.array import load_npy
    from .io.edf import load_edf_bdf
    from .pipeline import evaluate_recording

    progress("读取")
    path = Path(path)
    if not path.is_file():
        raise ValueError("文件不存在，请重新选择。")
    if isinstance(config, BenchConfig):
        cfg = config
    else:
        cfg = load_config(config)
    if line_hz is not None:
        cfg.apply_line_hz(line_hz)
    extras = {key: extra[key] for key in _OPTIONAL_INPUT if key in extra}
    suffix = path.suffix.lower()
    if suffix == ".npy":
        sfreq, unit, channels_first, extras = merge_npy_kwargs(
            path, sfreq=sfreq, unit=unit, channels_first=channels_first, extra=extras
        )
        if sfreq is None or not np.isfinite(sfreq) or sfreq <= 0:
            raise ValueError("请输入有效采样率。")
        recording = load_npy(
            path,
            sfreq,
            unit=unit or "uV",
            channels_first=channels_first,
            ch_names_path=ch_names_path,
            **extras,
        )
    elif suffix in EDF_SUFFIXES:
        edf_kwargs = dict(extras)
        if unit:
            edf_kwargs["unit"] = unit
        recording = load_edf_bdf(path, **edf_kwargs)
    else:
        raise ValueError("请选择脑电或数组文件。")
    progress("检查")
    if recording.data.ndim != 2 or min(recording.data.shape) == 0:
        raise ValueError("文件没有有效的脑电数据。")
    if not np.isfinite(recording.sfreq) or recording.sfreq <= 0:
        raise ValueError("文件采样率无效。")
    if recording.data.shape[1] < max(16, int(recording.sfreq)):
        raise ValueError("记录过短，至少需要 1 秒、16 个采样点。")
    progress("评分")
    report = evaluate_recording(recording, cfg)
    report.extras["adaptive"] = True
    report.extras["sfreq_hz"] = float(recording.sfreq)
    report.extras["line_hz"] = float(cfg.line_hz)
    report.extras["frequency_coverage"] = {
        "signal_band_complete": recording.sfreq / 2 - 1 >= cfg.signal_band_hz[1],
        "noise_band_complete": recording.sfreq / 2 - 1 >= cfg.noise_band_hz[1],
        "line_measurable": cfg.line_hz + cfg.line_halfwidth_hz < recording.sfreq / 2,
    }
    return report
