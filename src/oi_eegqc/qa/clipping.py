"""Window-local evidence of flat-topped signals, before filtering.

The device's ADC limits are not supplied by the supported input contract.
These are suspected clipping plateaus, not a measurement of hardware rails.
Ordinary samples near a smooth peak are insufficient evidence.
"""
from __future__ import annotations

import numpy as np


def plateau_fractions(data_uv: np.ndarray, min_samples: int, min_span_uv: float) -> np.ndarray:
    """Fraction of finite samples in consecutive local-extremum plateaus.

    NaNs break runs; one-sided clipping is supported. Constant/near-flat
    windows belong to the dead/flat detectors rather than this detector.
    Quantization may produce identical plateaus: this fraction is diagnostic,
    not sufficient evidence for a bad window or hardware saturation.
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
        equal = finite[:-1] & finite[1:] & (np.abs(np.diff(x)) <= tolerance)
        edges = np.diff(np.r_[False, equal, False].astype(np.int8))
        for a, stop in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            b = stop + 1
            if b - a < min_samples:
                continue
            # A plateau must be a local extremum. Missing neighbours cannot
            # establish its shape; a window boundary may have one neighbour.
            neighbours = []
            if a > 0 and finite[a - 1]:
                neighbours.append(x[a - 1])
            if b < len(x) and finite[b]:
                neighbours.append(x[b])
            if neighbours and (all(v < x[a] - tolerance for v in neighbours)
                               or all(v > x[a] + tolerance for v in neighbours)):
                plateau_samples += b - a
        fractions[channel] = plateau_samples / int(finite.sum())
    return fractions
