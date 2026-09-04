from __future__ import annotations

import numpy as np


DEFAULT_CLIP_SECONDS: tuple[float, ...] = (6.0, 12.0, 30.0, 60.0)
DEFAULT_EPOCH_TARGETS_S: tuple[float, ...] = (6.0, 12.0, 30.0)


def centered_clips(
    data: np.ndarray,
    sfreq: float,
    *,
    targets_s: tuple[float, ...] = DEFAULT_CLIP_SECONDS,
    valid_samples: int | None = None,
    min_fraction: float = 0.8,
) -> list[tuple[str, np.ndarray]]:
    """Cut centered windows of standard durations from a continuous recording.

    Used to exercise duration-adaptive profiles without claiming the clip is
    a stimulus-aligned epoch. The clip's own length is therefore *not* a
    valid ``stimulus_duration_s``.
    """
    n_times = data.shape[-1]
    if valid_samples is not None:
        n_times = min(n_times, int(valid_samples))
        data = data[:, :n_times]
    duration_s = n_times / float(sfreq)
    clips: list[tuple[str, np.ndarray]] = []
    for target in targets_s:
        if duration_s < target * min_fraction:
            continue
        n_samp = int(round(target * sfreq))
        start = max(0, (n_times - n_samp) // 2)
        clips.append((f"{target:.0f}s", data[:, start : start + n_samp]))
    if not clips and n_times > int(0.5 * sfreq):
        clips.append((f"full_{duration_s:.1f}s", data))
    return clips


def concat_epochs(epochs: np.ndarray, start: int, n_epochs: int) -> np.ndarray:
    """``(n_epochs_total, n_ch, n_times)`` -> ``(n_ch, n_times * n_epochs)``."""
    chunk = epochs[start : start + n_epochs]
    if chunk.shape[0] < n_epochs:
        raise ValueError(f"need {n_epochs} epochs from index {start}, have {chunk.shape[0]}")
    return np.concatenate([ep for ep in chunk], axis=-1)


def epoch_duration_plan(
    n_times: int,
    sfreq: float,
    *,
    targets_s: tuple[float, ...] = DEFAULT_EPOCH_TARGETS_S,
    include_single: bool = True,
) -> list[tuple[str, int]]:
    """How many consecutive epochs to concat to hit each duration profile."""
    epoch_s = n_times / float(sfreq)
    plans: list[tuple[str, int]] = []
    if include_single:
        plans.append(("1ep_ultra_short", 1))
    for target in targets_s:
        n_need = max(1, int(round(target / epoch_s)))
        label = f"~{target:.0f}s"
        if (label, n_need) not in plans:
            plans.append((label, n_need))
    return plans
