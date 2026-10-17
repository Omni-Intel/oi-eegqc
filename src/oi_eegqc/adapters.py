from __future__ import annotations

import re

import numpy as np
from scipy.signal import butter, sosfiltfilt


def is_aux_channel_name(name: str, aux_names=()) -> bool:
    """Return whether a channel label identifies an auxiliary signal.

    EDF labels often include a signal type and a site, e.g. ``EOG IO``. Treat
    those as auxiliary even when a BIDS channels.tsv mistakenly labels them EEG.
    """
    normalized = re.sub(r"[_-]+", " ", str(name)).strip().upper()
    if normalized.startswith("EEG "):
        normalized = normalized[4:].strip()
    for alias in aux_names or ():
        token = re.sub(r"[_-]+", " ", str(alias)).strip().upper()
        if not token:
            continue
        if normalized == token or re.match(rf"^{re.escape(token)}(?:\s|\d|$)", normalized):
            return True
    return False


def pick_eeg_channels(
    data: np.ndarray,
    ch_names: list[str],
    aux_names: tuple[str, ...] | list[str] | None = None,
) -> tuple[np.ndarray, list[str], list[str]]:
    """Drop auxiliary channels only, and report what was dropped.

    Flat / zero-variance channels are deliberately kept. Removing them would
    take the most severe acquisition failure out of the denominator, so a
    recording with a quarter of its electrodes dead could score a perfect ODQ.
    They stay in and are penalised by the flat-amplitude gate instead.
    """
    if data.ndim != 2:
        raise ValueError(f"Expected data shape (n_channels, n_times), got {data.shape}")
    if data.shape[0] != len(ch_names):
        raise ValueError("ch_names length must match data.shape[0]")

    keep_idx: list[int] = []
    keep_names: list[str] = []
    dropped: list[str] = []
    for i, name in enumerate(ch_names):
        if is_aux_channel_name(name, aux_names):
            dropped.append(name)
            continue
        # Missing EEG stays in the denominator, just like flat EEG.
        keep_idx.append(i)
        keep_names.append(name)

    if not keep_idx:
        raise ValueError("No usable EEG channels after excluding aux channels.")
    return data[np.asarray(keep_idx)], keep_names, dropped




def _highpass_single_pole(x: np.ndarray, sfreq: float, cutoff_hz: float) -> np.ndarray:
    """Fallback one-pole high-pass for segments too short to filtfilt."""
    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    dt = 1.0 / sfreq
    alpha = rc / (rc + dt)
    y = np.empty_like(x, dtype=float)
    y[0] = 0.0
    for i in range(1, x.shape[0]):
        y[i] = alpha * (y[i - 1] + x[i] - x[i - 1])
    return y


def highpass_channels(
    data: np.ndarray,
    sfreq: float,
    cutoff_hz: float = 1.0,
    order: int = 4,
) -> np.ndarray:
    """Zero-phase Butterworth high-pass across all channels at once."""
    if cutoff_hz <= 0:
        return np.asarray(data, dtype=float).copy()
    x = np.asarray(data, dtype=float)
    nyq = 0.5 * sfreq
    wn = min(cutoff_hz / nyq, 0.99)
    sos = butter(order, wn, btype="highpass", output="sos")
    # sosfiltfilt needs a few times the filter length of padding.
    padlen = 3 * (2 * order + 1)
    if x.shape[-1] <= padlen + 1:
        out = np.empty_like(x)
        for i in range(x.shape[0]):
            out[i] = _highpass_single_pole(x[i], sfreq, cutoff_hz)
        return out
    finite = np.isfinite(x)
    if not finite.all():
        # sosfiltfilt propagates NaN across the whole channel; neutralise the
        # gaps first and restore them afterwards so detectors still see them.
        x = np.where(finite, x, 0.0)
        out = sosfiltfilt(sos, x, axis=-1)
        return np.where(finite, out, np.nan)
    return sosfiltfilt(sos, x, axis=-1)


def highpass_1d(x: np.ndarray, sfreq: float, cutoff_hz: float = 1.0) -> np.ndarray:
    """Single-channel convenience wrapper around :func:`highpass_channels`."""
    return highpass_channels(np.asarray(x, dtype=float)[None, :], sfreq, cutoff_hz)[0]


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
