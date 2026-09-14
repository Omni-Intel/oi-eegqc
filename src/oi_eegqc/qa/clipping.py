"""Window-local evidence of flat-topped signals, before filtering.

The device's ADC limits are not supplied by the supported input contract.
These are suspected clipping plateaus, not a measurement of hardware rails.
Ordinary samples near a smooth peak are insufficient evidence.
"""
from __future__ import annotations

import numpy as np


def plateau_fractions(data_uv: np.ndarray, min_samples: int, min_span_uv: float) -> np.ndarray:
    """Fraction of finite samples in consecutive, numerically equal extrema runs.

    NaNs break runs; one-sided clipping is supported. Constant/near-flat
    windows belong to the dead/flat detectors rather than this detector.
    """
    fractions = np.zeros(data_uv.shape[0], dtype=float)
    for channel, x in enumerate(data_uv):
        finite = np.isfinite(x)
        if finite.sum() < min_samples:
            continue
        lo, hi = float(x[finite].min()), float(x[finite].max())
        if hi - lo <= min_span_uv:
            continue
        tolerance = max(abs(float(np.spacing(max(abs(lo), abs(hi))))), 1e-12) * 8
        plateau_samples = 0
        for extreme in (lo, hi):
            candidate = finite & (np.abs(x - extreme) <= tolerance)
            edges = np.diff(np.r_[False, candidate, False].astype(np.int8))
            lengths = np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)
            plateau_samples += int(lengths[lengths >= min_samples].sum())
        fractions[channel] = plateau_samples / int(finite.sum())
    return fractions
