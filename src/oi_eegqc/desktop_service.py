"""Desktop boundary: explicit array metadata and default adaptive scoring."""
from pathlib import Path

import numpy as np

from .config import default_config
from .io.array import load_npy
from .io.edf import load_edf_bdf
from .pipeline import evaluate_recording


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
    return report
