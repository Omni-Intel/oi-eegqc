"""Desktop boundary: explicit array metadata and default adaptive scoring."""
from pathlib import Path
import json

import numpy as np

from .config import default_config
from .io.array import load_npy
from .io.edf import load_edf_bdf
from .pipeline import evaluate_recording


def npy_metadata(path):
    """Optional same-stem JSON; never infer a sampling rate from array length."""
    sidecar = Path(path).with_suffix(".json")
    if not sidecar.is_file():
        return {}
    raw = json.loads(sidecar.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("采样参数文件必须是对象。")
    result = {}
    rates = [raw[k] for k in ("sfreq", "sampling_rate", "SamplingFrequency") if k in raw]
    if rates:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or v <= 0 for v in rates):
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
    return result


def score_file(path, sfreq=None, unit="uV", channels_first=True):
    path = Path(path)
    if not path.is_file():
        raise ValueError("文件不存在，请重新选择。")
    if path.suffix.lower() == ".npy":
        if sfreq is None or not np.isfinite(sfreq) or sfreq <= 0:
            raise ValueError("请输入有效采样率。")
        recording = load_npy(path, sfreq, unit=unit, channels_first=channels_first)
    elif path.suffix.lower() in {".edf", ".edf+", ".bdf"}:
        recording = load_edf_bdf(path)
    else:
        raise ValueError("请选择脑电或数组文件。")
    if recording.data.ndim != 2 or min(recording.data.shape) == 0:
        raise ValueError("文件没有有效的脑电数据。")
    if not np.isfinite(recording.sfreq) or recording.sfreq <= 0:
        raise ValueError("文件采样率无效。")
    if recording.data.shape[1] < max(16, int(recording.sfreq)):
        raise ValueError("记录过短，至少需要 1 秒、16 个采样点。")
    report = evaluate_recording(recording, default_config())
    report.extras["adaptive"] = True
    report.extras["sfreq_hz"] = float(recording.sfreq)
    cfg = default_config()
    report.extras["frequency_coverage"] = {
        "signal_band_complete": recording.sfreq / 2 - 1 >= cfg.signal_band_hz[1],
        "noise_band_complete": recording.sfreq / 2 - 1 >= cfg.noise_band_hz[1],
        "line_measurable": cfg.line_hz + cfg.line_halfwidth_hz < recording.sfreq / 2,
    }
    return report
