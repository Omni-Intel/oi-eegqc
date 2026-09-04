from __future__ import annotations

import numpy as np
from scipy.signal import welch

from ..adapters import sliding_windows
from ..config import BenchConfig, DurationProfile, MontageProfile
from ..types import WindowQASummary


def _robust_z(x: np.ndarray) -> np.ndarray:
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med)) + 1e-12
    return 0.6745 * (x - med) / mad


def _band_power(sig: np.ndarray, sfreq: float, fmin: float, fmax: float) -> float:
    nperseg = min(len(sig), max(32, int(sfreq * 2)))
    if len(sig) < 16:
        return 0.0
    freqs, psd = welch(sig, fs=sfreq, nperseg=nperseg)
    mask = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(mask):
        return 0.0
    integrate = getattr(np, "trapezoid", None) or np.trapz
    return float(integrate(psd[mask], freqs[mask]))


def _nsr(sig: np.ndarray, sfreq: float, signal_band, noise_band, nsr_threshold: float) -> bool:
    # Welch is unstable on very short windows; skip rather than false-positive.
    if len(sig) < int(0.75 * sfreq):
        return False
    p_sig = _band_power(sig, sfreq, *signal_band)
    p_noise = _band_power(sig, sfreq, *noise_band)
    if p_sig <= 0:
        return True
    ratio = p_noise / (p_sig + 1e-12)
    return ratio > nsr_threshold


def _line_noise_ratio(sig: np.ndarray, sfreq: float, line_hz: float) -> float:
    half = 1.0
    p_line = _band_power(sig, sfreq, line_hz - half, line_hz + half)
    p_sig = _band_power(sig, sfreq, 1.0, min(45.0, sfreq / 2 - 1))
    return float(p_line / (p_sig + 1e-12))


def _muscle_ratio(sig: np.ndarray, sfreq: float, muscle_band) -> float:
    p_m = _band_power(sig, sfreq, *muscle_band)
    p_sig = _band_power(sig, sfreq, 1.0, min(45.0, sfreq / 2 - 1))
    return float(p_m / (p_sig + 1e-12))


def assess_windows(
    data: np.ndarray,
    ch_names: list[str],
    sfreq: float,
    duration_profile: DurationProfile,
    montage_profile: MontageProfile,
    cfg: BenchConfig,
) -> WindowQASummary:
    """WeBrain-inspired window QA: constant, high-amp, NSR, low-corr."""
    n_ch, n_times = data.shape
    windows = sliding_windows(n_times, sfreq, duration_profile.window_s, duration_profile.hop_s)
    n_win = len(windows)

    constant = np.zeros((n_ch, n_win), dtype=bool)
    high_amp = np.zeros((n_ch, n_win), dtype=bool)
    high_nsr = np.zeros((n_ch, n_win), dtype=bool)
    low_corr = np.zeros((n_ch, n_win), dtype=bool)

    line_ratios: list[float] = []
    muscle_ratios: list[float] = []
    abs_vals: list[float] = []

    for w_i, (a, b) in enumerate(windows):
        seg = data[:, a:b]
        abs_vals.append(float(np.nanmean(np.abs(seg))))

        # Method 1: constant / non-finite
        for c in range(n_ch):
            x = seg[c]
            if (not np.isfinite(x).all()) or np.nanstd(x) < 1e-12:
                constant[c, w_i] = True

        # Method 2: unusually high amplitude via robust z of std
        stds = np.nanstd(seg, axis=1)
        z = np.abs(_robust_z(stds))
        high_amp[:, w_i] = z > montage_profile.amp_z

        # Method 3: NSR + spectral proxies (sampled on a subset for speed if dense)
        for c in range(n_ch):
            if constant[c, w_i]:
                continue
            high_nsr[c, w_i] = _nsr(
                seg[c],
                sfreq,
                cfg.signal_band_hz,
                cfg.noise_band_hz,
                montage_profile.nsr_threshold,
            )
            if c % max(1, n_ch // 8) == 0:
                line_ratios.append(_line_noise_ratio(seg[c], sfreq, cfg.line_hz))
                muscle_ratios.append(_muscle_ratio(seg[c], sfreq, cfg.muscle_band_hz))

        # Method 4: low absolute correlation with other channels
        finite_mask = ~constant[:, w_i]
        if finite_mask.sum() >= 3:
            good = seg[finite_mask]
            good = good - good.mean(axis=1, keepdims=True)
            norms = np.linalg.norm(good, axis=1) + 1e-12
            good_n = good / norms[:, None]
            # pairwise mean |corr| against other channels
            corr_mat = np.abs(good_n @ good_n.T)
            np.fill_diagonal(corr_mat, np.nan)
            mean_abs_corr = np.nanmean(corr_mat, axis=1)
            idxs = np.where(finite_mask)[0]
            for local_i, c in enumerate(idxs):
                if mean_abs_corr[local_i] < montage_profile.corr_threshold:
                    low_corr[c, w_i] = True

    bad_any = constant | high_amp | high_nsr | low_corr
    # Channel-level: broken if bad in too many windows
    broken_frac = bad_any.mean(axis=1)
    bad_ch_mask = broken_frac >= montage_profile.bad_channel_broken_frac
    bad_channels = [ch_names[i] for i, flag in enumerate(bad_ch_mask) if flag]

    # ODQ: fraction of channel-windows that are good
    odq = 100.0 * (1.0 - float(bad_any.mean()))
    usable_from_windows = float((~bad_any).mean(axis=0).mean())  # kept for callers via odq

    return WindowQASummary(
        n_windows=n_win,
        n_channels=n_ch,
        bad_window_ratio=float(bad_any.mean()),
        constant_ratio=float(constant.mean()),
        high_amp_ratio=float(high_amp.mean()),
        high_nsr_ratio=float(high_nsr.mean()),
        low_corr_ratio=float(low_corr.mean()),
        odq=float(odq),
        bad_channel_pct=100.0 * float(bad_ch_mask.mean()),
        bad_channels=bad_channels,
        mean_abs_uv=float(np.mean(abs_vals)) if abs_vals else 0.0,
        line_noise_ratio=float(np.mean(line_ratios)) if line_ratios else 0.0,
        muscle_band_ratio=float(np.mean(muscle_ratios)) if muscle_ratios else 0.0,
    )
