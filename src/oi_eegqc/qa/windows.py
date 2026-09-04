from __future__ import annotations

import numpy as np
from scipy.signal import welch

from ..adapters import sliding_windows
from ..config import BenchConfig, DurationProfile, MontageProfile
from ..types import WindowQASummary

#: Minimum sample count before a median/MAD statistic is trusted. The MAD has a
#: 50% breakdown point, and with only a handful of values it collapses to zero
#: and flags everything. Below this count the relative detectors stay silent and
#: the absolute amplitude gates carry the load.
MIN_ROBUST_N = 8

#: MAD floor as a fraction of the median, guarding against divide-by-zero blow-up
#: when many windows or channels share an almost identical spread.
MAD_FLOOR_REL = 0.05


def _robust_z(x: np.ndarray) -> np.ndarray:
    med = np.nanmedian(x)
    mad = float(np.nanmedian(np.abs(x - med)))
    mad = max(mad, MAD_FLOOR_REL * abs(float(med)) + 1e-12)
    return 0.6745 * (x - med) / mad


def _integrate(psd: np.ndarray, freqs: np.ndarray, fmin: float, fmax: float) -> float:
    mask = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(mask):
        return 0.0
    integrate = getattr(np, "trapezoid", None) or np.trapz
    return float(integrate(psd[mask], freqs[mask]))


def _cell_spectrum(sig: np.ndarray, sfreq: float) -> tuple[np.ndarray, np.ndarray] | None:
    """One Welch estimate per channel x window, reused by every band metric."""
    if len(sig) < max(16, int(0.75 * sfreq)):
        # Welch is unstable on very short windows; the amplitude gates still run.
        return None
    nperseg = min(len(sig), max(32, int(sfreq * 2)))
    freqs, psd = welch(sig, fs=sfreq, nperseg=nperseg)
    return freqs, psd


def assess_windows(
    data_uv: np.ndarray,
    ch_names: list[str],
    sfreq: float,
    duration_profile: DurationProfile,
    montage_profile: MontageProfile,
    cfg: BenchConfig,
    clipped_idx: list[int] | None = None,
) -> WindowQASummary:
    """Window QA with absolute amplitude gates plus relative outlier detection.

    ``data_uv`` must already be high-passed and expressed in microvolts, since
    the flat and saturation gates compare against physical thresholds.

    Detectors marking a channel x window cell as bad:

    ``constant``          non-finite samples or zero variance
    ``flat``              peak-to-peak below ``ptp_min_uv`` (dead lead)
    ``extreme_amp``       peak-to-peak above ``ptp_max_uv`` (saturation, movement)
    ``temporal_outlier``  spread far from the channel's own median spread
    ``spatial_outlier``   spread far above the other channels in the same window
    ``high_nsr``          broadband HF-to-signal power above threshold
    ``line_noise``        mains-band power fraction above threshold
    ``low_corr``          weak spatial coupling to the best-matching channels

    Alongside the binary flags the raw NSR and mains ratios are retained as
    continuous arrays. Binary flags alone make the score a step function, so
    scoring blends the flag density with these continuous measures.
    """
    n_ch, n_times = data_uv.shape
    windows = sliding_windows(n_times, sfreq, duration_profile.window_s, duration_profile.hop_s)
    n_win = len(windows)

    constant = np.zeros((n_ch, n_win), dtype=bool)
    flat = np.zeros((n_ch, n_win), dtype=bool)
    extreme_amp = np.zeros((n_ch, n_win), dtype=bool)
    temporal_outlier = np.zeros((n_ch, n_win), dtype=bool)
    spatial_outlier = np.zeros((n_ch, n_win), dtype=bool)
    high_nsr = np.zeros((n_ch, n_win), dtype=bool)
    line_noise = np.zeros((n_ch, n_win), dtype=bool)
    low_corr = np.zeros((n_ch, n_win), dtype=bool)

    stds = np.zeros((n_ch, n_win), dtype=float)
    ptps = np.zeros((n_ch, n_win), dtype=float)
    nsr_vals = np.full((n_ch, n_win), np.nan, dtype=float)
    line_vals = np.full((n_ch, n_win), np.nan, dtype=float)
    muscle_vals = np.full((n_ch, n_win), np.nan, dtype=float)

    noise_band = cfg.resolved_noise_band(sfreq)
    sig_lo, sig_hi = cfg.signal_band_hz
    sig_hi = min(sig_hi, 0.5 * sfreq - 1.0)
    line_hz = cfg.line_hz
    line_measurable = line_hz + cfg.line_halfwidth_hz < 0.5 * sfreq

    abs_means: list[float] = []
    abs_p99: list[float] = []
    abs_max: list[float] = []

    for w_i, (a, b) in enumerate(windows):
        seg = data_uv[:, a:b]
        finite = np.isfinite(seg)
        abs_seg = np.abs(seg[finite]) if finite.any() else np.zeros(1)
        abs_means.append(float(abs_seg.mean()))
        abs_p99.append(float(np.percentile(abs_seg, 99)))
        abs_max.append(float(abs_seg.max()))

        with np.errstate(invalid="ignore"):
            stds[:, w_i] = np.nanstd(seg, axis=1)
            ptps[:, w_i] = np.nanmax(seg, axis=1) - np.nanmin(seg, axis=1)
        stds[:, w_i] = np.nan_to_num(stds[:, w_i], nan=0.0)
        ptps[:, w_i] = np.nan_to_num(ptps[:, w_i], nan=0.0)

        # Detector 1: non-finite or numerically constant.
        constant[:, w_i] = (~finite.all(axis=1)) | (stds[:, w_i] < 1e-12)

        # Detectors 2 and 3: absolute amplitude gates in microvolts.
        flat[:, w_i] = ptps[:, w_i] < montage_profile.ptp_min_uv
        extreme_amp[:, w_i] = ptps[:, w_i] > montage_profile.ptp_max_uv

        # Detector 5: spatial amplitude outliers, high side only. The low side
        # is already covered by the flat gate, and including it would make the
        # statistic collapse when half the montage is dead.
        if n_ch >= MIN_ROBUST_N:
            alive = ~(constant[:, w_i] | flat[:, w_i])
            if alive.sum() >= MIN_ROBUST_N:
                z = np.zeros(n_ch)
                z[alive] = _robust_z(stds[alive, w_i])
                spatial_outlier[:, w_i] = alive & (z > montage_profile.amp_z)

        # Detectors 6 and 7: spectral ratios, one Welch estimate per cell.
        for c in range(n_ch):
            if constant[c, w_i]:
                continue
            spec = _cell_spectrum(np.nan_to_num(seg[c], nan=0.0), sfreq)
            if spec is None:
                continue
            freqs, psd = spec
            p_sig = _integrate(psd, freqs, sig_lo, sig_hi)
            if p_sig <= 0:
                nsr_vals[c, w_i] = np.inf
                high_nsr[c, w_i] = True
                continue
            if noise_band is not None:
                nsr = _integrate(psd, freqs, *noise_band) / p_sig
                nsr_vals[c, w_i] = nsr
                high_nsr[c, w_i] = nsr > montage_profile.nsr_threshold
            if line_measurable:
                ratio = (
                    _integrate(
                        psd,
                        freqs,
                        line_hz - cfg.line_halfwidth_hz,
                        line_hz + cfg.line_halfwidth_hz,
                    )
                    / p_sig
                )
                line_vals[c, w_i] = ratio
                line_noise[c, w_i] = ratio > montage_profile.line_ratio_threshold
            muscle_vals[c, w_i] = (
                _integrate(psd, freqs, *cfg.muscle_band_hz) / p_sig
            )

        # Detector 8: low spatial coupling. After common-average or ICA
        # cleaning the mean pairwise correlation collapses, so score each
        # channel by its strongest couplings rather than its average one.
        usable_mask = ~(constant[:, w_i] | flat[:, w_i])
        if montage_profile.corr_detector_enabled and usable_mask.sum() >= 3:
            good = np.nan_to_num(seg[usable_mask], nan=0.0)
            good = good - good.mean(axis=1, keepdims=True)
            norms = np.linalg.norm(good, axis=1) + 1e-12
            good_n = good / norms[:, None]
            corr_mat = np.abs(good_n @ good_n.T)
            np.fill_diagonal(corr_mat, np.nan)
            k = min(3, int(usable_mask.sum()) - 1)
            idxs = np.where(usable_mask)[0]
            for local_i, c in enumerate(idxs):
                row = corr_mat[local_i]
                row = row[~np.isnan(row)]
                if row.size == 0:
                    continue
                if float(np.mean(np.sort(row)[-k:])) < montage_profile.corr_threshold:
                    low_corr[c, w_i] = True

    # Detector 4: temporal amplitude outliers, judged against each channel's
    # own baseline across windows rather than against its neighbours.
    if n_win >= MIN_ROBUST_N:
        for c in range(n_ch):
            if np.nanmedian(stds[c]) <= 0:
                continue
            temporal_outlier[c] = np.abs(_robust_z(stds[c])) > montage_profile.amp_z

    bad_any = (
        constant
        | flat
        | extreme_amp
        | temporal_outlier
        | spatial_outlier
        | high_nsr
        | line_noise
        | low_corr
    )

    # Rail-clipped channels are unusable for their whole span.
    clipped_idx = list(clipped_idx or [])
    for c in clipped_idx:
        if 0 <= c < n_ch:
            bad_any[c, :] = True

    # Contamination density over channel x window cells.
    bad_cell_ratio = float(bad_any.mean())

    # Surviving recording time: a window passes while enough channels are good.
    bad_frac_per_window = bad_any.mean(axis=0)
    usable_window_ratio = float(
        (bad_frac_per_window <= montage_profile.max_bad_ch_frac_per_window).mean()
    )

    broken_frac = bad_any.mean(axis=1)
    bad_ch_mask = broken_frac >= montage_profile.bad_channel_broken_frac
    bad_channels = [ch_names[i] for i, flag in enumerate(bad_ch_mask) if flag]
    dead_channels = [
        ch_names[i]
        for i in range(n_ch)
        if (constant[i] | flat[i]).mean() >= montage_profile.bad_channel_broken_frac
    ]

    def _nanmedian(arr: np.ndarray) -> float:
        finite = arr[np.isfinite(arr)]
        return float(np.median(finite)) if finite.size else 0.0

    return WindowQASummary(
        n_windows=n_win,
        n_channels=n_ch,
        clean_ratio=1.0 - bad_cell_ratio,
        bad_cell_ratio=bad_cell_ratio,
        usable_window_ratio=usable_window_ratio,
        odq=100.0 * usable_window_ratio,
        constant_ratio=float(constant.mean()),
        flat_ratio=float(flat.mean()),
        extreme_amp_ratio=float(extreme_amp.mean()),
        temporal_outlier_ratio=float(temporal_outlier.mean()),
        spatial_outlier_ratio=float(spatial_outlier.mean()),
        high_nsr_ratio=float(high_nsr.mean()),
        line_noise_flag_ratio=float(line_noise.mean()),
        low_corr_ratio=float(low_corr.mean()),
        nsr_median=_nanmedian(nsr_vals),
        nsr_p90=float(np.percentile(nsr_vals[np.isfinite(nsr_vals)], 90))
        if np.isfinite(nsr_vals).any()
        else 0.0,
        bad_channel_pct=100.0 * float(bad_ch_mask.mean()),
        bad_channels=bad_channels,
        clipped_channels=[ch_names[c] for c in clipped_idx if 0 <= c < n_ch],
        dead_channels=dead_channels,
        mean_abs_uv=float(np.mean(abs_means)) if abs_means else 0.0,
        p99_abs_uv=float(np.max(abs_p99)) if abs_p99 else 0.0,
        max_abs_uv=float(np.max(abs_max)) if abs_max else 0.0,
        line_noise_ratio=_nanmedian(line_vals),
        muscle_band_ratio=_nanmedian(muscle_vals),
    )
