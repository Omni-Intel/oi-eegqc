from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfiltfilt


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

    aux = {n.upper() for n in (aux_names or ())}
    keep_idx: list[int] = []
    keep_names: list[str] = []
    dropped: list[str] = []
    for i, name in enumerate(ch_names):
        if name.upper() in aux:
            dropped.append(name)
            continue
        if not np.isfinite(data[i]).any():
            # Fully non-finite channels carry no signal to score; they are
            # reported so integrity can react to the missing channel count.
            dropped.append(name)
            continue
        keep_idx.append(i)
        keep_names.append(name)

    if not keep_idx:
        raise ValueError("No usable EEG channels after excluding aux channels.")
    return data[np.asarray(keep_idx)], keep_names, dropped


def detect_clipped_channels(
    data_uv: np.ndarray,
    frac_threshold: float,
) -> list[int]:
    """Indices of channels whose samples pile up on their own extreme rail.

    Run this on the unfiltered microvolt signal, because high-pass filtering
    smears the flat top of a saturated segment and hides the rail. The DC
    offset is removed first: DC-coupled amplifiers sit several millivolts away
    from zero, which would otherwise place every sample near ``max|x|`` and
    flag healthy channels as clipped.
    """
    clipped: list[int] = []
    for i in range(data_uv.shape[0]):
        x = np.asarray(data_uv[i], dtype=float)
        x = x[np.isfinite(x)]
        if x.size == 0:
            continue
        centred = np.abs(x - np.median(x))
        peak = float(centred.max())
        if peak <= 0:
            continue
        at_rail = float(np.mean(centred >= 0.999 * peak))
        if at_rail > frac_threshold:
            clipped.append(i)
    return clipped


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
