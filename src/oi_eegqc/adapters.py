from __future__ import annotations

import numpy as np


def pick_eeg_channels(
    data: np.ndarray,
    ch_names: list[str],
    aux_names: tuple[str, ...] | list[str] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Drop auxiliary / flat channels before QA."""
    if data.ndim != 2:
        raise ValueError(f"Expected data shape (n_channels, n_times), got {data.shape}")
    if data.shape[0] != len(ch_names):
        raise ValueError("ch_names length must match data.shape[0]")

    aux = {n.upper() for n in (aux_names or ())}
    keep_idx: list[int] = []
    keep_names: list[str] = []
    for i, name in enumerate(ch_names):
        if name.upper() in aux:
            continue
        if not np.isfinite(data[i]).any():
            continue
        if np.nanstd(data[i]) == 0:
            continue
        keep_idx.append(i)
        keep_names.append(name)

    if not keep_idx:
        raise ValueError("No usable EEG channels after excluding aux/flat channels.")
    return data[np.asarray(keep_idx)], keep_names


def highpass_1d(x: np.ndarray, sfreq: float, cutoff_hz: float = 1.0) -> np.ndarray:
    """Simple single-pole IIR high-pass (no SciPy filter design dependency for core path)."""
    if cutoff_hz <= 0:
        return x.copy()
    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    dt = 1.0 / sfreq
    alpha = rc / (rc + dt)
    y = np.empty_like(x, dtype=float)
    y[0] = 0.0
    for i in range(1, x.shape[0]):
        y[i] = alpha * (y[i - 1] + x[i] - x[i - 1])
    return y


def highpass_channels(data: np.ndarray, sfreq: float, cutoff_hz: float = 1.0) -> np.ndarray:
    out = np.empty_like(data, dtype=float)
    for i in range(data.shape[0]):
        out[i] = highpass_1d(np.asarray(data[i], dtype=float), sfreq, cutoff_hz)
    return out


def sliding_windows(
    n_times: int,
    sfreq: float,
    window_s: float,
    hop_s: float,
) -> list[tuple[int, int]]:
    win = max(1, int(round(window_s * sfreq)))
    hop = max(1, int(round(hop_s * sfreq)))
    if n_times < win:
        return [(0, n_times)]
    starts = list(range(0, n_times - win + 1, hop))
    if not starts or starts[-1] + win < n_times:
        last = n_times - win
        if last >= 0 and (not starts or starts[-1] != last):
            starts.append(last)
    return [(s, s + win) for s in starts]
