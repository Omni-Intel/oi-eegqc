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


def _read_json_object(path, label="采样参数文件"):
    with Path(path).open("r", encoding="utf-8-sig") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError(f"{label}过大。")
    raw = json.loads(content)
    if not isinstance(raw, dict):
        raise ValueError(f"{label}必须是对象。")
    return raw


def _parse_sidecar_fields(raw):
    """Shared field parser for same-stem sidecars and session metadata.json."""
    result = {}
    rates = [
        raw[k]
        for k in ("sfreq", "sampling_rate", "SamplingFrequency", "expected_sampling_rate_hz")
        if k in raw
    ]
    if rates:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in rates):
            raise ValueError("采样参数文件中的采样率无效。")
        if len(set(float(v) for v in rates)) != 1:
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
    if "channel_layout" in raw:
        layout = raw["channel_layout"]
        if not isinstance(layout, str) or not layout.strip():
            raise ValueError("采样参数文件中的通道布局无效。")
        result["channel_layout"] = layout.strip()
    if "channel_names" in raw:
        names = raw["channel_names"]
        if not isinstance(names, list) or not names or not all(isinstance(item, str) and item.strip() for item in names):
            raise ValueError("采样参数文件中的通道名无效。")
        result["channel_names"] = [item.strip() for item in names]
    if "device_type" in raw:
        device = str(raw["device_type"]).strip()
        if not device:
            raise ValueError("采样参数文件中的设备类型无效。")
        result["device_type"] = device
    if "expected_n_channels" in raw:
        count = raw["expected_n_channels"]
        if isinstance(count, bool) or not isinstance(count, (int, float)) or not math.isfinite(count) or int(count) <= 0:
            raise ValueError("采样参数文件中的通道数无效。")
        result["expected_n_channels"] = int(count)
    elif "n_channels" in raw:
        count = raw["n_channels"]
        if isinstance(count, bool) or not isinstance(count, (int, float)) or not math.isfinite(count) or int(count) <= 0:
            raise ValueError("采样参数文件中的通道数无效。")
        result["expected_n_channels"] = int(count)
    return result


def session_stamp_metadata(path):
    """BrainCo-style stamp folder: metadata.json next to continuous_eeg.npy.

    Only applies when this array is the session eeg file. Never infers sampling
    rate from array length. Missing unit on a recognised session stamp is µV.
    """
    path = Path(path)
    meta_path = path.parent / "metadata.json"
    if not meta_path.is_file():
        return {}
    raw = _read_json_object(meta_path)
    eeg_file = Path(str(raw.get("eeg_file") or "continuous_eeg.npy")).name
    if path.name != eeg_file:
        return {}
    session_like = bool(
        (path.parent / "events.json").is_file()
        or raw.get("device_type")
        or raw.get("task_mode")
        or raw.get("eeg_file")
    )
    if not session_like:
        return {}
    parsed = _parse_sidecar_fields(raw)
    if "unit" not in parsed:
        parsed["unit"] = "uV"
    return parsed


def npy_metadata(path):
    """Same-stem JSON, then recognised session metadata.json. Sidecar wins."""
    path = Path(path)
    sidecar = {}
    stem = path.with_suffix(".json")
    if stem.is_file():
        sidecar = _parse_sidecar_fields(_read_json_object(stem))
    session = session_stamp_metadata(path)
    if not session:
        return sidecar
    merged = dict(session)
    merged.update(sidecar)
    return merged


def edf_channel_labels(path):
    """Read EDF/BDF labels from the header without NumPy or MNE."""
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(256)
        if len(header) < 256:
            return []
        try:
            n_signals = int(header[252:256].decode("ascii").strip())
        except ValueError:
            return []
        if n_signals <= 0:
            return []
        raw = stream.read(n_signals * 16)
    if len(raw) != n_signals * 16:
        return []
    return [raw[i * 16:(i + 1) * 16].decode("ascii", "replace").strip() for i in range(n_signals)]


def inspect_channel_names(path, extra=None):
    """Names the scorer will use. Does not infer a montage from array length alone."""
    extra = dict(extra or {})
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in EDF_SUFFIXES:
        labels = edf_channel_labels(path)
        if not labels:
            raise ValueError("无法读取通道名。")
        return labels
    if suffix != ".npy":
        raise ValueError("请选择脑电或数组文件。")
    declared = npy_metadata(path)
    names = extra.get("channel_names") or declared.get("channel_names")
    layout_id = extra.get("channel_layout") or declared.get("channel_layout")
    device_type = extra.get("device_type") or declared.get("device_type")
    channels_first = extra.get("channels_first", declared.get("channels_first"))
    import numpy as np
    from .io.array import load_channel_names, orient_channels_first

    array = np.load(path, mmap_mode="r")
    hint = len(names) if names is not None else None
    n_ch = int(orient_channels_first(array, hint, channels_first=channels_first).shape[0])
    if names is not None:
        if len(names) != n_ch:
            raise ValueError(f"通道名数量 {len(names)} 与 {n_ch} 路数据不一致。")
        return [str(item) for item in names]
    if layout_id or device_type:
        from .layouts import resolve_channel_names

        resolved, _ = resolve_channel_names(n_ch, layout_id=layout_id, device_type=device_type)
        return resolved
    return load_channel_names(None, n_ch)


def apply_keep_channels(recording, keep_channels):
    """Keep the named subset in file order. Unselected channels are not scored."""
    import numpy as np

    wanted = [str(name).strip() for name in keep_channels or [] if str(name).strip()]
    if not wanted:
        raise ValueError("请至少选择一个通道。")
    chosen = set(wanted)
    keep = [index for index, name in enumerate(recording.ch_names) if name in chosen]
    if not keep:
        raise ValueError("当前文件没有已选通道。")
    recording.data = np.asarray(recording.data)[keep]
    recording.ch_names = [recording.ch_names[index] for index in keep]
    recording.expected_n_channels = len(keep)
    return recording


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
    for key in ("channel_layout", "channel_names", "device_type"):
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
    keep_channels=None,
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
    if keep_channels is None:
        keep_channels = extra.pop("keep_channels", None)
    extras = {key: extra[key] for key in _OPTIONAL_INPUT if key in extra}
    suffix = path.suffix.lower()
    if suffix == ".npy":
        sfreq, unit, channels_first, extras = merge_npy_kwargs(
            path, sfreq=sfreq, unit=unit, channels_first=channels_first, extra=extras
        )
        if sfreq is None or not np.isfinite(sfreq) or sfreq <= 0:
            raise ValueError("请输入有效采样率。")
        channel_names = extras.pop("channel_names", None)
        channel_layout = extras.pop("channel_layout", None)
        device_type = extras.pop("device_type", None)
        recording = load_npy(
            path,
            sfreq,
            unit=unit or "uV",
            channels_first=channels_first,
            ch_names=channel_names,
            ch_names_path=ch_names_path,
            channel_layout=channel_layout,
            device_type=device_type,
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
    if keep_channels is not None:
        recording = apply_keep_channels(recording, keep_channels)
    progress("评分")
    report = evaluate_recording(recording, cfg)
    report.extras["adaptive"] = True
    report.extras["sfreq_hz"] = float(recording.sfreq)
    report.extras["line_hz"] = float(cfg.line_hz)
    if recording.meta.get("channel_layout"):
        report.extras["channel_layout"] = recording.meta["channel_layout"]
    report.extras["frequency_coverage"] = {
        "signal_band_complete": recording.sfreq / 2 - 1 >= cfg.signal_band_hz[1],
        "noise_band_complete": recording.sfreq / 2 - 1 >= cfg.noise_band_hz[1],
        "line_measurable": cfg.line_hz + cfg.line_halfwidth_hz < recording.sfreq / 2,
    }
    from .scoring.explain import build_operator

    report.extras["operator"] = build_operator(report)
    return report
