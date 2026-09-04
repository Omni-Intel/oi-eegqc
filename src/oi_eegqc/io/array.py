from __future__ import annotations

from pathlib import Path

import numpy as np

from ..types import RecordingInput

_CHANNEL_DIM_CAP = 512
_INPUT_FIELDS = {
    "adc_to_uv",
    "aux_ch_names",
    "clip_id",
    "duration_s",
    "event_ok",
    "expected_n_channels",
    "impedance_kohm",
    "session_id",
    "stimulus_duration_s",
    "subject_id",
    "sync_error_ms",
}


def orient_channels_first(
    arr: np.ndarray,
    n_channels: int | None = None,
    *,
    channels_first: bool | None = None,
) -> np.ndarray:
    """Return ``(n_channels, n_times)``, transposing when the layout is obvious.

    Pass ``channels_first`` to skip the heuristic. When channel names are known,
    ``n_channels`` is the safer hint: the matching axis is treated as channels.
    """
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D array (channels, times), got {arr.shape}")
    if channels_first is True:
        return arr
    if channels_first is False:
        return arr.T
    if n_channels is not None:
        if arr.shape[0] == n_channels:
            return arr
        if arr.shape[1] == n_channels:
            return arr.T
        raise ValueError(
            f"Neither axis matches n_channels={n_channels} (shape {arr.shape})"
        )
    if arr.shape[0] <= arr.shape[1]:
        return arr
    if arr.shape[0] <= _CHANNEL_DIM_CAP and arr.shape[1] > 1000:
        return arr
    return arr.T


def load_channel_names(path: str | Path | None, n_channels: int) -> list[str]:
    if path is None:
        return [f"ch{i:03d}" for i in range(n_channels)]
    path = Path(path)
    if path.suffix == ".npy":
        names = np.load(path, allow_pickle=True).astype(str).tolist()
        return [str(x) for x in names][:n_channels]
    text = path.read_text(encoding="utf-8").strip().splitlines()
    return [line.strip() for line in text if line.strip()][:n_channels]


def split_recording_kwargs(kwargs: dict) -> tuple[dict, dict]:
    """Split kwargs into RecordingInput fields vs extras destined for ``meta``."""
    fields = {k: kwargs[k] for k in list(kwargs) if k in _INPUT_FIELDS}
    extras = {k: v for k, v in kwargs.items() if k not in _INPUT_FIELDS and k != "meta"}
    nested = dict(kwargs.get("meta") or {})
    nested.update(extras)
    return fields, nested


def load_npy(
    path: str | Path,
    sfreq: float,
    *,
    ch_names: list[str] | None = None,
    ch_names_path: str | Path | None = None,
    channels_first: bool | None = None,
    unit: str = "uV",
    **kwargs,
) -> RecordingInput:
    """Load a 2D ``.npy`` array as a :class:`RecordingInput`."""
    path = Path(path)
    arr = np.asarray(np.load(path), dtype=float)
    n_hint = len(ch_names) if ch_names is not None else None
    arr = orient_channels_first(arr, n_hint, channels_first=channels_first)
    if ch_names is None:
        ch_names = load_channel_names(ch_names_path, arr.shape[0])
    if len(ch_names) != arr.shape[0]:
        raise ValueError(
            f"ch_names length {len(ch_names)} does not match {arr.shape[0]} channels"
        )
    fields, extras = split_recording_kwargs(kwargs)
    fields.setdefault("clip_id", path.stem)
    extras.setdefault("source_path", str(path))
    return RecordingInput(
        data=arr,
        sfreq=float(sfreq),
        ch_names=list(ch_names),
        unit=unit,
        meta=extras,
        **fields,
    )
